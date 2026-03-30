"""
backend/agents/notification_templates.py

Pre-written templates for standard email types.
Intercepts compose_email() before the LLM is called.

FLOW:
  render_template() → (body, used_template)
  used_template=True  → return body directly, skip LLM
  used_template=False → caller falls through to LLM generation

WHEN LLM IS STILL USED:
  - staff_context > 80 chars (custom instructions present)
  - staff_context contains custom-note keywords (fast, bring, prepare, etc.)
  - required fields missing (e.g. no appointment_date for a reminder)
  - email_type not in registry

COVERAGE:
  reminder, confirmation, cancellation, rescheduling, followup  → ~80% of all emails
  alert, custom                                                  → always LLM
"""

import re
from typing import Optional

# ── Template strings ──────────────────────────────────────────────────────────
# {patient_name}, {doctor_name}, {appointment_date}, {appointment_slot},
# {followup_date} are substituted at render time.

_TEMPLATES: dict[str, str] = {
    "reminder": (
        "Dear {patient_name},\n\n"
        "This is a friendly reminder that you have an appointment scheduled with "
        "{doctor_name} on {appointment_date} at {appointment_slot}.\n\n"
        "Please arrive 10 minutes before your appointment time. "
        "If you need to reschedule or cancel, please contact us at least 24 hours in advance.\n\n"
        "Warm regards,\n"
        "ClinicCare Team\n\n"
        "Questions? Call us at our clinic number."
    ),

    "confirmation": (
        "Dear {patient_name},\n\n"
        "Your appointment with {doctor_name} has been confirmed for "
        "{appointment_date} at {appointment_slot}.\n\n"
        "We look forward to seeing you. "
        "Please bring any relevant medical records or test results if available.\n\n"
        "Warm regards,\n"
        "ClinicCare Team\n\n"
        "Questions? Call us at our clinic number."
    ),

    "cancellation": (
        "Dear {patient_name},\n\n"
        "We would like to inform you that your appointment with {doctor_name} "
        "on {appointment_date} has been cancelled.\n\n"
        "Please contact us to reschedule at your earliest convenience. "
        "We apologise for any inconvenience caused.\n\n"
        "Warm regards,\n"
        "ClinicCare Team\n\n"
        "Questions? Call us at our clinic number."
    ),

    "rescheduling": (
        "Dear {patient_name},\n\n"
        "Your appointment with {doctor_name} has been rescheduled to "
        "{appointment_date} at {appointment_slot}.\n\n"
        "Please confirm your availability for the new slot. "
        "If this time does not work for you, please contact us and we will find an alternative.\n\n"
        "Warm regards,\n"
        "ClinicCare Team\n\n"
        "Questions? Call us at our clinic number."
    ),

    "followup": (
        "Dear {patient_name},\n\n"
        "This is a reminder for your follow-up appointment with {doctor_name} "
        "on {followup_date}.\n\n"
        "Follow-up appointments are important to monitor your health progress. "
        "Please ensure you attend, or contact us to reschedule if necessary.\n\n"
        "Warm regards,\n"
        "ClinicCare Team\n\n"
        "Questions? Call us at our clinic number."
    ),
}

# ── Custom-instruction detection ──────────────────────────────────────────────
# If staff_context exceeds this, it likely contains bespoke instructions → use LLM
_CUSTOM_NOTE_CHAR_THRESHOLD = 80

# Keywords that signal custom content the template cannot cover
_CUSTOM_NOTE_KEYWORDS = (
    "fast", "bring", "prepare", "take before", "avoid",
    "remind him", "remind her", "remind them",
    "special", "note that", "also tell", "please tell",
    "mention", "inform him", "inform her", "let him", "let her",
    "don't forget", "make sure",
)


def _has_custom_instructions(staff_context: str) -> bool:
    """Return True when staff_context contains custom content the template cannot cover."""
    if len(staff_context.strip()) > _CUSTOM_NOTE_CHAR_THRESHOLD:
        return True
    lower = staff_context.lower()
    return any(kw in lower for kw in _CUSTOM_NOTE_KEYWORDS)


def _clean_empty_slot_line(body: str) -> str:
    """Remove awkward ' at .' artifacts when appointment_slot is empty."""
    body = re.sub(r' at \.$', '.', body, flags=re.MULTILINE)
    body = re.sub(r' at \s*\n', '\n', body)
    return body


# ── Public API ────────────────────────────────────────────────────────────────

def render_template(
    email_type: str,
    patient_name: str,
    doctor_name: Optional[str],
    appointment_date: Optional[str],
    appointment_slot: Optional[str],
    followup_date: Optional[str],
    staff_context: str = "",
) -> tuple[Optional[str], bool]:
    """
    Attempt to render a standard email template.

    Returns:
        (rendered_body, used_template)
        used_template=True  → body is ready to send, skip LLM
        used_template=False → caller should fall through to LLM
    """
    # Custom instructions present → LLM handles it
    if staff_context and _has_custom_instructions(staff_context):
        return None, False

    template = _TEMPLATES.get(email_type)
    if not template:
        return None, False

    # Reminder / confirmation / rescheduling require a date to be meaningful
    if email_type in ("reminder", "confirmation", "rescheduling") and not appointment_date:
        return None, False

    # Resolve safe fallbacks for optional fields
    doctor_val   = doctor_name      or "your doctor"
    date_val     = appointment_date or followup_date or "the scheduled date"
    slot_val     = appointment_slot or ""
    followup_val = followup_date    or appointment_date or "the scheduled date"

    try:
        body = template.format(
            patient_name=patient_name,
            doctor_name=doctor_val,
            appointment_date=date_val,
            appointment_slot=slot_val,
            followup_date=followup_val,
        )
        body = _clean_empty_slot_line(body)
        return body, True
    except KeyError:
        return None, False
