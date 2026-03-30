"""
backend/agents/supervisor.py
"""

import hashlib
import json
import re as _re
import time
import structlog
from langchain_core.messages import SystemMessage, HumanMessage

from backend.agents.history_compressor import maybe_compress
from backend.agents.state import AgentState
from backend.core.config import get_settings
from backend.core.llm import make_chat_llm

logger = structlog.get_logger(__name__)
settings = get_settings()

_llm = make_chat_llm(temperature=0)

# ── Routing cache ─────────────────────────────────────────────────────────────
# In-memory TTL cache for supervisor routing decisions.
# Proper nouns (patient/doctor names) and dates are stripped before hashing so
# structurally identical messages share the same cache entry.
#
# Skipped for: corrections ("actually…", "wait…"), low-confidence results.
#
# TTL:       600s (10 minutes) — intent doesn't change mid-session
# Max size:  500 entries — evicts oldest when full

_routing_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL_SECONDS = 600
_CACHE_MAX_ENTRIES = 500

# Patterns stripped before hashing (capitalised proper nouns, numeric dates)
_PROPER_NOUN_RE  = _re.compile(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b')
_DATE_RE         = _re.compile(r'\b\d{1,2}[\s/-][A-Za-z]+[\s/-]\d{2,4}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b')
_MULTI_SPACE_RE  = _re.compile(r'\s+')


def _routing_cache_key(message: str, staff_role: str) -> str:
    """Normalise message by stripping names/dates, then hash with staff_role."""
    normalised = _PROPER_NOUN_RE.sub('[N]', message)
    normalised = _DATE_RE.sub('[D]', normalised)
    normalised = _MULTI_SPACE_RE.sub(' ', normalised).lower().strip()
    raw = f"route:{staff_role}:{normalised}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _cache_get(key: str) -> dict | None:
    entry = _routing_cache.get(key)
    if entry:
        result, ts = entry
        if time.time() - ts < _CACHE_TTL_SECONDS:
            return result
        del _routing_cache[key]
    return None


def _cache_set(key: str, result: dict) -> None:
    if len(_routing_cache) >= _CACHE_MAX_ENTRIES:
        oldest = min(_routing_cache, key=lambda k: _routing_cache[k][1])
        del _routing_cache[oldest]
    _routing_cache[key] = (result, time.time())


SUPERVISOR_SYSTEM_PROMPT = """Route clinic staff messages to the correct agent. Output JSON only, no other text.

RECEPTIONIST — patient check-in, name/phone search, new patient registration, admin info (phone/email/address/allergies)
RAG_AGENT    — medical history, diagnoses, medications, drug interactions, clinical summaries, pre-visit briefs
SCHEDULING   — book new appointment, reschedule/change/move an existing appointment
NOTIFICATION — only when staff explicitly says send/email/notify a patient
CALENDAR     — view/count/query existing schedule, follow-ups, appointments by date; cancel a scheduled event
UNKNOWN      — nothing matches

Rules:
- "tell me about [patient]" → RAG_AGENT (clinical), not RECEPTIONIST (admin)
- "patient's phone/address" → RECEPTIONIST (admin lookup)
- Short follow-up like "for next month?" after a schedule result → CALENDAR
- SCHEDULING = creating/changing a booking; CALENDAR = reading/counting existing bookings
- confidence: 0.0–1.0

{"agent": "CALENDAR", "intent": "query_schedule", "confidence": 0.95}"""


# Post-booking session-close patterns — when staff says "no" / "nothing" after booking
_SESSION_CLOSE_PATTERNS = [
    r"^no[\s,!.]*$",
    r"^nope[\s,!.]*$",
    r"^nothing[\s,!.]*$",
    r"^(that'?s?|that\s+is)\s+(all|it|good|great|fine|enough)[\s,!.]*$",
    r"^(all\s+)?done[\s,!.]*$",
    r"^(no|nope),?\s+thank",
    r"^i'?m?\s+(good|done|all\s+set|fine|okay|ok)[\s,!.]*$",
    r"^not\s+right\s+now[\s,!.]*$",
    r"^we'?re\s+(good|done|all\s+set)[\s,!.]*$",
    r"^that\s+(will\s+be|would\s+be)\s+all[\s,!.]*$",
]

# Keyword pre-checks: bypass LLM for common patterns the LLM keeps misrouting.
# These are exact-match regex rules checked BEFORE calling the LLM.
_RECEPTIONIST_PATTERNS = [
    r"^yes,?\s+register",          # "Yes, register Akshay Kumar"
    r"^register\s+new\s+patient",  # "Register new patient ..."
    r"^no,?\s+(i'?ll?\s+)?search", # "No, I'll search again"
    r"^no,?\s+search\s+again",     # "No, search again"
    r"^check\s+in\s+",             # "Check in Riya Shah"
    r"^find\s+patient",            # "Find patient ..."
    r"^__register__:",             # Form submission from registration flashcard
]

_SCHEDULING_PATTERNS = [
    r"^yes,?\s+book",              # "Yes, book" — after registration success message
    r"^yes,?\s+book\s+on",        # "Yes, book on 30 March"
]

def _keyword_route(message: str) -> str | None:
    """Return agent name if message matches a hard-coded pattern, else None."""
    lower = message.lower().strip()
    for pattern in _RECEPTIONIST_PATTERNS:
        if _re.match(pattern, lower):
            return "RECEPTIONIST"
    for pattern in _SCHEDULING_PATTERNS:
        if _re.match(pattern, lower):
            return "SCHEDULING"
    return None


_CORRECTION_PATTERNS = [
    r"^actually[,\s]",
    r"^wait[,.\s]",
    r"^no[,\s]+i\s+meant",
    r"^sorry[,\s]+(i\s+meant|wrong)",
    r"^change\s+that\s+to\b",
    r"^not\s+that[,.\s]",
    r"^i\s+said\s+the\s+wrong\b",
    r"^wrong\s+(date|time|doctor|patient|slot)\b",
    r"^my\s+mistake\b",
]


def _is_correction(message: str) -> bool:
    """True if the message looks like a correction to the previous agent response."""
    lower = message.lower().strip()
    for pattern in _CORRECTION_PATTERNS:
        if _re.match(pattern, lower):
            return True
    return False


async def supervisor_node(state: AgentState) -> dict:
    # ── History compression (before routing — keeps token cost flat) ──────────
    messages = await maybe_compress(state["messages"])

    last_message = messages[-1].content
    logger.info("supervisor_classifying",
                thread_id=state.get("thread_id"),
                message_preview=last_message[:60])

    # ── Post-booking session close (before any LLM call) ─────
    if state.get("booking_done"):
        lower = last_message.lower().strip()
        for pat in _SESSION_CLOSE_PATTERNS:
            if _re.match(pat, lower):
                logger.info("supervisor_session_close_detected",
                            thread_id=state.get("thread_id"))
                return {
                    "current_agent": "SESSION_END",
                    "intent": "session_close",
                    "confidence": 1.0,
                }

    # ── Keyword pre-check (code-level, never wrong) ──────────
    keyword_agent = _keyword_route(last_message)
    if keyword_agent:
        lower_msg = last_message.lower()
        intent_map = {
            "RECEPTIONIST": "register_patient" if "register" in lower_msg else "search_patient",
            "SCHEDULING":   "book_appointment",
        }
        logger.info("supervisor_keyword_routed", agent=keyword_agent, message_preview=last_message[:40])
        return {
            "current_agent": keyword_agent,
            "intent": intent_map.get(keyword_agent, "keyword_match"),
            "confidence": 1.0,
        }

    # ── Correction detection — route back to the last active agent ──────
    last_agent = state.get("current_agent", "UNKNOWN")
    if last_agent in ("SCHEDULING", "RECEPTIONIST", "CALENDAR", "RAG_AGENT") \
            and _is_correction(last_message):
        logger.info("supervisor_correction_detected",
                    rerouted_to=last_agent,
                    message_preview=last_message[:60])
        return {
            "current_agent": last_agent,
            "intent": "correction",
            "confidence": 1.0,
        }

    # ── Routing cache key (computed once, reused for get + set) ─────────────
    staff_role = state.get("staff_role", "")
    cache_key = _routing_cache_key(last_message, staff_role)
    cached = _cache_get(cache_key)
    if cached:
        logger.info("supervisor_cache_hit", cache_key=cache_key,
                    agent=cached.get("current_agent"))
        return cached

    # Build recent conversation context only when the message is ambiguous.
    # Long, explicit messages (>55 chars) or clear action keywords don't need
    # history to route correctly — skipping context saves ~300-500 tokens per call.
    _SELF_CONTAINED_KEYWORDS = (
        "book", "appointment", "register", "schedule", "patient", "send",
        "email", "notify", "calendar", "follow", "diagnos", "medication",
        "history", "visit", "reschedule", "cancel", "slot", "date",
    )
    msg_lower = last_message.lower()
    is_self_contained = (
        len(last_message) > 55
        or any(kw in msg_lower for kw in _SELF_CONTAINED_KEYWORDS)
    )

    if is_self_contained:
        classify_input = last_message
    else:
        # Short / ambiguous message — include recent context so supervisor can
        # resolve follow-ups like "for next month?" or "same doctor?"
        recent_msgs = messages[:-1][-6:]
        context_lines = []
        for m in recent_msgs:
            role = "User" if isinstance(m, HumanMessage) else "Assistant"
            snippet = m.content[:100].replace("\n", " ")
            if snippet and not snippet.startswith("__AGENT_UI__"):
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
        result = {
            "current_agent": agent,
            "intent": intent,
            "confidence": confidence,
            **({"fallback_reason": "low_confidence"} if will_fallback else {}),
        }
        # Cache only high-confidence, non-correction results (corrections are
        # context-dependent and must never be replayed from cache)
        if not will_fallback and not _is_correction(last_message):
            _cache_set(cache_key, result)
        return result

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
        "SESSION_END":  "session_end",
        "UNKNOWN":      "fallback",
    }.get(agent, "fallback")


async def session_end_node(state: AgentState) -> dict:
    """Emits a warm goodbye when staff signals they're done after a booking."""
    from langchain_core.messages import AIMessage
    patient_name = state.get("patient_name", "")
    name_part = f" {patient_name}'s" if patient_name else " the"
    return {
        "messages": [AIMessage(
            content=(
                f"You're all set!{name_part} appointment is confirmed. "
                "Have a great day! 👋\n\n"
                "> To start a new conversation, click **New Chat** above."
            )
        )],
        "current_agent": "SESSION_END",
        "booking_done": False,
    }


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