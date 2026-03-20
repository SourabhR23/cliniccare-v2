"""
backend/api/routes/pdf.py

PDF EXPORT ENDPOINTS (Doctor-only)

GET  /pdf/patient/{patient_id}        → full patient history (info + all visits)
GET  /pdf/visit/{visit_id}            → single visit record
POST /pdf/patient/{patient_id}/email  → generate + email full history to patient
POST /pdf/visit/{visit_id}/email      → generate + email single visit to patient

GET  endpoints: return application/pdf with Content-Disposition: attachment.
POST endpoints: send PDF as email attachment, return JSON confirmation.

Uses reportlab for pure-Python PDF generation — no wkhtmltopdf needed.
Uses aiosmtplib (same SMTP config as NotificationAgent) for email delivery.
"""

import io
from datetime import datetime
from typing import Optional

import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.db.mongodb.connection import get_db
from backend.api.middleware.auth_middleware import require_doctor
from backend.models.patient import TokenData
from backend.services.patient.patient_service import PatientService

router = APIRouter(prefix="/pdf", tags=["PDF Export"])


# ─────────────────────────────────────────────────────────────
# PDF BUILDER HELPERS (reportlab)
# ─────────────────────────────────────────────────────────────

def _build_patient_pdf(patient_data: dict, visits: list) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"], fontSize=16,
                                 spaceAfter=6, textColor=colors.HexColor("#1a56db"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12,
                        spaceBefore=12, spaceAfter=4, textColor=colors.HexColor("#374151"))
    body = styles["Normal"]
    body.fontSize = 10
    small = ParagraphStyle("small", parent=body, fontSize=9, textColor=colors.grey)

    TEAL = colors.HexColor("#0891b2")
    LIGHT = colors.HexColor("#f0f9ff")
    BORDER = colors.HexColor("#e2e8f0")

    story = []

    # ── Header ────────────────────────────────────────────────
    story.append(Paragraph("ClinicCare — Patient Medical Record", title_style))
    story.append(Paragraph(
        f"Generated: {datetime.utcnow().strftime('%d %B %Y, %H:%M UTC')}",
        small,
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=8))

    # ── Patient Info ──────────────────────────────────────────
    story.append(Paragraph("Patient Information", h2))
    p = patient_data
    info_rows = [
        ["Name", p.get("name", "—"), "Patient ID", p.get("id", "—")],
        ["Age / Sex", f"{p.get('age', '—')} / {p.get('sex', '—')}", "Blood Group", p.get("blood_group", "—")],
        ["Phone", p.get("phone", "—"), "Email", p.get("email") or "—"],
        ["Address", p.get("address") or "—", "Registered", p.get("registered_date") or "—"],
        ["Assigned Doctor", p.get("assigned_doctor_id", "—"), "Total Visits", str(p.get("total_visits", 0))],
    ]
    allergies = ", ".join(p.get("known_allergies", [])) or "None"
    conditions = ", ".join(p.get("chronic_conditions", [])) or "None"
    info_rows += [
        ["Known Allergies", allergies, "", ""],
        ["Chronic Conditions", conditions, "", ""],
    ]

    info_table = Table(info_rows, colWidths=[4 * cm, 7.5 * cm, 3.5 * cm, 2.5 * cm])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("BACKGROUND", (2, 0), (2, -1), LIGHT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 0.5 * cm))

    # ── Visit Records ─────────────────────────────────────────
    story.append(Paragraph(f"Visit History ({len(visits)} records)", h2))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=6))

    for i, v in enumerate(visits, 1):
        story.append(Paragraph(
            f"<b>Visit {i} — {v.get('visit_date', '—')}</b>  |  ID: {v.get('_id') or v.get('id', '—')}",
            ParagraphStyle("vh", parent=body, fontSize=10, spaceBefore=8,
                           textColor=colors.HexColor("#0f172a")),
        ))
        vrows = [
            ["Chief Complaint", v.get("chief_complaint") or "—"],
            ["Diagnosis", v.get("diagnosis") or "—"],
            ["Vitals", _fmt_vitals(v)],
            ["Medications", _fmt_meds(v.get("medications", []))],
            ["Notes", v.get("notes") or "—"],
            ["Follow-up Required", "Yes" if v.get("followup_required") else "No"],
            ["Follow-up Date", v.get("followup_date") or "—"],
        ]
        vt = Table(vrows, colWidths=[4 * cm, 13.5 * cm])
        vt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), LIGHT),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ]))
        story.append(vt)

    story.append(Spacer(1, cm))
    story.append(Paragraph(
        "— End of Report — Confidential Medical Record —",
        ParagraphStyle("footer", parent=small, alignment=1),
    ))

    doc.build(story)
    return buf.getvalue()


def _build_visit_pdf(patient_data: dict, visit_data: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    TEAL = colors.HexColor("#0891b2")
    LIGHT = colors.HexColor("#f0f9ff")
    BORDER = colors.HexColor("#e2e8f0")

    title_style = ParagraphStyle("t", parent=styles["Heading1"], fontSize=15,
                                 spaceAfter=4, textColor=colors.HexColor("#1a56db"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11,
                        spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#374151"))
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=8, textColor=colors.grey)

    story = []
    v = visit_data
    p = patient_data

    story.append(Paragraph("ClinicCare — Visit Record", title_style))
    story.append(Paragraph(
        f"Visit ID: {v.get('_id') or v.get('id', '—')}  |  Date: {v.get('visit_date', '—')}  |  "
        f"Generated: {datetime.utcnow().strftime('%d %b %Y, %H:%M UTC')}",
        small,
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=TEAL, spaceAfter=8))

    # Patient summary
    story.append(Paragraph("Patient", h2))
    pt_rows = [
        ["Name", p.get("name", "—"), "ID", p.get("id", "—")],
        ["Age / Sex", f"{p.get('age', '—')} / {p.get('sex', '—')}", "Blood Group", p.get("blood_group", "—")],
    ]
    pt = Table(pt_rows, colWidths=[3.5*cm, 7.5*cm, 3*cm, 3.5*cm])
    pt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("BACKGROUND", (2, 0), (2, -1), LIGHT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    story.append(pt)

    # Visit details
    story.append(Paragraph("Clinical Details", h2))
    vrows = [
        ["Doctor", v.get("doctor_name") or v.get("doctor_id", "—")],
        ["Chief Complaint", v.get("chief_complaint") or "—"],
        ["Diagnosis", v.get("diagnosis") or "—"],
        ["Vitals", _fmt_vitals(v)],
        ["Medications", _fmt_meds(v.get("medications", []))],
        ["Notes", v.get("notes") or "—"],
        ["Follow-up Required", "Yes" if v.get("followup_required") else "No"],
        ["Follow-up Date", v.get("followup_date") or "—"],
    ]
    vt = Table(vrows, colWidths=[4*cm, 13.5*cm])
    vt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    story.append(vt)
    story.append(Spacer(1, cm))
    story.append(Paragraph(
        "— End of Visit Record — Confidential Medical Record —",
        ParagraphStyle("footer", parent=small, alignment=1),
    ))
    doc.build(story)
    return buf.getvalue()


def _fmt_vitals(v: dict) -> str:
    """
    Visits store vitals flat: bp, weight_kg at the top level of the document.
    Also handles a nested 'vitals' dict if present.
    """
    parts = []
    # Flat fields (VisitDocument schema)
    if v.get("bp"):
        parts.append(f"BP: {v['bp']}")
    if v.get("weight_kg"):
        parts.append(f"Weight: {v['weight_kg']} kg")
    # Nested vitals dict (future-proof)
    vitals = v.get("vitals") or {}
    if isinstance(vitals, dict):
        if vitals.get("blood_pressure"):
            parts.append(f"BP: {vitals['blood_pressure']}")
        if vitals.get("pulse"):
            parts.append(f"Pulse: {vitals['pulse']} bpm")
        if vitals.get("temperature"):
            parts.append(f"Temp: {vitals['temperature']}°C")
        if vitals.get("weight_kg") and not v.get("weight_kg"):
            parts.append(f"Weight: {vitals['weight_kg']} kg")
        if vitals.get("height_cm"):
            parts.append(f"Height: {vitals['height_cm']} cm")
        if vitals.get("spo2"):
            parts.append(f"SpO2: {vitals['spo2']}%")
    return "  |  ".join(parts) if parts else "—"


def _fmt_meds(medications: list) -> str:
    if not medications:
        return "None"
    lines = []
    for m in medications:
        name = m.get("name", "")
        dose = m.get("dose", "") or m.get("dosage", "")
        freq = m.get("frequency", "")
        dur = m.get("duration", "")
        parts = [x for x in [name, dose, freq, dur] if x]
        lines.append(" — ".join(parts))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.get(
    "/patient/{patient_id}",
    summary="Export full patient history as PDF",
    description="Doctor-only. Returns a PDF with patient info + all visit records.",
)
async def export_patient_pdf(
    patient_id: str,
    current_user: TokenData = Depends(require_doctor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    service = PatientService(db)
    record = await service.get_patient_with_visits(patient_id)

    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Patient {patient_id} not found")

    if record.patient.assigned_doctor_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied to this patient's records")

    patient_dict = record.patient.model_dump(mode="json")
    visits_dicts = [v.model_dump(mode="json", by_alias=True) for v in record.visits]

    try:
        pdf_bytes = _build_patient_pdf(patient_dict, visits_dicts)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"PDF generation failed: {str(e)}")

    filename = f"patient_{patient_id}_{datetime.utcnow().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/visit/{visit_id}",
    summary="Export a single visit record as PDF",
    description="Doctor-only. Returns a PDF for one visit with patient header.",
)
async def export_visit_pdf(
    visit_id: str,
    current_user: TokenData = Depends(require_doctor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    visit_doc = await db["visits"].find_one({"_id": visit_id})
    if not visit_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Visit {visit_id} not found")

    if visit_doc.get("doctor_id") != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied to this visit record")

    patient_doc = await db["patients"].find_one({"_id": visit_doc["patient_id"]})
    if not patient_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Patient record not found")

    service = PatientService(db)
    from backend.models.patient import PatientDocument
    patient = PatientDocument(**patient_doc)

    patient_dict = {
        "id": patient.id,
        "name": patient.personal.name,
        "age": patient.personal.age,
        "sex": patient.personal.sex.value,
        "blood_group": patient.personal.blood_group.value,
    }

    try:
        pdf_bytes = _build_visit_pdf(patient_dict, visit_doc)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"PDF generation failed: {str(e)}")

    filename = f"visit_{visit_id}_{datetime.utcnow().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────
# EMAIL HELPER
# ─────────────────────────────────────────────────────────────

async def _send_pdf_email(
    recipient_email: str,
    recipient_name: str,
    subject: str,
    body_text: str,
    pdf_bytes: bytes,
    filename: str,
) -> None:
    """
    Send a PDF as an email attachment using the configured SMTP settings.
    Raises an exception on failure (caller handles HTTPException).
    """
    from backend.core.config import get_settings
    settings = get_settings()

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = recipient_email

    msg.attach(MIMEText(body_text, "plain"))

    part = MIMEBase("application", "pdf")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)

    use_implicit_tls = settings.smtp_port == 465
    async with aiosmtplib.SMTP(
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        use_tls=use_implicit_tls,
        start_tls=not use_implicit_tls,
    ) as smtp:
        await smtp.login(settings.smtp_username, settings.smtp_password)
        await smtp.send_message(msg)


# ─────────────────────────────────────────────────────────────
# POST /pdf/patient/{patient_id}/email
# ─────────────────────────────────────────────────────────────

@router.post(
    "/patient/{patient_id}/email",
    summary="Email full patient history PDF to the patient",
    description="Doctor-only. Generates patient history PDF and sends it to the patient's registered email.",
    response_model=dict,
)
async def email_patient_pdf(
    patient_id: str,
    current_user: TokenData = Depends(require_doctor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    service = PatientService(db)
    record = await service.get_patient_with_visits(patient_id)

    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Patient {patient_id} not found")

    if record.patient.assigned_doctor_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied to this patient's records")

    patient_email = record.patient.email
    if not patient_email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Patient {record.patient.name} has no email address on record.",
        )

    patient_dict = record.patient.model_dump(mode="json")
    visits_dicts = [v.model_dump(mode="json", by_alias=True) for v in record.visits]

    try:
        pdf_bytes = _build_patient_pdf(patient_dict, visits_dicts)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"PDF generation failed: {str(e)}")

    # Fetch sending doctor's name
    doctor_doc = await db["users"].find_one({"_id": current_user.user_id}, {"name": 1})
    doctor_name = doctor_doc.get("name", "Your Doctor") if doctor_doc else "Your Doctor"

    filename = f"medical_history_{datetime.utcnow().strftime('%Y%m%d')}.pdf"
    subject = f"Your Medical Records — ClinicCare"
    body = (
        f"Dear {record.patient.name},\n\n"
        f"Please find attached your complete medical history from ClinicCare, "
        f"prepared by {doctor_name}.\n\n"
        f"This document contains your visit records and is strictly confidential. "
        f"Please do not forward it to unauthorised parties.\n\n"
        f"If you have any questions, contact us at the clinic.\n\n"
        f"Warm regards,\nClinicCare Team"
    )

    try:
        await _send_pdf_email(
            recipient_email=str(patient_email),
            recipient_name=record.patient.name,
            subject=subject,
            body_text=body,
            pdf_bytes=pdf_bytes,
            filename=filename,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"PDF generated but email delivery failed: {str(e)}",
        )

    return {
        "sent": True,
        "recipient": str(patient_email),
        "patient_name": record.patient.name,
        "filename": filename,
        "message": f"Medical history PDF sent to {patient_email}",
    }


# ─────────────────────────────────────────────────────────────
# POST /pdf/visit/{visit_id}/email
# ─────────────────────────────────────────────────────────────

@router.post(
    "/visit/{visit_id}/email",
    summary="Email a single visit record PDF to the patient",
    description="Doctor-only. Generates visit PDF and sends it to the patient's registered email.",
    response_model=dict,
)
async def email_visit_pdf(
    visit_id: str,
    current_user: TokenData = Depends(require_doctor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    visit_doc = await db["visits"].find_one({"_id": visit_id})
    if not visit_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Visit {visit_id} not found")

    if visit_doc.get("doctor_id") != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied to this visit record")

    patient_doc = await db["patients"].find_one({"_id": visit_doc["patient_id"]})
    if not patient_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Patient record not found")

    from backend.models.patient import PatientDocument
    patient = PatientDocument(**patient_doc)

    patient_email = patient.personal.email
    if not patient_email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Patient {patient.personal.name} has no email address on record.",
        )

    patient_dict = {
        "id": patient.id,
        "name": patient.personal.name,
        "age": patient.personal.age,
        "sex": patient.personal.sex.value,
        "blood_group": patient.personal.blood_group.value,
    }

    try:
        pdf_bytes = _build_visit_pdf(patient_dict, visit_doc)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"PDF generation failed: {str(e)}")

    doctor_doc = await db["users"].find_one({"_id": current_user.user_id}, {"name": 1})
    doctor_name = doctor_doc.get("name", "Your Doctor") if doctor_doc else "Your Doctor"

    visit_date = str(visit_doc.get("visit_date", ""))[:10]
    filename = f"visit_{visit_date}.pdf"
    subject = f"Visit Record ({visit_date}) — ClinicCare"
    body = (
        f"Dear {patient.personal.name},\n\n"
        f"Please find attached your visit record from {visit_date}, "
        f"prepared by {doctor_name}.\n\n"
        f"This document is strictly confidential. "
        f"If you have any questions, please contact the clinic.\n\n"
        f"Warm regards,\nClinicCare Team"
    )

    try:
        await _send_pdf_email(
            recipient_email=str(patient_email),
            recipient_name=patient.personal.name,
            subject=subject,
            body_text=body,
            pdf_bytes=pdf_bytes,
            filename=filename,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"PDF generated but email delivery failed: {str(e)}",
        )

    return {
        "sent": True,
        "recipient": str(patient_email),
        "patient_name": patient.personal.name,
        "visit_date": visit_date,
        "message": f"Visit record PDF sent to {patient_email}",
    }
