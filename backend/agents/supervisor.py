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

Classify the message into exactly one of these agents:

RECEPTIONIST — Use for FRONT-DESK / ADMINISTRATIVE tasks only:
  - Checking in a patient (new or returning)
  - Searching for a patient by name or phone number
  - Registering a new patient
  - Displaying administrative patient data: phone, email, address, assigned doctor, allergies list
  - "Check in Ajay Varma" / "Find patient by name" / "Register new patient"
  - "What is this patient's phone/email/address?"
  NOTE: This agent is for ADMINISTRATIVE lookups — NOT clinical history.

RAG_AGENT — Use for CLINICAL / MEDICAL questions about a patient:
  - Any question about a patient's medical history, visits, diagnoses, treatments
  - "Give info on this person [name]" — clinical summary
  - "What medications has this patient been on?"
  - "What was the diagnosis at the last visit?"
  - "Any drug interactions for this patient?"
  - "Tell me about [patient name]" / "Patient summary for [name]"
  - "What conditions does [patient] have?"
  - Pre-visit briefings or clinical overviews
  - Any question requiring medical record lookup

SCHEDULING — Use for:
  - Booking a new follow-up or appointment
  - Rescheduling or cancelling an existing appointment
  - Checking appointment slot availability
  - "Book appointment for [patient] on [date]"

NOTIFICATION — Use ONLY when EXPLICITLY asked to SEND an email or message:
  - "Send email to the patient"
  - "Send notification / reminder to the patient"
  - "Notify the doctor"
  - Message must contain an explicit send/email/notify ACTION

CALENDAR — Use for:
  - Querying existing schedule, appointments, or follow-ups
  - "Are there any follow-ups today?"
  - "What appointments do we have this week?"
  - "Show me the schedule for 25 March"
  - "Who has follow-ups pending?"
  - Cancelling or deleting a scheduled event
  - Any question about EXISTING bookings or scheduled dates

UNKNOWN — Use when the request doesn't fit any category.

CRITICAL DISTINCTIONS:
  RAG_AGENT vs RECEPTIONIST for patient queries:
    "Give info on [patient]" / "Tell me about [patient]" / "Patient summary" → RAG_AGENT (clinical)
    "What is [patient]'s phone?" / "Find patient [name]" / "Check in [patient]" → RECEPTIONIST (admin)

  SCHEDULING vs CALENDAR:
    SCHEDULING = creating/changing a booking
    CALENDAR   = reading/querying existing bookings

Respond ONLY with valid JSON, no other text, no markdown:
{"agent": "RECEPTIONIST", "intent": "new_patient_checkin", "confidence": 0.95}

confidence must be 0.0 to 1.0 (your certainty in the classification)."""


async def supervisor_node(state: AgentState) -> dict:
    last_message = state["messages"][-1].content
    logger.info("supervisor_classifying",
                thread_id=state.get("thread_id"),
                message_preview=last_message[:60])
    try:
        response = await _llm.ainvoke([
            SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT),
            HumanMessage(content=last_message),
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
            "2. Ask a clinical question about a patient\n"
            "3. Book or manage an appointment\n"
            "4. Send a message or notification"
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