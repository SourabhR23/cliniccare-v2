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
import asyncio
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

from backend.agents.state import AgentState
from backend.core.config import get_settings
from backend.core.llm import make_chat_llm

logger = structlog.get_logger(__name__)
settings = get_settings()

_llm = make_chat_llm(temperature=0)

# ─────────────────────────────────────────────────────────────
# NODE 1 — IDENTIFY PATIENT
# ─────────────────────────────────────────────────────────────

IDENTIFY_PROMPT = """You are a friendly clinic receptionist. The staff member wants to check in or find a patient.

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
    """
    # Patient already checked in — skip re-search for info queries.
    # Do NOT short-circuit if the message is a register/new-patient request,
    # as that indicates a different patient is being registered.
    last_msg_content = state["messages"][-1].content.lower() if state.get("messages") else ""
    is_registration_request = any(
        kw in last_msg_content for kw in ("register", "new patient", "add patient", "no, search", "search again")
    )
    if state.get("patient_id") and state.get("is_new_patient") is False and not is_registration_request:
        return {
            "is_new_patient": False,
            "patient_id": state.get("patient_id"),
            "patient_name": state.get("patient_name"),
        }

    # Clear stale patient state when starting a fresh search
    if is_registration_request:
        # Reset so we don't carry over a previous patient's data
        state = {**state, "patient_id": None, "patient_name": None, "is_new_patient": None}

    llm_with_tools = _llm.bind_tools(tools)

    response = await llm_with_tools.ainvoke([
        SystemMessage(content=IDENTIFY_PROMPT),
        *state["messages"],
    ])

    if response.tool_calls:
        # Execute all tool calls (usually just search_patients)
        tool_results = []
        for call in response.tool_calls:
            tool_fn = next((t for t in tools if t.name == call["name"]), None)
            if tool_fn:
                result = await tool_fn.ainvoke(call["args"])
                tool_results.append(ToolMessage(
                    content=json.dumps(result),
                    tool_call_id=call["id"],
                ))

        # Get LLM to interpret results and compose reply
        final_response = await llm_with_tools.ainvoke([
            SystemMessage(content=IDENTIFY_PROMPT),
            *state["messages"],
            response,
            *tool_results,
        ])

        # Parse result to determine new vs returning
        content = final_response.content.lower()
        is_new = "no record" in content or "register" in content or "new patient" in content

        # Try to extract patient_id from tool results
        patient_id = None
        patient_name = None
        for tr in tool_results:
            try:
                data = json.loads(tr.content)
                results = data.get("results", [])
                if results and len(results) == 1:
                    patient_id = results[0]["id"]
                    patient_name = results[0]["name"]
            except Exception:
                pass

        return {
            "messages": [response, *tool_results, final_response],
            "is_new_patient": is_new,
            "patient_id": patient_id,
            "patient_name": patient_name,
        }

    # No tool call — LLM responded directly (shouldn't normally happen)
    return {
        "messages": [response],
        "is_new_patient": True,  # Safe default: try to register
    }


def route_after_identify(state: AgentState) -> str:
    """
    Conditional edge: new vs returning patient.
    """
    if state.get("is_new_patient"):
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

COLLECT_INFO_PROMPT = """You are registering a new patient at a clinic. Collect one field at a time.

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
        collected = _extract_fields_from_messages(state["messages"], collected)

    return {
        "messages": new_messages,
        "collected_fields": collected,
    }


def _extract_fields_from_messages(messages: list, existing: dict) -> dict:
    """Extract patient fields from the full conversation so far."""
    import re
    collected = dict(existing)
    full_text = " ".join(getattr(m, "content", "") for m in messages)

    # Phone: 10 consecutive digits
    if "phone" not in collected:
        phone_match = re.search(r"\b(\d{10})\b", full_text)
        if phone_match:
            collected["phone"] = phone_match.group(1)

    # Date of birth: YYYY-MM-DD
    if "date_of_birth" not in collected:
        date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", full_text)
        if date_match:
            collected["date_of_birth"] = date_match.group(1)

    # Sex: M/F/O mentioned as a word
    if "sex" not in collected:
        sex_match = re.search(r"\b(male|female|other|M|F|O)\b", full_text, re.IGNORECASE)
        if sex_match:
            raw = sex_match.group(1).lower()
            collected["sex"] = "M" if raw in ("m", "male") else "F" if raw in ("f", "female") else "O"

    return collected


# ─────────────────────────────────────────────────────────────
# NODE 3 — VALIDATE INFO
# ─────────────────────────────────────────────────────────────

REQUIRED_FIELDS = ["full_name", "date_of_birth", "sex", "phone", "assigned_doctor_id"]


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

async def register_patient(state: AgentState, tools: list) -> dict:
    """
    Calls create_patient tool with collected fields.
    Handles duplicate phone → treat as returning patient.
    """
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

    if result.get("error_type") == "duplicate":
        # Phone already exists — this is a returning patient
        logger.info("receptionist_duplicate_phone_found", phone=collected.get("phone"))
        return {
            "messages": [AIMessage(
                content=(
                    f"A patient with this phone number already exists. "
                    f"Looking up their record now..."
                )
            )],
            "is_new_patient": False,
            "intent": "lookup_existing",
        }

    if "error" in result:
        return {
            "messages": [AIMessage(content=f"Registration failed: {result['error']}")],
            "error": result["error"],
        }

    return {
        "messages": [AIMessage(
            content=(
                f"✓ **{result['name']}** registered successfully!\n\n"
                f"Patient ID: `{result['patient_id']}`\n\n"
                f"Would you like to book an appointment for **{result['name']}** now? "
                f"If yes, please tell me the preferred date — for example: "
                f"'Yes, book on 25 March' or 'Book appointment next Monday'."
            )
        )],
        "patient_id": result["patient_id"],
        "patient_name": result["name"],
        "is_new_patient": False,
    }
