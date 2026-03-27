"""
backend/agents/scheduling_agent.py — Long-running appointment workflow with interrupt()
"""

import structlog
import aiosmtplib
from email.mime.text import MIMEText
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.types import interrupt

from backend.agents.state import AgentState
from backend.core.config import get_settings
from backend.core.llm import make_chat_llm
from backend.agents.notification_agent import compose_email, send_email

logger = structlog.get_logger(__name__)
settings = get_settings()

_llm = make_chat_llm(temperature=0)

MAX_RESCHEDULE_ATTEMPTS = 3

# ── Clinic scheduling rules ────────────────────────────────
CLINIC_SLOTS = [
    "09:00 AM", "09:30 AM", "10:00 AM", "10:30 AM", "11:00 AM", "11:30 AM",
    "02:00 PM", "02:30 PM", "03:00 PM", "03:30 PM", "04:00 PM", "04:30 PM",
]
MAX_PATIENTS_PER_DAY = 10


async def _get_booked_slots(db, doctor_id: str, appointment_date: str) -> list[str]:
    """Return list of booked slot strings for a doctor on a given date."""
    cursor = db["appointments"].find(
        {
            "doctor_id": doctor_id,
            "appointment_date": appointment_date,
            "status": {"$ne": "cancelled"},
        },
        {"appointment_slot": 1},
    )
    return [doc["appointment_slot"] for doc in await cursor.to_list(None)
            if doc.get("appointment_slot")]


async def _find_next_available_day(db, doctor_id: str, from_date: str) -> tuple[str, list[str]]:
    """
    Starting from from_date, find the next date where doctor has < MAX_PATIENTS_PER_DAY.
    Returns (date_str, available_slots).
    Checks up to 14 days ahead.
    """
    from datetime import date, timedelta
    start = date.fromisoformat(from_date)
    for i in range(1, 15):
        check = (start + timedelta(days=i)).isoformat()
        booked = await _get_booked_slots(db, doctor_id, check)
        if len(booked) < MAX_PATIENTS_PER_DAY:
            available = [s for s in CLINIC_SLOTS if s not in booked]
            return check, available
    return "", []


CLASSIFY_PROMPT = """Classify the patient reply to an appointment confirmation request.

Reply: "{reply}"

Return ONLY one word: confirmed | declined | unclear"""


EXTRACT_DETAILS_PROMPT = """Extract appointment scheduling details from the staff message.

Staff message: "{message}"
Patient name already known: {patient_name}
Previous assistant question: {previous_question}
Today's date: {today}

Extract:
1. appointment_date: Convert to ISO format YYYY-MM-DD.
   Examples: "22 march 2026" → "2026-03-22", "25 march" → "2026-03-25", "tomorrow" → tomorrow's date,
   "next week" → next monday from today. If no date mentioned → null
2. appointment_slot: Time slot if mentioned (e.g. "10:30 AM", "2pm", "morning", "09:00 AM"). If not mentioned → null
3. followup_reason: Reason/purpose if mentioned. If not mentioned → null
4. patient_name_in_message: Patient name mentioned in the message.
   IMPORTANT: If "previous_assistant_question" asked for the patient's name AND the current message looks like a
   name (e.g. "Akshay Kumar", "Patient name: Riya Shah", "The patient is John Doe"), extract that as the name.
   Examples: "Ajay Varma" → "Ajay Varma", "Patient name: Riya" → "Riya", "It's Akshay Kumar" → "Akshay Kumar".
   If none → null
5. doctor_name_in_message: Doctor name mentioned in the message (e.g. "Dr. Rohan", "doctor rohan", "Dr Mehta"). If none → null
6. is_reschedule: true if message indicates reschedule/change/move/shift an existing appointment. false otherwise.
7. old_appointment_date: Only if is_reschedule=true and an existing/old date is mentioned. ISO format. null otherwise.

Respond ONLY with valid JSON:
{{"appointment_date": "2026-03-25", "appointment_slot": null, "followup_reason": null, "patient_name_in_message": "Ajay Varma", "doctor_name_in_message": "Dr. Rohan", "is_reschedule": false, "old_appointment_date": null}}"""


async def extract_appointment_details(state: AgentState, db) -> dict:
    """
    1. Use LLM to extract appointment_date, slot, reason and patient name from message.
    2. If patient_id not in state, search DB for the patient by name extracted from message.
    3. Ask for any missing required info (date or patient).
    """
    import json, re
    from datetime import date

    # ── Reset stale scheduling fields when starting a new booking cycle ──
    # Prevents a previous booking's slot/date from leaking into the new request.
    is_new_booking_cycle = bool(state.get("booking_done"))
    if is_new_booking_cycle:
        state = {
            **state,
            "booking_done": False,
            "appointment_date": None,
            "appointment_slot": None,
            "followup_reason": None,
            "confirmation_status": None,
            "scheduling_retry_count": 0,
            "reminder_sent": False,
            "email_sent": False,
        }
        logger.info("scheduling_new_booking_cycle",
                    thread_id=state.get("thread_id"),
                    patient_name=state.get("patient_name"))

    last_message = ""
    previous_question = ""
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) and not last_message:
            last_message = msg.content
        elif isinstance(msg, AIMessage) and last_message and not previous_question:
            previous_question = msg.content[:200]
            break

    # ── Step 1: Extract details from message ─────────────────
    prompt = EXTRACT_DETAILS_PROMPT.format(
        message=last_message,
        patient_name=state.get("patient_name") or "not known yet",
        previous_question=previous_question or "none",
        today=date.today().isoformat(),
    )
    try:
        response = await _llm.ainvoke([SystemMessage(content=prompt)])
        raw = response.content.strip()
        # Strip markdown fences if LLM wraps JSON in ```
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        parsed = json.loads(raw)
        # Fall back to state values when message doesn't mention them explicitly
        # This handles "Yes, book" after registration (uses remembered appointment_date)
        appointment_date = parsed.get("appointment_date") or state.get("appointment_date")
        appointment_slot = parsed.get("appointment_slot") or state.get("appointment_slot")
        followup_reason = parsed.get("followup_reason")
        patient_name_in_msg = parsed.get("patient_name_in_message")
        doctor_name_in_msg = parsed.get("doctor_name_in_message")
        is_reschedule = bool(parsed.get("is_reschedule", False))
        old_appointment_date = parsed.get("old_appointment_date")
    except Exception as e:
        logger.warning("scheduling_extract_parse_error", error=str(e))
        appointment_date = state.get("appointment_date")
        appointment_slot = state.get("appointment_slot")
        followup_reason = None
        patient_name_in_msg = None
        doctor_name_in_msg = None
        is_reschedule = False
        old_appointment_date = None
        followup_reason = None
        patient_name_in_msg = None
        doctor_name_in_msg = None
        is_reschedule = False
        old_appointment_date = None

    # ── Step 2: Resolve patient from DB if not in state ──────
    patient_id = state.get("patient_id")
    patient_name = state.get("patient_name")
    patient_email = state.get("patient_email")
    assigned_doctor_id = state.get("assigned_doctor_id")
    assigned_doctor_name = state.get("assigned_doctor_name")
    doctor_name_in_msg = doctor_name_in_msg or None

    if not patient_id:
        lookup_name = patient_name_in_msg or patient_name
        if not lookup_name:
            action = "reschedule" if is_reschedule else "book"
            return {
                "messages": [AIMessage(
                    content=(
                        f"Sure, I'd be happy to help {action} an appointment! "
                        "Could I get the patient's name so I can look them up?"
                    )
                )],
                "intent": "abort",
            }

        # Search MongoDB for the patient
        escaped = re.escape(lookup_name)
        patient_doc = await db["patients"].find_one(
            {"personal.name": {"$regex": escaped, "$options": "i"}},
            {
                "_id": 1,
                "personal.name": 1,
                "personal.email": 1,
                "personal.assigned_doctor_id": 1,
            },
        )

        if not patient_doc:
            import json as _json
            # Fetch doctors for the registration form
            doctors = []
            try:
                cursor = db["users"].find(
                    {"role": "doctor", "is_active": True},
                    {"_id": 1, "name": 1, "specialization": 1}
                )
                doctor_docs = await cursor.to_list(length=50)
                doctors = [{"id": d["_id"], "name": d["name"], "specialization": d.get("specialization")} for d in doctor_docs]
            except Exception:
                pass

            ui_payload = _json.dumps({
                "type": "registration_form",
                "patient_name": lookup_name,
                "message": (
                    f"I searched for **{lookup_name}** but couldn't find a match in our records. "
                    f"Please register them as a new patient and we'll book the appointment right after."
                ),
                "doctors": doctors,
            })
            # Save the intended appointment date so it's offered after registration
            return {
                "messages": [AIMessage(content=f"__AGENT_UI__:{ui_payload}")],
                "intent": "abort",
                "appointment_date": appointment_date,  # Preserved for post-registration booking
                "appointment_slot": appointment_slot,
            }

        patient_id = patient_doc["_id"]
        patient_name = patient_doc["personal"]["name"]
        email_val = patient_doc["personal"].get("email")
        patient_email = str(email_val) if email_val else None
        assigned_doctor_id = patient_doc["personal"].get("assigned_doctor_id")

        # Try to find requested doctor by name first, then fall back to assigned
        if doctor_name_in_msg:
            doc_by_name = await db["users"].find_one(
                {
                    "name": {"$regex": re.escape(doctor_name_in_msg), "$options": "i"},
                    "role": "doctor",
                },
                {"_id": 1, "name": 1},
            )
            if doc_by_name:
                assigned_doctor_id = doc_by_name["_id"]
                assigned_doctor_name = doc_by_name["name"]
                logger.info("scheduling_doctor_resolved_by_name",
                            doctor=assigned_doctor_name)

        # Fetch doctor name if not yet resolved
        if assigned_doctor_id and not assigned_doctor_name:
            doc_user = await db["users"].find_one(
                {"_id": assigned_doctor_id}, {"name": 1}
            )
            if doc_user:
                assigned_doctor_name = doc_user["name"]

        logger.info("scheduling_patient_resolved",
                    patient_id=patient_id, patient_name=patient_name)

    # ── Step 2b: Override doctor if explicitly named in message ──
    # Handles "Book with Dr. X" when patient is already in state (e.g., from DoctorPicker)
    if patient_id and doctor_name_in_msg:
        doc_by_name = await db["users"].find_one(
            {
                "name": {"$regex": re.escape(doctor_name_in_msg), "$options": "i"},
                "role": "doctor",
            },
            {"_id": 1, "name": 1},
        )
        if doc_by_name:
            assigned_doctor_id = doc_by_name["_id"]
            assigned_doctor_name = doc_by_name["name"]
            logger.info("scheduling_doctor_overridden_by_name", doctor=assigned_doctor_name)

    # ── Step 3: Handle reschedule — find existing appointment ──
    if is_reschedule:
        # Look up existing appointment in DB
        query: dict = {"patient_id": patient_id, "status": {"$ne": "cancelled"}}
        if old_appointment_date:
            query["appointment_date"] = old_appointment_date
        existing_appt = await db["appointments"].find_one(
            query, sort=[("appointment_date", -1)]
        )
        if existing_appt and appointment_date:
            # Update appointment with new date (and clear slot so picker shows again)
            await db["appointments"].update_one(
                {"_id": existing_appt["_id"]},
                {"$set": {
                    "appointment_date": appointment_date,
                    "appointment_slot": appointment_slot or existing_appt.get("appointment_slot"),
                    "status": "scheduled",
                }}
            )
            logger.info("appointment_rescheduled",
                        appt_id=existing_appt["_id"],
                        old_date=existing_appt.get("appointment_date"),
                        new_date=appointment_date)
            import json as _json
            ui_payload = _json.dumps({
                "type": "booking_confirm",
                "appointment_id": existing_appt["_id"],
                "patient_name": patient_name,
                "doctor_name": assigned_doctor_name or existing_appt.get("doctor_name", ""),
                "appointment_date": appointment_date,
                "appointment_slot": appointment_slot or existing_appt.get("appointment_slot", ""),
                "reason": followup_reason or existing_appt.get("followup_reason") or "Follow-up",
                "patient_email": patient_email or "",
                "email_sent": False,
            })
            return {
                "messages": [AIMessage(content=f"__AGENT_UI__:{ui_payload}")],
                "intent": "dormant",
            }
        elif existing_appt and not appointment_date:
            old_date = existing_appt.get("appointment_date", "their current date")
            return {
                "messages": [AIMessage(
                    content=(
                        f"Got it — **{patient_name}** currently has an appointment on **{old_date}**. "
                        "What date would you like to move it to?"
                    )
                )],
                "patient_id": patient_id,
                "patient_name": patient_name,
                "patient_email": patient_email,
                "assigned_doctor_id": assigned_doctor_id,
                "assigned_doctor_name": assigned_doctor_name,
                "intent": "abort",
            }
        elif not existing_appt:
            return {
                "messages": [AIMessage(
                    content=(
                        f"I couldn't find an existing appointment for **{patient_name}** to reschedule. "
                        "Would you like to book a new one instead?"
                    )
                )],
                "intent": "abort",
            }

    # ── Step 4: Require appointment date ─────────────────────
    if not appointment_date:
        return {
            "messages": [AIMessage(
                content=(
                    f"Found them — **{patient_name}**. "
                    "What date works best for the appointment? "
                    "You can say something like 'on 25 March' or 'next Monday'."
                )
            )],
            "patient_id": patient_id,
            "patient_name": patient_name,
            "patient_email": patient_email,
            "assigned_doctor_id": assigned_doctor_id,
            "assigned_doctor_name": assigned_doctor_name,
            "followup_reason": followup_reason,
            "intent": "abort",
        }

    # ── Step 4a: Show doctor picker if no doctor was explicitly chosen ──
    # Show whenever no doctor is named in the current message and no slot yet.
    # The DoctorPicker and SlotPicker UIs both generate messages that include
    # the doctor name explicitly, so this step only fires once per booking attempt.
    if not doctor_name_in_msg and not appointment_slot:
        import json as _json
        doctors = []
        try:
            cursor = db["users"].find(
                {"role": "doctor", "is_active": True},
                {"_id": 1, "name": 1, "specialization": 1}
            )
            doctor_docs = await cursor.to_list(length=50)
            doctors = [{"id": d["_id"], "name": d["name"], "specialization": d.get("specialization")} for d in doctor_docs]
        except Exception:
            pass
        ui_payload = _json.dumps({
            "type": "doctor_picker",
            "patient_name": patient_name,
            "patient_id": str(patient_id),
            "appointment_date": appointment_date,
            "doctors": doctors,
        })
        logger.info("scheduling_showing_doctor_picker", patient_id=patient_id)
        return {
            "messages": [AIMessage(content=f"__AGENT_UI__:{ui_payload}")],
            "patient_id": patient_id,
            "patient_name": patient_name,
            "patient_email": patient_email,
            "assigned_doctor_id": assigned_doctor_id,
            "assigned_doctor_name": assigned_doctor_name,
            "appointment_date": appointment_date,
            "followup_reason": followup_reason,
            "intent": "abort",
        }

    # ── Step 4b: Show slot picker if no slot selected yet ────
    if not appointment_slot:
        # Fetch available slots from DB for this doctor + date
        booked = await _get_booked_slots(db, assigned_doctor_id or "", appointment_date) if assigned_doctor_id else []
        available_slots = [s for s in CLINIC_SLOTS if s not in booked]

        if not available_slots:
            next_day, next_slots = await _find_next_available_day(
                db, assigned_doctor_id or "", appointment_date
            ) if assigned_doctor_id else ("", [])
            if next_day and next_slots:
                available_slots = next_slots
                appointment_date = next_day

        import json as _json
        ui_payload = _json.dumps({
            "type": "slot_picker",
            "patient_name": patient_name,
            "patient_id": str(patient_id),
            "doctor_name": assigned_doctor_name or "Doctor",
            "doctor_id": str(assigned_doctor_id) if assigned_doctor_id else "",
            "appointment_date": appointment_date,
            "slots": available_slots,
            "reason": followup_reason or "",
        })
        logger.info("scheduling_showing_slot_picker",
                    patient_id=patient_id, date=appointment_date,
                    slots_available=len(available_slots))
        return {
            "messages": [AIMessage(content=f"__AGENT_UI__:{ui_payload}")],
            "patient_id": patient_id,
            "patient_name": patient_name,
            "patient_email": patient_email,
            "assigned_doctor_id": assigned_doctor_id,
            "assigned_doctor_name": assigned_doctor_name,
            "appointment_date": appointment_date,
            "followup_reason": followup_reason,
            "scheduling_retry_count": 0,
            "reminder_sent": False,
            "email_sent": False,
            "intent": "slot_selection",
        }

    logger.info("scheduling_details_extracted",
                patient_id=patient_id,
                appointment_date=appointment_date,
                appointment_slot=appointment_slot)

    return {
        "messages": [AIMessage(
            content=f"Got it — scheduling **{patient_name}** on **{appointment_date}**"
                    + (f" at {appointment_slot}" if appointment_slot else "")
                    + "."
        )],
        "appointment_date": appointment_date,
        "appointment_slot": appointment_slot,
        "followup_reason": followup_reason,
        "patient_id": patient_id,
        "patient_name": patient_name,
        "patient_email": patient_email,
        "assigned_doctor_id": assigned_doctor_id,
        "assigned_doctor_name": assigned_doctor_name,
        "scheduling_retry_count": 0,
        "reminder_sent": False,
        "email_sent": False,
        "booking_done": False,
        "intent": "continue",  # Clear any stale slot_selection/abort from previous turn
    }


async def check_slot_availability(state: AgentState, db) -> dict:
    appointment_date = state.get("appointment_date")
    appointment_slot = state.get("appointment_slot")
    doctor_id = state.get("assigned_doctor_id")
    doctor_name = state.get("assigned_doctor_name") or "the doctor"

    if not appointment_date or not doctor_id:
        return {"intent": "proceed"}

    booked_slots = await _get_booked_slots(db, doctor_id, appointment_date)

    # ── Check daily capacity (max 10 patients per doctor) ──────
    if len(booked_slots) >= MAX_PATIENTS_PER_DAY:
        next_day, next_slots = await _find_next_available_day(db, doctor_id, appointment_date)
        if next_day:
            slots_preview = ", ".join(next_slots[:4]) + ("..." if len(next_slots) > 4 else "")
            return {
                "messages": [AIMessage(
                    content=(
                        f"Looks like {doctor_name} is fully booked on {appointment_date} — "
                        f"all {MAX_PATIENTS_PER_DAY} slots are taken. "
                        f"The next available day is **{next_day}**, with slots at {slots_preview}. "
                        f"Shall I go ahead and book on {next_day}?"
                    )
                )],
                "intent": "slot_conflict",
            }
        return {
            "messages": [AIMessage(
                content=(
                    f"Unfortunately {doctor_name} has no availability in the next 14 days. "
                    "You may want to check with the clinic admin to arrange something."
                )
            )],
            "intent": "slot_conflict",
        }

    available_slots = [s for s in CLINIC_SLOTS if s not in booked_slots]

    # ── If no slot specified, assign first available ───────────
    if not appointment_slot:
        appointment_slot = available_slots[0] if available_slots else None
        if not appointment_slot:
            return {
                "messages": [AIMessage(
                    content=(
                        f"Sorry, there are no available slots on {appointment_date} for {doctor_name}. "
                        "Clinic hours are 9:00 AM – 5:00 PM. Would you like to try a different date?"
                    )
                )],
                "intent": "slot_conflict",
            }
        logger.info("scheduling_slot_auto_assigned",
                    slot=appointment_slot, date=appointment_date)
        return {
            "appointment_slot": appointment_slot,
            "intent": "proceed",
            "messages": [AIMessage(
                content=f"I've gone ahead and assigned the **{appointment_slot}** slot on {appointment_date} for {doctor_name}."
            )],
        }

    # ── Validate slot is within clinic hours ──────────────────
    if appointment_slot not in CLINIC_SLOTS:
        slots_preview = ", ".join(available_slots[:6])
        return {
            "messages": [AIMessage(
                content=(
                    f"Hmm, **{appointment_slot}** is outside our clinic hours (9:00 AM – 5:00 PM). "
                    f"Here are the available slots on {appointment_date}: {slots_preview}. "
                    "Which one works?"
                )
            )],
            "intent": "slot_conflict",
        }

    # ── Check specific slot is free ───────────────────────────
    if appointment_slot in booked_slots:
        alt_slots = ", ".join(available_slots[:4]) if available_slots else "none available"
        return {
            "messages": [AIMessage(
                content=(
                    f"Oh, **{appointment_slot}** on {appointment_date} is already taken. "
                    f"Other open slots that day: {alt_slots}. Which one would you prefer?"
                )
            )],
            "intent": "slot_conflict",
        }

    logger.info("scheduling_slot_available",
                slot=appointment_slot, date=appointment_date)
    return {
        "intent": "proceed",
        "messages": [AIMessage(content="")],
    }


def route_after_availability(state: AgentState) -> str:
    return "__end__" if state.get("intent") in ("slot_conflict", "abort") else "confirm_booking"


async def confirm_booking(state: AgentState, db) -> dict:
    import uuid, json as _json
    from datetime import datetime
    appt_id = "APT" + uuid.uuid4().hex[:8].upper()
    doc = {
        "_id": appt_id,
        "patient_id": state.get("patient_id"),
        "patient_name": state.get("patient_name"),
        "doctor_id": state.get("assigned_doctor_id"),
        "doctor_name": state.get("assigned_doctor_name"),
        "appointment_date": state.get("appointment_date"),
        "appointment_slot": state.get("appointment_slot"),
        "followup_reason": state.get("followup_reason"),
        "status": "scheduled",
        "scheduling_thread_id": state.get("thread_id"),
        "created_at": datetime.utcnow().isoformat(),
    }
    await db["appointments"].insert_one(doc)
    logger.info("appointment_booked", id=appt_id)
    conf_state = {**state, "email_type": "confirmation"}
    composed = await compose_email(conf_state)
    email_sent = False
    if composed.get("email_body"):
        result = await send_email({**conf_state, **composed})
        email_sent = result.get("email_sent", False)

    ui_payload = _json.dumps({
        "type": "booking_confirm",
        "appointment_id": appt_id,
        "patient_name": state.get("patient_name", ""),
        "doctor_name": state.get("assigned_doctor_name", ""),
        "appointment_date": state.get("appointment_date", ""),
        "appointment_slot": state.get("appointment_slot", ""),
        "reason": state.get("followup_reason") or "General Consultation",
        "patient_email": state.get("patient_email") or "",
        "email_sent": email_sent,
    })
    return {
        "messages": [AIMessage(content=f"__AGENT_UI__:{ui_payload}")],
        "intent": "dormant",
        "booking_done": True,
    }


async def send_reminder(state: AgentState) -> dict:
    if state.get("reminder_sent"):
        return {}
    reminder_state = {**state, "email_type": "reminder"}
    composed = await compose_email(reminder_state)
    email_sent = False
    if composed.get("email_body"):
        result = await send_email({**reminder_state, **composed})
        email_sent = result.get("email_sent", False)
    patient_name = state.get("patient_name") or "patient"
    patient_email = state.get("patient_email") or ""
    email_note = (
        f"A confirmation email has been sent to {patient_email}."
        if email_sent
        else "No email on file — confirmation not sent."
    )
    return {
        "reminder_sent": True,
        "email_sent": email_sent,
        "messages": [AIMessage(
            content=(
                f"All set! **{patient_name}** is booked for "
                f"**{state.get('appointment_date')}** at **{state.get('appointment_slot')}** "
                f"with {state.get('assigned_doctor_name') or 'the doctor'}. "
                f"{email_note}\n\n"
                f"Is there anything else — another appointment or a different patient?"
            )
        )],
    }


async def wait_for_confirmation(state: AgentState) -> dict:
    patient_reply = interrupt("Waiting for patient confirmation via webhook")
    logger.info("scheduling_patient_replied", thread_id=state.get("thread_id"))
    return {"messages": [HumanMessage(content=str(patient_reply))], "intent": "classify_reply"}


async def classify_response(state: AgentState) -> dict:
    last_message = state["messages"][-1].content
    response = await _llm.ainvoke([
        SystemMessage(content=CLASSIFY_PROMPT.format(reply=last_message)),
    ])
    classification = response.content.strip().lower()
    if classification not in ("confirmed", "declined", "unclear"):
        classification = "unclear"
    logger.info("scheduling_reply_classified", classification=classification)
    return {"confirmation_status": classification}


def route_after_classification(state: AgentState) -> str:
    status = state.get("confirmation_status")
    retries = state.get("scheduling_retry_count", 0)
    if status == "confirmed":
        return "send_confirmation_email"
    elif status == "declined" and retries < MAX_RESCHEDULE_ATTEMPTS:
        return "offer_alternatives"
    elif status == "declined":
        return "notify_doctor_of_decline"
    elif status == "unclear":
        return "ask_clarification"
    return "notify_doctor_of_decline"


async def send_confirmation_email(state: AgentState, db) -> dict:
    conf_state = {**state, "email_type": "confirmation"}
    composed = await compose_email(conf_state)
    if composed.get("email_body"):
        await send_email({**conf_state, **composed})
    await db["appointments"].update_one(
        {"scheduling_thread_id": state.get("thread_id")},
        {"$set": {"status": "confirmed"}},
    )
    return {
        "messages": [AIMessage(content=f"{state['patient_name']} confirmed their appointment.")],
        "confirmation_status": "confirmed",
    }


async def offer_alternatives(state: AgentState) -> dict:
    retries = state.get("scheduling_retry_count", 0)
    rescheduling_state = {**state, "email_type": "rescheduling"}
    composed = await compose_email(rescheduling_state)
    if composed.get("email_body"):
        await send_email({**rescheduling_state, **composed})
    new_reply = interrupt("Waiting for rescheduling preference")
    return {
        "messages": [HumanMessage(content=str(new_reply))],
        "scheduling_retry_count": retries + 1,
        "appointment_date": None,
        "intent": "classify_reply",
    }


async def ask_clarification(state: AgentState) -> dict:
    clear_reply = interrupt("Waiting for yes/no clarification")
    return {"messages": [HumanMessage(content=str(clear_reply))], "intent": "classify_reply"}


async def notify_doctor_of_decline(state: AgentState, db) -> dict:
    await db["appointments"].update_one(
        {"scheduling_thread_id": state.get("thread_id")},
        {"$set": {"status": "declined"}},
    )
    doctor_doc = None
    if state.get("assigned_doctor_id"):
        doctor_doc = await db["users"].find_one({"_id": state["assigned_doctor_id"]})
    if doctor_doc and doctor_doc.get("email"):
        try:
            msg = MIMEText(
                f"Patient {state.get('patient_name')} did not confirm their appointment "
                f"on {state.get('appointment_date')}. Please contact them directly.", "plain"
            )
            msg["Subject"] = "Action Required: Unconfirmed Appointment"
            msg["From"] = settings.smtp_from_email
            msg["To"] = doctor_doc["email"]
            use_implicit_tls = settings.smtp_port == 465
            async with aiosmtplib.SMTP(
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                use_tls=use_implicit_tls,
                start_tls=not use_implicit_tls,
            ) as smtp:
                await smtp.login(settings.smtp_username, settings.smtp_password)
                await smtp.send_message(msg)
        except Exception as e:
            logger.error("doctor_notification_failed", error=str(e))
    return {
        "messages": [AIMessage(
            content="Understood — appointment has been marked as declined and the doctor has been notified."
        )],
        "confirmation_status": "declined",
    }