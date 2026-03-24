"""
backend/agents/calendar_agent.py

CALENDAR AGENT — Schedule queries and event cancellation.

Handles:
  - "are there any follow-ups today?"
  - "what appointments do we have this week?"
  - "delete/cancel appointment on 25 march"
  - "remove follow-up for Ajay Varma"
  - "show available slots for 25 march"
"""

import json
import re
import structlog
from datetime import date, timedelta
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from backend.agents.state import AgentState
from backend.core.llm import make_chat_llm

logger = structlog.get_logger(__name__)
_llm = make_chat_llm(temperature=0)

MAX_PATIENTS_PER_DAY = 10
CLINIC_SLOTS = [
    "09:00 AM", "09:30 AM", "10:00 AM", "10:30 AM", "11:00 AM", "11:30 AM",
    "02:00 PM", "02:30 PM", "03:00 PM", "03:30 PM", "04:00 PM", "04:30 PM",
]

# ─────────────────────────────────────────────────────────────
# INTENT DETECTION
# ─────────────────────────────────────────────────────────────

CANCEL_KEYWORDS = {
    "cancel", "delete", "remove", "clear", "drop",
    "cancelling", "deleting", "removing",
}


def _is_cancel_request(message: str) -> bool:
    words = set(message.lower().split())
    return bool(words & CANCEL_KEYWORDS)


# ─────────────────────────────────────────────────────────────
# QUERY PROMPT
# ─────────────────────────────────────────────────────────────

CALENDAR_EXTRACT_PROMPT = """Extract calendar query parameters from the staff message.

Staff message: "{message}"
Today's date: {today}

Extract:
1. date_start: ISO date (YYYY-MM-DD).
   - "today" → today, "tomorrow" → tomorrow
   - "this week" → today, "next week" → next monday
   - "25 march" / "25 march 2026" → "2026-03-25"
   - If no date → today
2. date_end: ISO date (end of range, inclusive).
   - Single day → same as date_start
   - "this week" → coming sunday, "next week" → next sunday
   - "this month" → last day of month
3. patient_name: patient name if mentioned, else null
4. doctor_name: doctor name if mentioned, else null
5. event_type: "appointment" | "followup" | "both" (default: "both")
6. show_slots: true if asking about available slots/capacity, else false

Respond ONLY with valid JSON:
{{"date_start":"2026-03-20","date_end":"2026-03-20","patient_name":null,"doctor_name":null,"event_type":"both","show_slots":false}}"""


# ─────────────────────────────────────────────────────────────
# CANCEL PROMPT
# ─────────────────────────────────────────────────────────────

CANCEL_EXTRACT_PROMPT = """Extract cancellation details from the staff message.

Staff message: "{message}"
Today's date: {today}

Extract:
1. cancel_type: "appointment" | "followup" | "both"
2. appointment_date: ISO date if cancelling an appointment by date (e.g. "25 march" → "2026-03-25"), else null
3. patient_name: patient name if mentioned, else null
4. appointment_id: appointment ID (starts with APT) if mentioned, else null

Respond ONLY with valid JSON:
{{"cancel_type":"appointment","appointment_date":"2026-03-25","patient_name":null,"appointment_id":null}}"""


# ─────────────────────────────────────────────────────────────
# MAIN DISPATCH NODE
# ─────────────────────────────────────────────────────────────

async def calendar_dispatch(state: AgentState, db) -> dict:
    """
    Single entry point for all calendar operations.
    Routes to cancel flow or query flow based on message intent.

    DOCTOR SCOPING:
    When staff_role == "doctor", all queries are automatically restricted to
    the doctor's own patients (appointments where doctor_id = staff_id,
    follow-ups where assigned_doctor_id = staff_id).
    Doctors never see other doctors' schedules.
    """
    last_message = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            last_message = msg.content
            break

    if _is_cancel_request(last_message):
        return await _handle_cancel(state, db, last_message)
    return await _handle_query(state, db, last_message)


# ─────────────────────────────────────────────────────────────
# CANCEL HANDLER
# ─────────────────────────────────────────────────────────────

async def _handle_cancel(state: AgentState, db, message: str) -> dict:
    today = date.today()
    # Doctor scoping — doctors can only cancel their own patients' appointments
    is_doctor = state.get("staff_role") == "doctor"
    doctor_scope_id = state.get("staff_id") if is_doctor else None

    try:
        response = await _llm.ainvoke([
            SystemMessage(content=CANCEL_EXTRACT_PROMPT.format(
                message=message, today=today.isoformat()
            )),
        ])
        params = json.loads(response.content.strip())
    except Exception as e:
        logger.warning("calendar_cancel_extract_error", error=str(e))
        return {
            "messages": [AIMessage(
                content="I couldn't understand what to cancel. Please say: "
                        "'Cancel appointment on 25 March' or 'Remove follow-up for Ajay Varma'."
            )]
        }

    cancel_type = params.get("cancel_type", "appointment")
    appointment_date = params.get("appointment_date")
    patient_name = params.get("patient_name") or state.get("patient_name")
    appointment_id = params.get("appointment_id")

    cancelled = []
    not_found = []

    # ── Cancel appointment ─────────────────────────────────────
    if cancel_type in ("appointment", "both"):
        appt_filter: dict = {"status": {"$ne": "cancelled"}}
        if doctor_scope_id:
            appt_filter["doctor_id"] = doctor_scope_id

        if appointment_id:
            appt_filter["_id"] = appointment_id
        elif appointment_date:
            appt_filter["appointment_date"] = appointment_date
            if patient_name:
                appt_filter["patient_name"] = {
                    "$regex": re.escape(patient_name), "$options": "i"
                }
            elif state.get("patient_id"):
                appt_filter["patient_id"] = state["patient_id"]
        elif patient_name:
            appt_filter["patient_name"] = {
                "$regex": re.escape(patient_name), "$options": "i"
            }

        if len(appt_filter) > 1:  # has at least one real filter
            appt_doc = await db["appointments"].find_one(appt_filter)
            if appt_doc:
                await db["appointments"].update_one(
                    {"_id": appt_doc["_id"]},
                    {"$set": {"status": "cancelled"}},
                )
                cancelled.append(
                    f"Appointment for {appt_doc.get('patient_name', 'patient')} "
                    f"on {appt_doc.get('appointment_date')} "
                    f"at {appt_doc.get('appointment_slot', '')} — cancelled."
                )
                logger.info("appointment_cancelled_via_agent",
                            appointment_id=appt_doc["_id"])
            else:
                not_found.append(
                    f"No active appointment found"
                    + (f" on {appointment_date}" if appointment_date else "")
                    + (f" for {patient_name}" if patient_name else "")
                    + "."
                )

    # ── Cancel / clear follow-up ───────────────────────────────
    if cancel_type in ("followup", "both"):
        patient_filter: dict = {
            "metadata.pending_followup_date": {"$ne": None}
        }
        if doctor_scope_id:
            patient_filter["personal.assigned_doctor_id"] = doctor_scope_id
        if patient_name:
            patient_filter["personal.name"] = {
                "$regex": re.escape(patient_name), "$options": "i"
            }
        elif state.get("patient_id"):
            patient_filter["_id"] = state["patient_id"]
        elif appointment_date:
            patient_filter["metadata.pending_followup_date"] = appointment_date

        if len(patient_filter) > 1:
            p_doc = await db["patients"].find_one(patient_filter, {"personal.name": 1})
            if p_doc:
                await db["patients"].update_one(
                    {"_id": p_doc["_id"]},
                    {"$set": {
                        "metadata.pending_followup_date": None,
                        "metadata.pending_followup_visit_id": None,
                    }},
                )
                cancelled.append(
                    f"Follow-up for {p_doc['personal']['name']} — cleared."
                )
                logger.info("followup_cleared_via_agent", patient_id=p_doc["_id"])
            else:
                not_found.append(
                    f"No pending follow-up found"
                    + (f" for {patient_name}" if patient_name else "")
                    + "."
                )

    if not cancelled and not not_found:
        return {
            "messages": [AIMessage(
                content="Please specify what to cancel: appointment date and/or patient name. "
                        "Example: 'Cancel appointment on 25 March for Ajay Varma'."
            )]
        }

    parts = cancelled + not_found
    reply = "✓ " + "\n".join(parts) if cancelled else "\n".join(parts)
    return {"messages": [AIMessage(content=reply)]}


# ─────────────────────────────────────────────────────────────
# QUERY HANDLER
# ─────────────────────────────────────────────────────────────

async def _handle_query(state: AgentState, db, message: str) -> dict:
    today = date.today()

    # Doctor scoping — restrict all results to this doctor's patients only
    is_doctor = state.get("staff_role") == "doctor"
    doctor_scope_id = state.get("staff_id") if is_doctor else None

    try:
        extract_response = await _llm.ainvoke([
            SystemMessage(content=CALENDAR_EXTRACT_PROMPT.format(
                message=message, today=today.isoformat()
            )),
        ])
        params = json.loads(extract_response.content.strip())
    except Exception as e:
        logger.warning("calendar_extract_error", error=str(e))
        params = {
            "date_start": today.isoformat(), "date_end": today.isoformat(),
            "patient_name": None, "doctor_name": None,
            "event_type": "both", "show_slots": False,
        }

    date_start = params.get("date_start") or today.isoformat()
    date_end = params.get("date_end") or date_start
    if date_end < date_start:
        date_end = date_start

    try:
        date_end_exclusive = (date.fromisoformat(date_end) + timedelta(days=1)).isoformat()
    except Exception:
        date_end_exclusive = date_end

    patient_name_filter = params.get("patient_name")
    doctor_name_filter = params.get("doctor_name")
    event_type = params.get("event_type", "both")
    show_slots = params.get("show_slots", False)

    # Structured rows for markdown table
    rows: list[dict] = []
    capacity_notes: list[str] = []

    # ── Appointments ──────────────────────────────────────────
    if event_type in ("appointment", "both"):
        appt_filter: dict = {
            "appointment_date": {"$gte": date_start, "$lt": date_end_exclusive},
            "status": {"$ne": "cancelled"},
        }
        if doctor_scope_id:
            appt_filter["doctor_id"] = doctor_scope_id
        if patient_name_filter:
            appt_filter["patient_name"] = {
                "$regex": re.escape(patient_name_filter), "$options": "i"
            }
        if doctor_name_filter and not doctor_scope_id:
            appt_filter["doctor_name"] = {
                "$regex": re.escape(doctor_name_filter), "$options": "i"
            }

        date_doctor_counts: dict[str, dict[str, int]] = {}
        cursor = db["appointments"].find(appt_filter).sort("appointment_date", 1)
        async for doc in cursor:
            appt_date = doc.get("appointment_date", "?")
            doc_id = doc.get("doctor_id", "unknown")
            if appt_date not in date_doctor_counts:
                date_doctor_counts[appt_date] = {}
            date_doctor_counts[appt_date][doc_id] = \
                date_doctor_counts[appt_date].get(doc_id, 0) + 1

            rows.append({
                "type": "Appointment",
                "patient": doc.get("patient_name", "Unknown"),
                "date": appt_date,
                "time": doc.get("appointment_slot") or "—",
                "doctor": doc.get("doctor_name") or "Not assigned",
                "status": doc.get("status", "scheduled").capitalize(),
            })

        # Capacity notes if slots requested
        if show_slots and date_doctor_counts:
            for d, docs in sorted(date_doctor_counts.items()):
                for doc_id, count in docs.items():
                    doc_user = await db["users"].find_one({"_id": doc_id}, {"name": 1})
                    doc_display = doc_user["name"] if doc_user else doc_id
                    remaining = MAX_PATIENTS_PER_DAY - count
                    booked = [
                        doc2.get("appointment_slot", "")
                        async for doc2 in db["appointments"].find(
                            {"appointment_date": d, "doctor_id": doc_id,
                             "status": {"$ne": "cancelled"}},
                            {"appointment_slot": 1}
                        )
                    ]
                    avail = [s for s in CLINIC_SLOTS if s not in booked]
                    avail_preview = ", ".join(avail[:4]) + ("…" if len(avail) > 4 else "")
                    capacity_notes.append(
                        f"**{d}** — {doc_display}: {count}/{MAX_PATIENTS_PER_DAY} booked, "
                        f"{remaining} slots remaining. Available: {avail_preview}"
                    )

    # ── Follow-ups ────────────────────────────────────────────
    if event_type in ("followup", "both"):
        followup_filter: dict = {
            "metadata.pending_followup_date": {
                "$gte": date_start, "$lt": date_end_exclusive,
                "$ne": None,
            },
        }
        if doctor_scope_id:
            followup_filter["personal.assigned_doctor_id"] = doctor_scope_id
        if patient_name_filter:
            followup_filter["personal.name"] = {
                "$regex": re.escape(patient_name_filter), "$options": "i"
            }

        doctor_name_cache: dict[str, str] = {}
        patient_cursor = db["patients"].find(
            followup_filter,
            {"_id": 1, "personal.name": 1, "personal.assigned_doctor_id": 1,
             "metadata.pending_followup_date": 1},
        ).sort("metadata.pending_followup_date", 1)

        async for p in patient_cursor:
            doc_id = p.get("personal", {}).get("assigned_doctor_id")
            if doc_id and doc_id not in doctor_name_cache:
                doc_user = await db["users"].find_one({"_id": doc_id}, {"name": 1})
                doctor_name_cache[doc_id] = doc_user["name"] if doc_user else doc_id

            if doctor_name_filter and doctor_name_cache.get(doc_id, "").lower() not in doctor_name_filter.lower():
                continue

            followup_date = p.get("metadata", {}).get("pending_followup_date")
            if hasattr(followup_date, "isoformat"):
                followup_date = followup_date.isoformat()
            else:
                followup_date = str(followup_date)[:10] if followup_date else "?"

            rows.append({
                "type": "Follow-up",
                "patient": p.get("personal", {}).get("name", "Unknown"),
                "date": followup_date,
                "time": "—",
                "doctor": doctor_name_cache.get(doc_id, "Not assigned"),
                "status": "Pending",
            })

    # Sort all rows by date then time
    rows.sort(key=lambda r: (r["date"], r["time"]))

    logger.info("calendar_query_complete",
                date_start=date_start, date_end=date_end, results=len(rows))

    period = f"on {date_start}" if date_start == date_end else f"from {date_start} to {date_end}"

    if not rows:
        reply = f"No appointments or follow-ups found {period}."
    else:
        count = len(rows)
        header = f"**Schedule {period} — {count} event{'s' if count != 1 else ''}**\n"

        # Markdown table
        table = (
            "| Type | Patient | Date | Time | Doctor | Status |\n"
            "|------|---------|------|------|--------|--------|\n"
        )
        for r in rows:
            table += (
                f"| {r['type']} | {r['patient']} | {r['date']} "
                f"| {r['time']} | {r['doctor']} | {r['status']} |\n"
            )

        reply = header + "\n" + table
        if capacity_notes:
            reply += "\n**Capacity:**\n" + "\n".join(f"- {n}" for n in capacity_notes)

    return {"messages": [AIMessage(content=reply)], "current_agent": "CALENDAR"}
