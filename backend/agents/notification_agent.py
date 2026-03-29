"""
backend/agents/notification_agent.py — Email composition and dispatch
"""

import asyncio
import structlog
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from backend.agents.state import AgentState
from backend.core.config import get_settings
from backend.core.llm import make_chat_llm

logger = structlog.get_logger(__name__)
settings = get_settings()

_llm = make_chat_llm(temperature=0.3)

EMAIL_COMPOSE_PROMPT = """Compose a professional appointment/follow-up email for ClinicCare.

Rules:
1. NEVER include diagnosis, medications, test results, or clinical data
2. Use ONLY the exact values provided — never use placeholder brackets like [Doctor's Name]
3. Omit any "not specified" values naturally
4. Tone: warm, professional, concise. Length: 4–6 sentences.
5. Include specific dates, times, doctor name when available
6. Extract any dates/changes from Staff Request and include them
7. Close with: "Warm regards,\nClinicCare Team\n\nQuestions? Call us at our clinic number."

Context:
  Patient: {patient_name}
  Type: {email_type}
  Appointment date: {appointment_date}
  Time slot: {appointment_slot}
  Follow-up date: {pending_followup_date}
  Doctor: {doctor_name}
  Staff request: {staff_context}

Write ONLY the email body. Start with "Dear {patient_name},\""""

EMAIL_SUBJECTS = {
    "reminder":      "Appointment Reminder — ClinicCare",
    "confirmation":  "Appointment Confirmed — ClinicCare",
    "cancellation":  "Appointment Update — ClinicCare",
    "alert":         "Important Update from ClinicCare",
    "rescheduling":  "Follow-up Rescheduled — ClinicCare",
    "followup":      "Follow-up Appointment — ClinicCare",
}

# Map supervisor intents to email_type values
_INTENT_TO_EMAIL_TYPE: dict[str, str] = {
    "send_follow_up_notification":  "followup",
    "follow_up_reminder":           "followup",
    "send_followup":                "followup",
    "followup_notification":        "followup",
    "send_appointment_reminder":    "reminder",
    "appointment_reminder":         "reminder",
    "send_rescheduling":            "rescheduling",
    "reschedule_notification":      "rescheduling",
    "send_cancellation":            "cancellation",
    "cancellation_notification":    "cancellation",
    "send_confirmation":            "confirmation",
    "appointment_confirmed":        "confirmation",
}

MAX_EMAIL_RETRIES = 3


async def compose_email(state: AgentState) -> dict:
    patient_email = state.get("patient_email")
    if not patient_email:
        return {
            "messages": [AIMessage(content="No patient email address on file. Cannot send notification.")],
            "email_sent": False,
            "error": "no_email_address",
        }

    # Infer email_type from intent if not explicitly set
    email_type = state.get("email_type")
    if not email_type:
        intent = state.get("intent", "")
        email_type = _INTENT_TO_EMAIL_TYPE.get(intent, "alert")

    # Extract the last HumanMessage as staff context so LLM can use specific
    # dates/changes the receptionist mentioned (e.g. "changed from 22 to 23 march")
    staff_context = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            staff_context = msg.content
            break

    prompt = EMAIL_COMPOSE_PROMPT.format(
        patient_name=state.get("patient_name") or "Patient",
        email_type=email_type,
        appointment_date=state.get("appointment_date") or "not specified",
        appointment_slot=state.get("appointment_slot") or "not specified",
        pending_followup_date=state.get("pending_followup_date") or "not specified",
        doctor_name=state.get("assigned_doctor_name") or "not specified",
        staff_context=staff_context or "Send a general notification.",
    )

    try:
        response = await _llm.ainvoke([
            SystemMessage(content=prompt),
        ])
        logger.info("email_composed", email_type=email_type,
                    patient_id=state.get("patient_id"))
        return {"email_body": response.content, "email_attempt": 1, "email_type": email_type}
    except Exception as e:
        logger.error("email_compose_error", error=str(e))
        return {"error": str(e), "fallback_reason": "llm_timeout", "email_sent": False}


async def send_email(state: AgentState) -> dict:
    email_body = state.get("email_body")
    if not email_body:
        return {"email_sent": False, "error": "no_email_body"}

    patient_email = state.get("patient_email")
    if not patient_email:
        return {"email_sent": False, "error": "no_recipient"}

    attempt = state.get("email_attempt", 1)
    email_type = state.get("email_type", "reminder")
    subject = EMAIL_SUBJECTS.get(email_type, "Message from ClinicCare")

    if attempt > 1:
        await asyncio.sleep(2 ** (attempt - 1))

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
        msg["To"] = patient_email
        msg.attach(MIMEText(email_body, "plain"))

        # Port 465 → implicit SSL: use_tls=True, start_tls=False
        # Port 587 → STARTTLS:    use_tls=False, start_tls=True (library negotiates it)
        # Never call smtp.starttls() manually — aiosmtplib handles it via start_tls=True,
        # and calling it again on an already-TLS connection raises "Connection already using TLS"
        use_implicit_tls = settings.smtp_port == 465

        async with aiosmtplib.SMTP(
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            use_tls=use_implicit_tls,
            start_tls=not use_implicit_tls,
        ) as smtp:
            await smtp.login(settings.smtp_username, settings.smtp_password)
            await smtp.send_message(msg)

        logger.info("email_sent_success", recipient=patient_email,
                    email_type=email_type, attempt=attempt)
        patient_name = state.get("patient_name") or "the patient"
        return {
            "email_sent": True,
            "email_attempt": attempt,
            "messages": [AIMessage(
                content=f"✓ {email_type.capitalize()} email sent to {patient_name} ({patient_email}) successfully."
            )],
        }

    except aiosmtplib.SMTPException as e:
        logger.warning("email_smtp_error", error=str(e), attempt=attempt)
        if attempt < MAX_EMAIL_RETRIES:
            return {"email_attempt": attempt + 1, "intent": "retry_email", "error": str(e)}
        logger.error("email_all_retries_failed", recipient=patient_email, attempts=attempt)
        return {
            "email_sent": False,
            "intent": "give_up",   # clear retry_email intent so route_after_send goes to log_result
            "error": f"Email failed after {attempt} attempts: {str(e)}",
            "messages": [AIMessage(content=f"Unable to send email to {patient_email} after {attempt} attempts.")],
        }


def route_after_send(state: AgentState) -> str:
    return "send_email" if state.get("intent") == "retry_email" else "log_result"


async def log_result(state: AgentState) -> dict:
    if state.get("email_sent"):
        logger.info("notification_complete", patient_id=state.get("patient_id"),
                    email_type=state.get("email_type"))
    else:
        logger.error("notification_failed", patient_id=state.get("patient_id"),
                     error=state.get("error"))
    return {}