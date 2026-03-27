"""
backend/agents/receptionist_agent.py

RECEPTIONIST AGENT — Patient intake and registration.

GRAPH TYPE: Sequential with conditional branch
  identify_patient
    ├── [returning] → fetch_patient_record → confirm_details → END
    └── [new]       → collect_info → validate_info → register_patient → END

RESPONSIBILITIES:
  - Identify if patient is new or returning
  - For returning: fetch and confirm their record
  - For new: collect all required fields, validate, register

FALLBACKS:
  - Multiple search matches → list top 3, ask user to pick
  - Duplicate phone on register → treat as returning patient
  - 3 consecutive validation failures → escalate to human
  - Name/phone extraction fails → ask user directly

TOOLS USED:
  search_patients, get_patient, get_doctors_list, create_patient
"""

import json
import structlog
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage, HumanMessage

from backend.agents.state import AgentState
from backend.core.config import get_settings
from backend.core.llm import make_chat_llm

logger = structlog.get_logger(__name__)
settings = get_settings()

_llm = make_chat_llm(temperature=0)

# ─────────────────────────────────────────────────────────────
# NODE 1 — IDENTIFY PATIENT
# ─────────────────────────────────────────────────────────────

IDENTIFY_PROMPT = """You are a warm, friendly clinic receptionist. Speak naturally and concisely — like a real person, not a robot. Be polite and helpful.

The staff member wants to check in or find a patient.

If the message says "search again", "different patient", or similar — ask: "Sure! What's the patient's name or phone number?"
Do NOT call any tool in that case.

Otherwise, extract the patient name and/or phone number from the message and search using the search_patients tool.

After searching:
- If 1 exact match: say "Found **[Name]** — last visit [date], assigned to [doctor]. Shall I proceed with this patient?"
- If multiple matches: list the top 3 and ask "Which patient did you mean?"
- If no match: say "I couldn't find '[query]' in the system. Would you like to register them as a new patient?"

Always use the search_patients tool before concluding new vs returning."""


async def identify_patient(state: AgentState, tools: list) -> dict:
    """
    Searches MongoDB for the patient. Sets is_new_patient in state.
    Short-circuits if patient is already known (e.g. info queries after check-in).

    When patient is not found → fetches doctors list and returns registration_form UI.
    When __REGISTER__:{json} arrives → parses form data and routes to validate_info.
    """
    last_msg = state["messages"][-1].content if state.get("messages") else ""
    last_msg_lower = last_msg.lower()

    # ── Form submission from registration flashcard ─────────
    if last_msg.startswith("__REGISTER__:"):
        try:
            form_data = json.loads(last_msg[len("__REGISTER__:"):])
            logger.info("receptionist_form_submitted", name=form_data.get("full_name"))
            return {
                "collected_fields": form_data,
                "is_new_patient": True,
                "intent": "form_submitted",
                "registration_attempts": 0,
            }
        except Exception as e:
            logger.warning("receptionist_form_parse_error", error=str(e))

    # ── Detect search-reset requests ────────────────────────
    is_registration_request = any(
        kw in last_msg_lower for kw in ("register", "new patient", "add patient", "no, search", "search again")
    )

    # Short-circuit: returning patient already known
    if state.get("patient_id") and state.get("is_new_patient") is False and not is_registration_request:
        return {
            "is_new_patient": False,
            "patient_id": state.get("patient_id"),
            "patient_name": state.get("patient_name"),
        }

    # Reset stale patient state for fresh search — preserve appointment_date for post-registration booking
    if is_registration_request:
        state = {**state, "patient_id": None, "patient_name": None, "is_new_patient": None,
                 "registration_attempts": 0, "collected_fields": {},
                 "appointment_date": state.get("appointment_date"),
                 "appointment_slot": state.get("appointment_slot")}

    # ── LLM-based patient search ────────────────────────────
    llm_with_tools = _llm.bind_tools(tools)
    response = await llm_with_tools.ainvoke([
        SystemMessage(content=IDENTIFY_PROMPT),
        *state["messages"],
    ])

    if not response.tool_calls:
        # LLM asked a clarifying question — wait for next user input
        return {
            "messages": [response],
            "is_new_patient": None,
        }

    # Execute tool calls (usually search_patients)
    tool_results = []
    search_query = None
    for call in response.tool_calls:
        tool_fn = next((t for t in tools if t.name == call["name"]), None)
        if tool_fn:
            result = await tool_fn.ainvoke(call["args"])
            tool_results.append(ToolMessage(
                content=json.dumps(result),
                tool_call_id=call["id"],
            ))
            if call["name"] == "search_patients":
                search_query = call["args"].get("query", "")
        else:
            # Always pair tool_calls with ToolMessages to prevent 400 API errors
            tool_results.append(ToolMessage(
                content=json.dumps({"error": "tool not available"}),
                tool_call_id=call["id"],
            ))

    # Collect all search results
    all_results = []
    for tr in tool_results:
        try:
            data = json.loads(tr.content)
            all_results.extend(data.get("results", []))
        except Exception:
            pass

    # ── Patient NOT found → show registration form ──────────
    if len(all_results) == 0 and search_query:
        doctors_fn = next((t for t in tools if t.name == "get_doctors_list"), None)
        doctors = []
        if doctors_fn:
            try:
                dr_result = await doctors_fn.ainvoke({})
                doctors = dr_result.get("doctors", [])
            except Exception:
                pass

        ui_payload = json.dumps({
            "type": "registration_form",
            "patient_name": search_query,
            "message": (
                f"I've checked our records — **{search_query}** isn't registered in the system. "
                f"Please fill in the details below to register them as a new patient."
            ),
            "doctors": doctors,
        })
        return {
            "messages": [
                response,
                *tool_results,
                AIMessage(content=f"__AGENT_UI__:{ui_payload}"),
            ],
            "is_new_patient": None,
            "patient_name": search_query,
            "patient_id": None,
        }

    # ── Get LLM to interpret results (1 match or multiple) ──
    final_response = await llm_with_tools.ainvoke([
        SystemMessage(content=IDENTIFY_PROMPT),
        *state["messages"],
        response,
        *tool_results,
    ])

    # Exactly 1 result → returning patient
    patient_id = None
    patient_name = None
    if len(all_results) == 1:
        patient_id = all_results[0]["id"]
        patient_name = all_results[0]["name"]
        is_new = False
    else:
        is_new = len(all_results) == 0

    return {
        "messages": [response, *tool_results, final_response],
        "is_new_patient": is_new,
        "patient_id": patient_id,
        "patient_name": patient_name,
    }


def route_after_identify(state: AgentState) -> str:
    """
    Conditional edge: new vs returning patient.
    - form_submitted intent → skip collect_info, go straight to validate_info
    - is_new_patient=None → LLM asked a clarifying question, end turn and wait
    - is_new_patient=True → collect_info (fallback if form not used)
    - is_new_patient=False → fetch_patient_record
    """
    if state.get("intent") == "form_submitted":
        return "validate_info"
    is_new = state.get("is_new_patient")
    if is_new is None:
        return "__end__"
    if is_new:
        return "collect_info"
    return "fetch_patient_record"


# ─────────────────────────────────────────────────────────────
# NODE 2a — FETCH PATIENT RECORD (returning)
# ─────────────────────────────────────────────────────────────

async def fetch_patient_record(state: AgentState, tools: list) -> dict:
    """
    For returning patients: fetch full record and present to staff.
    """
    patient_id = state.get("patient_id")
    if not patient_id:
        return {
            "messages": [AIMessage(content="Patient ID not found. Please search again.")],
            "error": "missing_patient_id",
        }

    tool_fn = next((t for t in tools if t.name == "get_patient"), None)
    if not tool_fn:
        return {"messages": [AIMessage(content="Patient lookup unavailable.")]}

    patient_data = await tool_fn.ainvoke({"patient_id": patient_id})

    if "error" in patient_data:
        return {
            "messages": [AIMessage(content=f"Could not find patient: {patient_data['error']}")],
            "error": patient_data["error"],
        }

    # Format summary for staff
    allergies = ", ".join(patient_data.get("known_allergies", [])) or "None on record"
    conditions = ", ".join(patient_data.get("chronic_conditions", [])) or "None on record"
    doctor_display = (
        patient_data.get("assigned_doctor_name")
        or patient_data.get("assigned_doctor_id")
        or "Not assigned"
    )

    summary = (
        f"✓ Returning patient confirmed:\n\n"
        f"**{patient_data['name']}** | Age: {patient_data['age']} | {patient_data['sex']}\n"
        f"Phone: {patient_data['phone']}\n"
        f"Doctor: {doctor_display}\n"
        f"Last visit: {patient_data.get('last_visit_date', 'No visits yet')}\n"
        f"Total visits: {patient_data.get('total_visits', 0)}\n"
        f"Known allergies: {allergies}\n"
        f"Chronic conditions: {conditions}"
    )

    return {
        "messages": [AIMessage(content=summary)],
        "patient_id": patient_id,
        "patient_name": patient_data["name"],
        "patient_email": patient_data.get("email"),
        "patient_phone": patient_data.get("phone"),
        "assigned_doctor_id": patient_data.get("assigned_doctor_id"),
        "assigned_doctor_name": patient_data.get("assigned_doctor_name"),
        "pending_followup_date": patient_data.get("pending_followup_date"),
    }


# ─────────────────────────────────────────────────────────────
# NODE 2b — COLLECT INFO (new patient)
# ─────────────────────────────────────────────────────────────

COLLECT_INFO_PROMPT = """You are a warm, friendly clinic receptionist registering a new patient. Be conversational and polite — ask for one piece of information at a time, naturally. Never ask multiple questions at once.

Required fields (in order):
  1. full_name — patient's full name
  2. date_of_birth — format YYYY-MM-DD (ask as "date of birth" in natural language)
  3. sex — M, F, or O
  4. phone — 10-digit mobile number
  5. assigned_doctor_id — use get_doctors_list tool to show available doctors and let the user pick

Optional (ask only after required fields are done):
  - email
  - address

RULES:
- Ask for EXACTLY ONE missing field at a time. Do not ask multiple fields together.
- If the user provides a correction ("actually it's X" / "sorry, wrong number"), accept it gracefully and move on.
- When asking about the doctor, ALWAYS call get_doctors_list tool first, then present the list.
- Once all required fields are collected, say EXACTLY: "All information collected. Ready to register."

Current collected fields: {collected_fields}"""


async def collect_info(state: AgentState, tools: list) -> dict:
    """
    Multi-turn information collection for new patient registration.
    Runs until all required fields are collected.
    """
    collected = state.get("collected_fields", {})
    llm_with_tools = _llm.bind_tools(tools)

    prompt = COLLECT_INFO_PROMPT.format(
        collected_fields=json.dumps(collected, indent=2)
    )

    response = await llm_with_tools.ainvoke([
        SystemMessage(content=prompt),
        *state["messages"],
    ])

    new_messages = [response]

    if response.tool_calls:
        tool_results = []
        for call in response.tool_calls:
            tool_fn = next((t for t in tools if t.name == call["name"]), None)
            if tool_fn:
                result = await tool_fn.ainvoke(call["args"])
                tool_results.append(ToolMessage(
                    content=json.dumps(result),
                    tool_call_id=call["id"],
                ))

        # Let LLM continue after tool results
        followup = await llm_with_tools.ainvoke([
            SystemMessage(content=prompt),
            *state["messages"],
            response,
            *tool_results,
        ])
        new_messages = [response, *tool_results, followup]

        # Try extracting any newly mentioned fields from conversation
        collected = await _extract_fields_with_llm(state["messages"], collected)

    return {
        "messages": new_messages,
        "collected_fields": collected,
    }


_EXTRACT_FIELDS_PROMPT = """Extract patient registration fields from this clinic conversation transcript.

{conversation}

Extract any fields that have been clearly provided by the staff or patient:
- full_name: Patient's complete name
- date_of_birth: ISO format YYYY-MM-DD (convert natural dates — "5th Jan 1990" → "1990-01-05", "March 15 1985" → "1985-03-15")
- sex: M, F, or O (convert "male"→M, "female"→F, "other"→O)
- phone: 10-digit number only (strip spaces, dashes, country codes)
- email: email address if mentioned
- address: street/home address if mentioned

Corrections override earlier values — if user says "actually it's X", use X.
Return ONLY a JSON object with the extracted fields. Omit any field not yet provided.
Example: {{"full_name": "Riya Shah", "sex": "F", "phone": "9876543210"}}"""


async def _extract_fields_with_llm(messages: list, existing: dict) -> dict:
    """
    LLM-based field extraction from conversation history.
    Handles natural language dates, corrections, and varied formats.
    Falls back to regex on LLM failure.
    """
    import re, json as _json

    # Build a readable transcript, skipping UI payloads
    lines = []
    for m in messages:
        content = getattr(m, "content", "")
        if not content or content.startswith("__"):
            continue
        role = "Staff" if isinstance(m, HumanMessage) else "Assistant"
        lines.append(f"{role}: {content[:300]}")

    if not lines:
        return existing

    conversation = "\n".join(lines[-20:])
    try:
        response = await _llm.ainvoke([
            SystemMessage(content=_EXTRACT_FIELDS_PROMPT.format(conversation=conversation))
        ])
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        extracted = _json.loads(raw)
        # Merge: LLM-extracted values override existing (handles corrections)
        merged = dict(existing)
        merged.update(extracted)
        return merged
    except Exception:
        pass

    # ── Regex fallback ─────────────────────────────────────────
    collected = dict(existing)
    full_text = " ".join(getattr(m, "content", "") for m in messages)

    if "phone" not in collected:
        phone_match = re.search(r"\b(\d{10})\b", full_text)
        if phone_match:
            collected["phone"] = phone_match.group(1)

    if "date_of_birth" not in collected:
        date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", full_text)
        if date_match:
            collected["date_of_birth"] = date_match.group(1)

    if "sex" not in collected:
        sex_match = re.search(r"\b(male|female|other|M|F|O)\b", full_text, re.IGNORECASE)
        if sex_match:
            raw = sex_match.group(1).lower()
            collected["sex"] = "M" if raw in ("m", "male") else "F" if raw in ("f", "female") else "O"

    return collected


# ─────────────────────────────────────────────────────────────
# NODE 3 — VALIDATE INFO
# ─────────────────────────────────────────────────────────────

REQUIRED_FIELDS = ["full_name", "date_of_birth", "sex", "phone", "email", "assigned_doctor_id"]


async def validate_info(state: AgentState, tools: list) -> dict:
    """
    Validates all required fields before attempting registration.
    Routes back to collect_info if fields are missing.
    """
    collected = state.get("collected_fields", {})
    attempts = state.get("registration_attempts", 0)

    # Too many failures — escalate
    if attempts >= 3:
        return {
            "messages": [AIMessage(
                content=(
                    "I've had difficulty collecting all required information. "
                    "Please have a colleague assist with this registration or "
                    "complete it manually in the patient portal."
                )
            )],
            "error": "max_validation_attempts",
            "intent": "escalate",
        }

    missing = [f for f in REQUIRED_FIELDS if not collected.get(f)]

    if missing:
        return {
            "messages": [AIMessage(
                content=f"Still need: {', '.join(missing)}. Let me ask for those."
            )],
            "registration_attempts": attempts + 1,
            "intent": "needs_more_info",
        }

    return {
        "registration_attempts": 0,
        "intent": "ready_to_register",
    }


def route_after_validate(state: AgentState) -> str:
    intent = state.get("intent", "")
    if intent == "ready_to_register":
        return "register_patient"
    if intent == "escalate":
        return "__end__"
    return "collect_info"  # Loop back


# ─────────────────────────────────────────────────────────────
# NODE 4 — REGISTER PATIENT
# ─────────────────────────────────────────────────────────────

async def register_patient(state: AgentState, tools: list, db=None) -> dict:
    """
    Calls create_patient tool with collected fields.
    Handles duplicate phone → treat as returning patient.
    If appointment_date is in state (from scheduling flow), directly returns slot_picker UI.
    """
    from backend.agents.scheduling_agent import CLINIC_SLOTS, _get_booked_slots, _find_next_available_day

    collected = state.get("collected_fields", {})
    tool_fn = next((t for t in tools if t.name == "create_patient"), None)

    if not tool_fn:
        return {
            "messages": [AIMessage(content="Patient registration tool unavailable.")],
            "error": "missing_tool",
        }

    result = await tool_fn.ainvoke({
        "name": collected.get("full_name", ""),
        "date_of_birth": collected.get("date_of_birth", ""),
        "sex": collected.get("sex", "O"),
        "phone": collected.get("phone", ""),
        "assigned_doctor_id": collected.get("assigned_doctor_id", ""),
        "email": collected.get("email"),
        "address": collected.get("address"),
        "emergency_contact": collected.get("emergency_contact"),
    })

    if "error" in result:
        return {
            "messages": [AIMessage(content=f"Registration failed: {result['error']}")],
            "error": result["error"],
        }

    patient_id = result["patient_id"]
    patient_name = result["name"]
    patient_email = collected.get("email")
    assigned_doctor_id = result.get("assigned_doctor_id", collected.get("assigned_doctor_id"))
    assigned_doctor_name = result.get("assigned_doctor_name", "")

    # ── If appointment_date was remembered from scheduling flow → show slot_picker directly ──
    pending_date = state.get("appointment_date")
    if pending_date and assigned_doctor_id and db is not None:
        appointment_date = pending_date
        try:
            booked = await _get_booked_slots(db, assigned_doctor_id, appointment_date)
            available_slots = [s for s in CLINIC_SLOTS if s not in booked]
            if not available_slots:
                next_day, next_slots = await _find_next_available_day(db, assigned_doctor_id, appointment_date)
                if next_day:
                    available_slots = next_slots
                    appointment_date = next_day
        except Exception:
            available_slots = CLINIC_SLOTS[:]

        ui_payload = json.dumps({
            "type": "slot_picker",
            "patient_name": patient_name,
            "patient_id": str(patient_id),
            "doctor_name": assigned_doctor_name,
            "doctor_id": str(assigned_doctor_id),
            "appointment_date": appointment_date,
            "slots": available_slots,
            "reason": "",
            "registration_success": True,
        })
        logger.info("receptionist_post_register_slot_picker",
                    patient_id=patient_id, date=appointment_date)
        return {
            "messages": [AIMessage(content=f"__AGENT_UI__:{ui_payload}")],
            "patient_id": patient_id,
            "patient_name": patient_name,
            "patient_email": patient_email,
            "assigned_doctor_id": assigned_doctor_id,
            "assigned_doctor_name": assigned_doctor_name,
            "appointment_date": appointment_date,
            "is_new_patient": False,
        }

    # ── No pending date → ask for appointment date ──────────
    return {
        "messages": [AIMessage(
            content=(
                f"**{patient_name}** has been registered successfully.\n\n"
                f"Patient ID: `{patient_id}`\n\n"
                f"Would you like to book an appointment? "
                f"If yes, tell me the preferred date — for example: 'Book on 25 March'."
            )
        )],
        "patient_id": patient_id,
        "patient_name": patient_name,
        "patient_email": patient_email,
        "assigned_doctor_id": assigned_doctor_id,
        "assigned_doctor_name": assigned_doctor_name,
        "is_new_patient": False,
    }
