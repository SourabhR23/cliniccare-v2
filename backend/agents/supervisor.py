"""
backend/agents/supervisor.py
"""

import json
import structlog
from langchain_core.messages import SystemMessage, HumanMessage

from backend.agents.state import AgentState
from backend.core.config import get_settings
from backend.core.llm import make_chat_llm

logger = structlog.get_logger(__name__)
settings = get_settings()

_llm = make_chat_llm(temperature=0)

SUPERVISOR_SYSTEM_PROMPT = """You are a clinic management supervisor routing staff requests to the correct AI assistant.

You will receive the LAST FEW MESSAGES for context. Classify the LATEST user message into exactly one agent.

RECEPTIONIST — Use for FRONT-DESK / ADMINISTRATIVE tasks only:
  - Checking in a patient (new or returning)
  - Searching for a patient by name or phone number
  - Registering a new patient
  - Displaying administrative patient data: phone, email, address, assigned doctor, allergies list
  - "Check in Ajay Varma" / "Find patient by name" / "Register new patient"
  - "What is this patient's phone/email/address?"
  - CONFIRMING patient registration: "Yes, register [name]" / "yes register them" / "yes please register"
    → ANY message that starts with "Yes, register" or "yes, register" is ALWAYS RECEPTIONIST.
  NOTE: This agent is for ADMINISTRATIVE lookups — NOT clinical history.

RAG_AGENT — Use for CLINICAL / MEDICAL questions about a patient:
  - Any question about a patient's medical history, visits, diagnoses, treatments
  - "What medications has this patient been on?"
  - "What was the diagnosis at the last visit?"
  - "Any drug interactions for this patient?"
  - "Tell me about [patient name]" / "Patient summary for [name]"
  - Pre-visit briefings or clinical overviews

SCHEDULING — Use for:
  - Booking a NEW follow-up or appointment
  - Rescheduling, changing, moving an existing appointment to a new date
  - "Book appointment for [patient] on [date]"
  - "Reschedule [patient]'s appointment" / "Change appointment to [date]"
  - "Move the appointment" / "Can we shift the date?" / "I want another date"
  - "My appointment is on X, I want to change it to Y"

NOTIFICATION — Use ONLY when EXPLICITLY asked to SEND an email or message:
  - "Send email to the patient"
  - "Send notification / reminder to the patient"
  - Message must contain an explicit send/email/notify ACTION

CALENDAR — Use for ANY of these:
  - Querying existing schedule, appointments, or follow-ups
  - "Are there any follow-ups today / this week / next week / next month?"
  - "What appointments do we have?"
  - "Show me the schedule for 25 March"
  - "Who has follow-ups pending?"
  - "How many bookings / appointments are there?"
  - "How many patients booked?" / "How many new patients booked?"
  - "Any bookings for [date/period]?"
  - Count or summary of existing appointments
  - Cancelling or deleting a scheduled event
  - ANY question about EXISTING bookings, scheduled dates, or future schedule
  - SHORT follow-up time references like "for next month?", "next week?", "in future", "this week?"
    when the conversation is already about appointments/schedule

UNKNOWN — Use when the request TRULY doesn't fit any category above.

CRITICAL DISTINCTIONS:
  SCHEDULING vs CALENDAR:
    SCHEDULING = creating/changing a NEW booking
    CALENDAR   = reading/querying/counting EXISTING bookings

  SHORT FOLLOW-UPS: If the last assistant message was about a schedule/calendar result,
    and the user says something like "for next month?", "what about next week?", "in future" —
    classify as CALENDAR.

  COUNTING appointments = CALENDAR (not UNKNOWN).

Respond ONLY with valid JSON, no other text, no markdown:
{"agent": "CALENDAR", "intent": "query_schedule", "confidence": 0.95}

confidence must be 0.0 to 1.0 (your certainty in the classification)."""


import re as _re

# Keyword pre-checks: bypass LLM for common patterns the LLM keeps misrouting.
# These are exact-match regex rules checked BEFORE calling the LLM.
_RECEPTIONIST_PATTERNS = [
    r"^yes,?\s+register",          # "Yes, register Akshay Kumar"
    r"^register\s+new\s+patient",  # "Register new patient ..."
    r"^no,?\s+(i'?ll?\s+)?search", # "No, I'll search again"
    r"^no,?\s+search\s+again",     # "No, search again"
    r"^check\s+in\s+",             # "Check in Riya Shah"
    r"^find\s+patient",            # "Find patient ..."
]

def _keyword_route(message: str) -> str | None:
    """Return agent name if message matches a hard-coded pattern, else None."""
    lower = message.lower().strip()
    for pattern in _RECEPTIONIST_PATTERNS:
        if _re.match(pattern, lower):
            return "RECEPTIONIST"
    return None


async def supervisor_node(state: AgentState) -> dict:
    last_message = state["messages"][-1].content
    logger.info("supervisor_classifying",
                thread_id=state.get("thread_id"),
                message_preview=last_message[:60])

    # ── Keyword pre-check (code-level, never wrong) ──────────
    keyword_agent = _keyword_route(last_message)
    if keyword_agent:
        intent_map = {
            "RECEPTIONIST": "register_patient" if "register" in last_message.lower() else "search_patient",
        }
        logger.info("supervisor_keyword_routed", agent=keyword_agent, message_preview=last_message[:40])
        return {
            "current_agent": keyword_agent,
            "intent": intent_map.get(keyword_agent, "keyword_match"),
            "confidence": 1.0,
        }

    # Build recent conversation context (last 4 messages, excluding the current one)
    # so the supervisor can resolve short follow-ups like "for next month?"
    recent_msgs = state["messages"][:-1][-4:]
    context_lines = []
    for m in recent_msgs:
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        snippet = m.content[:120].replace("\n", " ")
        context_lines.append(f"{role}: {snippet}")
    context_block = "\n".join(context_lines)
    classify_input = (
        f"Recent conversation:\n{context_block}\n\nLatest message to classify:\n{last_message}"
        if context_lines else last_message
    )

    try:
        response = await _llm.ainvoke([
            SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT),
            HumanMessage(content=classify_input),
        ])
        parsed = json.loads(response.content)
        agent = parsed.get("agent", "UNKNOWN")
        intent = parsed.get("intent", "unknown")
        confidence = float(parsed.get("confidence", 0.0))
        logger.info("supervisor_classified", agent=agent, intent=intent, confidence=confidence)
        will_fallback = agent == "UNKNOWN" or confidence < 0.70
        return {
            "current_agent": agent,
            "intent": intent,
            "confidence": confidence,
            **({"fallback_reason": "low_confidence"} if will_fallback else {}),
        }

    except json.JSONDecodeError as e:
        logger.warning("supervisor_json_parse_error", error=str(e))
        return {"current_agent": "UNKNOWN", "intent": "parse_error",
                "confidence": 0.0, "fallback_reason": "parse_error"}
    except Exception as e:
        logger.error("supervisor_unexpected_error", error=str(e))
        return {"current_agent": "UNKNOWN", "intent": "error",
                "confidence": 0.0, "fallback_reason": "llm_timeout", "error": str(e)}


def route_to_agent(state: AgentState) -> str:
    agent = state.get("current_agent", "UNKNOWN")
    confidence = state.get("confidence", 0.0)
    staff_role = state.get("staff_role", "")

    if confidence < 0.70:
        logger.info("supervisor_low_confidence_fallback",
                    confidence=confidence, agent=agent)
        return "fallback"

    # Doctors: only RAG and Calendar are permitted.
    # If the supervisor routed a patient-info query to RECEPTIONIST for a doctor,
    # reroute it to RAG_AGENT — the doctor wants clinical info, not admin intake.
    if staff_role == "doctor" and agent == "RECEPTIONIST":
        logger.info("supervisor_doctor_receptionist_rerouted_to_rag",
                    attempted_agent=agent, staff_role=staff_role)
        return "rag_agent"

    # Block Scheduling and Notification for doctors — those are receptionist duties.
    if staff_role == "doctor" and agent not in ("RAG_AGENT", "CALENDAR"):
        logger.info("supervisor_doctor_route_restricted",
                    attempted_agent=agent, staff_role=staff_role)
        return "fallback"

    # Receptionists: cannot access clinical RAG — that's doctor-only.
    if staff_role == "receptionist" and agent == "RAG_AGENT":
        logger.info("supervisor_receptionist_rag_blocked",
                    attempted_agent=agent, staff_role=staff_role)
        return "fallback"

    # Admins: Calendar only (unscoped — see all doctors).
    # They manage the system; patient intake and clinical queries go through other roles.
    if staff_role == "admin" and agent != "CALENDAR":
        logger.info("supervisor_admin_route_restricted",
                    attempted_agent=agent, staff_role=staff_role)
        return "fallback"

    return {
        "RECEPTIONIST": "receptionist_agent",
        "RAG_AGENT":    "rag_agent",
        "SCHEDULING":   "scheduling_agent",
        "NOTIFICATION": "notification_agent",
        "CALENDAR":     "calendar_agent",
        "UNKNOWN":      "fallback",
    }.get(agent, "fallback")


async def fallback_node(state: AgentState) -> dict:
    reason = state.get("fallback_reason", "unknown")
    logger.warning("agent_fallback",
                   thread_id=state.get("thread_id"),
                   reason=reason,
                   error=state.get("error"),
                   last_agent=state.get("current_agent"))
    staff_role = state.get("staff_role", "")
    is_doctor = staff_role == "doctor"
    is_admin = staff_role == "admin"

    if is_admin:
        low_confidence_msg = (
            "I can only help with **clinic schedule queries** in this interface.\n\n"
            "Try asking:\n"
            "- \"How many bookings does Dr. Anika have today?\"\n"
            "- \"Doctor 1 week plan\"\n"
            "- \"Follow-ups this week\"\n"
            "- \"Show appointments for 25 March\"\n"
            "- \"Cancel appointment ID abc123\""
        )
    elif is_doctor:
        low_confidence_msg = (
            "I can help you with:\n\n"
            "**Clinical questions:**\n"
            "- \"What medications has Ajay Varma been on?\"\n"
            "- \"What was the diagnosis at the last visit?\"\n\n"
            "**Schedule & follow-ups:**\n"
            "- \"Are there any follow-ups today?\"\n"
            "- \"Who comes in this week?\"\n"
            "- \"Show appointments for 25 March\""
        )
    else:
        low_confidence_msg = (
            "I wasn't sure how to handle that. Could you clarify what you need?\n\n"
            "1. Check in / register a patient\n"
            "2. Book or manage an appointment\n"
            "3. View the schedule or follow-ups\n"
            "4. Send a message or notification to a patient"
        )

    messages = {
        "low_confidence": low_confidence_msg,
        "parse_error":  "I had a temporary issue. Please try rephrasing.",
        "tool_error":   "System error. Please try again or contact IT support.",
        "llm_timeout":  "The AI assistant is temporarily unavailable. Please try again shortly.",
        "unknown":      "Something went wrong. Please try again.",
    }
    from langchain_core.messages import AIMessage
    return {
        "messages": [AIMessage(content=messages.get(reason, messages["unknown"]))],
        "current_agent": "fallback",
        "error_count": state.get("error_count", 0) + 1,
    }