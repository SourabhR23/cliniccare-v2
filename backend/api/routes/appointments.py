"""
backend/api/routes/appointments.py

GET /api/appointments  — Calendar events (appointments + follow-ups)

AUTH:
  receptionist → all appointments across all doctors
  doctor       → only their own appointments and patients

RESPONSE: list of CalendarEvent dicts, each with:
  id, type (appointment|followup), date, slot, patient_name,
  patient_id, doctor_name, doctor_id, status, reason
"""

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Optional

from backend.db.mongodb.connection import get_db
from backend.models.patient import TokenData, UserRoleEnum
from backend.api.middleware.auth_middleware import require_any_staff

router = APIRouter(prefix="/appointments", tags=["Appointments — Calendar"])


@router.get("/", response_model=list[dict])
async def list_calendar_events(
    month: Optional[str] = Query(None, description="YYYY-MM format. Defaults to current month."),
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Returns calendar events for a given month combining:
    1. appointments collection (booked via agent)
    2. patients.metadata.pending_followup_date

    Receptionist: sees all doctors.
    Doctor: sees only their own patients.
    """
    from datetime import date, datetime

    # Determine month range
    if month:
        try:
            year, m = map(int, month.split("-"))
        except ValueError:
            year, m = date.today().year, date.today().month
    else:
        year, m = date.today().year, date.today().month

    # Build date range strings for filtering
    month_start = f"{year}-{m:02d}-01"
    # Last day of month
    if m == 12:
        month_end = f"{year + 1}-01-01"
    else:
        month_end = f"{year}-{m + 1:02d}-01"

    events: list[dict] = []

    # ── 1. Appointments from appointments collection ───────────
    appt_filter: dict = {
        "appointment_date": {"$gte": month_start, "$lt": month_end},
    }
    if current_user.role == UserRoleEnum.DOCTOR.value:
        appt_filter["doctor_id"] = current_user.user_id

    cursor = db["appointments"].find(appt_filter)
    async for doc in cursor:
        events.append({
            "id": doc["_id"],
            "type": "appointment",
            "date": doc.get("appointment_date"),
            "slot": doc.get("appointment_slot"),
            "patient_name": doc.get("patient_name"),
            "patient_id": doc.get("patient_id"),
            "doctor_name": doc.get("doctor_name"),
            "doctor_id": doc.get("doctor_id"),
            "status": doc.get("status", "scheduled"),
            "reason": doc.get("followup_reason"),
        })

    # ── 2. Follow-ups from patients.metadata.pending_followup_date ──
    followup_filter: dict = {
        "metadata.pending_followup_date": {"$gte": month_start, "$lt": month_end},
    }
    if current_user.role == UserRoleEnum.DOCTOR.value:
        followup_filter["personal.assigned_doctor_id"] = current_user.user_id

    patient_cursor = db["patients"].find(
        followup_filter,
        {
            "_id": 1,
            "personal.name": 1,
            "personal.assigned_doctor_id": 1,
            "metadata.pending_followup_date": 1,
        },
    )

    # Build doctor name lookup for follow-ups
    doctor_name_cache: dict[str, str] = {}

    async for p in patient_cursor:
        doc_id = p.get("personal", {}).get("assigned_doctor_id")
        if doc_id and doc_id not in doctor_name_cache:
            doc_user = await db["users"].find_one({"_id": doc_id}, {"name": 1})
            doctor_name_cache[doc_id] = doc_user["name"] if doc_user else doc_id

        followup_date = p.get("metadata", {}).get("pending_followup_date")
        # pending_followup_date may be a date object or ISO string
        if followup_date:
            if hasattr(followup_date, "isoformat"):
                followup_date = followup_date.isoformat()
            else:
                followup_date = str(followup_date)[:10]  # keep YYYY-MM-DD

        events.append({
            "id": f"followup_{p['_id']}",
            "type": "followup",
            "date": followup_date,
            "slot": None,
            "patient_name": p.get("personal", {}).get("name"),
            "patient_id": p["_id"],
            "doctor_name": doctor_name_cache.get(doc_id),
            "doctor_id": doc_id,
            "status": "pending",
            "reason": "Follow-up",
        })

    # Sort by date
    events.sort(key=lambda e: (e.get("date") or ""))

    return events


@router.patch("/{appointment_id}/cancel", response_model=dict)
async def cancel_appointment(
    appointment_id: str,
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Cancel an appointment (set status to cancelled)."""
    result = await db["appointments"].update_one(
        {"_id": appointment_id},
        {"$set": {"status": "cancelled"}},
    )
    if result.matched_count == 0:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Appointment {appointment_id} not found")
    return {"id": appointment_id, "status": "cancelled"}


@router.delete("/{appointment_id}", response_model=dict)
async def delete_appointment(
    appointment_id: str,
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Hard-delete an appointment from the database."""
    from fastapi import HTTPException, status as http_status
    result = await db["appointments"].delete_one({"_id": appointment_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                            detail=f"Appointment {appointment_id} not found")
    return {"id": appointment_id, "deleted": True}


@router.post("/{appointment_id}/notify", response_model=dict)
async def notify_appointment(
    appointment_id: str,
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Send a notification/reminder email to the patient for an appointment."""
    import aiosmtplib
    from email.mime.text import MIMEText
    from fastapi import HTTPException, status as http_status
    from backend.core.config import get_settings

    cfg = get_settings()

    appt = await db["appointments"].find_one({"_id": appointment_id})
    if not appt:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                            detail=f"Appointment {appointment_id} not found")

    # Resolve patient email if not stored on appointment
    patient_email = appt.get("patient_email")
    if not patient_email and appt.get("patient_id"):
        patient_doc = await db["patients"].find_one(
            {"_id": appt["patient_id"]}, {"personal.email": 1}
        )
        if patient_doc:
            email_val = patient_doc.get("personal", {}).get("email")
            patient_email = str(email_val) if email_val else None

    if not patient_email:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No email address on file for this patient.",
        )

    patient_name = appt.get("patient_name", "Patient")
    doctor_name = appt.get("doctor_name", "your doctor")
    appt_date = appt.get("appointment_date", "")
    appt_slot = appt.get("appointment_slot", "")
    reason = appt.get("followup_reason") or "General Consultation"

    body = (
        f"Dear {patient_name},\n\n"
        f"This is a reminder of your upcoming appointment:\n\n"
        f"  Date:   {appt_date}\n"
        f"  Time:   {appt_slot}\n"
        f"  Doctor: {doctor_name}\n"
        f"  Reason: {reason}\n\n"
        f"Please arrive 10 minutes early. If you need to reschedule, "
        f"contact the clinic as soon as possible.\n\n"
        f"Thank you,\nClinicCare Team"
    )

    msg = MIMEText(body, "plain")
    msg["Subject"] = f"Appointment Reminder — {appt_date} at {appt_slot}"
    msg["From"] = cfg.smtp_from_email
    msg["To"] = patient_email

    try:
        use_implicit_tls = cfg.smtp_port == 465
        async with aiosmtplib.SMTP(
            hostname=cfg.smtp_host,
            port=cfg.smtp_port,
            use_tls=use_implicit_tls,
            start_tls=not use_implicit_tls,
        ) as smtp:
            await smtp.login(cfg.smtp_username, cfg.smtp_password)
            await smtp.send_message(msg)
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=f"Email delivery failed: {exc}",
        )

    return {"id": appointment_id, "notified": True, "email": patient_email}


CLINIC_SLOTS = [
    "09:00 AM", "09:30 AM", "10:00 AM", "10:30 AM", "11:00 AM", "11:30 AM",
    "02:00 PM", "02:30 PM", "03:00 PM", "03:30 PM", "04:00 PM", "04:30 PM",
]
MAX_PATIENTS_PER_DAY = 10


@router.get("/available-slots", response_model=dict)
async def get_available_slots(
    date: str = Query(..., description="YYYY-MM-DD"),
    doctor_id: str = Query(..., description="Doctor user _id"),
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Returns booked and available slots for a doctor on a specific date.
    Used by the frontend calendar to show capacity.
    """
    cursor = db["appointments"].find(
        {
            "doctor_id": doctor_id,
            "appointment_date": date,
            "status": {"$ne": "cancelled"},
        },
        {"appointment_slot": 1, "patient_name": 1},
    )
    booked_docs = await cursor.to_list(None)
    booked_slots = [d["appointment_slot"] for d in booked_docs if d.get("appointment_slot")]
    available_slots = [s for s in CLINIC_SLOTS if s not in booked_slots]

    return {
        "date": date,
        "doctor_id": doctor_id,
        "total_capacity": MAX_PATIENTS_PER_DAY,
        "booked_count": len(booked_docs),
        "available_count": len(available_slots),
        "is_full": len(booked_docs) >= MAX_PATIENTS_PER_DAY,
        "booked_slots": booked_slots,
        "available_slots": available_slots,
    }
