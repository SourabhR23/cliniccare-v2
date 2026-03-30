"""
backend/agents/patient_booking_agent.py — Patient-facing appointment booking assistant.

Public-facing chatbot (no staff auth). Form-first flow (no conversational Q&A):
  - Existing patient → doctor + slot picker UI card (no LLM needed after find_patient)
  - New patient → registration + booking UI card (no LLM needed)
  - Both paths emit __AGENT_UI__ payloads handled by the frontend

Fast-path message handling (zero LLM calls):
  __PATIENT_BOOK__:{...}     — book appointment directly from slot picker form
  __PATIENT_REGISTER__:{...} — register + book from registration form

LLM calls only for:
  - Identity form parsing → find_patient tool
  - view_my_appointments requests
  - Conversation closing
"""

import json
import structlog
from datetime import date
from typing import Annotated

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from backend.core.llm import make_chat_llm

logger = structlog.get_logger(__name__)

_llm = make_chat_llm(temperature=0.3)

# Simplified prompt — most booking flows bypass LLM entirely via fast-path
SYSTEM_PROMPT = """You are ClinicCare's patient booking assistant.

Rules:
- When patient submits "Full Name: [name] | Phone: [phone]" → call find_patient
- When patient asks to view/check appointments → call view_my_appointments (use patient_id from context)
- Reschedule / cancel → redirect: "Please call the clinic or speak to our receptionist."
- When done → say a warm goodbye ending with "See you soon!" or "Goodbye!"

Today: {today}"""


class PatientChatState(TypedDict):
    messages: Annotated[list, add_messages]


# ─── Raw DB helpers (called directly in fast-path, without tool wrapper) ──────

async def _fetch_doctors(db) -> list:
    cursor = db["users"].find(
        {"role": "doctor", "is_active": True},
        {"_id": 1, "name": 1, "specialization": 1},
    )
    docs = await cursor.to_list(length=20)
    return [
        {
            "id": str(d["_id"]),
            "name": d["name"],
            "specialization": d.get("specialization") or "General Physician",
        }
        for d in docs
    ]


async def _book_appointment(
    db,
    patient_id: str,
    doctor_id: str,
    appointment_date: str,
    appointment_slot: str,
    reason: str = "General Consultation",
    patient_name: str = "",
) -> dict:
    import uuid
    from datetime import datetime

    existing = await db["appointments"].find_one({
        "doctor_id": doctor_id,
        "appointment_date": appointment_date,
        "appointment_slot": appointment_slot,
        "status": {"$ne": "cancelled"},
    })
    if existing:
        return {
            "success": False,
            "message": f"Slot {appointment_slot} on {appointment_date} is already taken. Please choose another.",
        }

    doc = await db["users"].find_one({"_id": doctor_id}, {"name": 1})
    doctor_name = doc["name"] if doc else "Doctor"
    if not patient_name:
        pat = await db["patients"].find_one({"_id": patient_id}, {"personal.name": 1})
        patient_name = pat["personal"]["name"] if pat else "Patient"

    appt_id = "APT" + uuid.uuid4().hex[:8].upper()
    await db["appointments"].insert_one({
        "_id": appt_id,
        "patient_id": patient_id,
        "patient_name": patient_name,
        "doctor_id": doctor_id,
        "doctor_name": doctor_name,
        "appointment_date": appointment_date,
        "appointment_slot": appointment_slot,
        "followup_reason": reason,
        "status": "scheduled",
        "created_at": datetime.utcnow().isoformat(),
        "source": "patient_chatbot",
    })
    logger.info("patient_chatbot_appointment_booked",
                appointment_id=appt_id, patient_id=patient_id)
    return {
        "success": True,
        "__ui__": {
            "type": "patient_booking_confirm",
            "appointment_id": appt_id,
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "appointment_date": appointment_date,
            "appointment_slot": appointment_slot,
            "reason": reason,
        },
    }


async def _register_patient(
    db,
    name: str,
    phone: str,
    date_of_birth: str,
    sex: str,
    email: str = "",
    doctor_id: str = "",
) -> dict:
    import uuid
    from datetime import datetime

    sex_map = {"male": "M", "female": "F", "other": "O", "m": "M", "f": "F", "o": "O"}
    sex_norm = sex_map.get(sex.lower().strip(), "O")

    assigned_doctor_name = None
    if doctor_id:
        doc = await db["users"].find_one({"_id": doctor_id}, {"name": 1})
        if doc:
            assigned_doctor_name = doc["name"]

    patient_id = str(uuid.uuid4())
    await db["patients"].insert_one({
        "_id": patient_id,
        "personal": {
            "name": name,
            "phone": phone,
            "email": email or None,
            "date_of_birth": date_of_birth,
            "sex": sex_norm,
            "address": None,
            "known_allergies": [],
            "chronic_conditions": [],
            "blood_group": "Unknown",
            "assigned_doctor_id": doctor_id or None,
            "assigned_doctor_name": assigned_doctor_name,
            "registered_date": str(datetime.utcnow().date()),
        },
        "metadata": {
            "total_visits": 0,
            "last_visit_date": None,
            "last_visit_doctor_id": None,
            "pending_followup_date": None,
            "pending_followup_visit_id": None,
            "embedding_pending_count": 0,
        },
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "source": "patient_chatbot",
    })
    logger.info("patient_self_registered", patient_id=patient_id, name=name)
    return {"success": True, "patient_id": patient_id, "name": name}


# ─── Tools (used only by the LLM path) ────────────────────────────────────────

def _make_tools(db):

    @tool
    async def find_patient(name: str, phone: str = "") -> dict:
        """
        Find a patient by name or phone. Returns a UI form for next steps.
        Args:
            name: Patient full name
            phone: Patient phone number
        """
        import re as _re
        doctors = await _fetch_doctors(db)
        proj = {"_id": 1, "personal.name": 1, "personal.phone": 1}

        # ── 1. Try AND search (name + phone together) for best precision ────
        if name.strip() and phone.strip():
            and_cursor = db["patients"].find(
                {
                    "personal.name": {"$regex": _re.escape(name.strip()), "$options": "i"},
                    "personal.phone": {"$regex": _re.escape(phone.strip())},
                },
                proj,
            ).limit(3)
            and_docs = await and_cursor.to_list(None)
            if len(and_docs) == 1:
                p = and_docs[0]
                return {
                    "found": True,
                    "patient_id": str(p["_id"]),
                    "patient_name": p["personal"]["name"],
                    "__ui__": {
                        "type": "patient_slot_picker",
                        "patient_id": str(p["_id"]),
                        "patient_name": p["personal"]["name"],
                        "doctors": doctors,
                    },
                }

        # ── 2. OR search ────────────────────────────────────────────────────
        queries = []
        if name.strip():
            queries.append({"personal.name": {"$regex": _re.escape(name.strip()), "$options": "i"}})
        if phone.strip():
            queries.append({"personal.phone": {"$regex": _re.escape(phone.strip())}})

        cursor = db["patients"].find(
            {"$or": queries} if queries else {},
            proj,
        ).limit(5)
        docs = await cursor.to_list(None)

        if not docs:
            return {
                "found": False,
                "__ui__": {
                    "type": "patient_registration_form",
                    "name_hint": name,
                    "phone_hint": phone,
                    "doctors": doctors,
                },
            }
        if len(docs) == 1:
            p = docs[0]
            return {
                "found": True,
                "patient_id": str(p["_id"]),
                "patient_name": p["personal"]["name"],
                "__ui__": {
                    "type": "patient_slot_picker",
                    "patient_id": str(p["_id"]),
                    "patient_name": p["personal"]["name"],
                    "doctors": doctors,
                },
            }
        # Multiple matches — treat as new patient (same phone, different person e.g. family)
        # Never expose other patients' names. Show registration form pre-filled with submitted details.
        return {
            "found": "multiple",
            "__ui__": {
                "type": "patient_registration_form",
                "name_hint": name,
                "phone_hint": phone,
                "doctors": doctors,
            },
        }

    @tool
    async def view_my_appointments(patient_id: str) -> dict:
        """
        Retrieve upcoming and recent appointments for the patient.
        Args:
            patient_id: Patient's ID (from conversation context)
        """
        from datetime import date as _date
        today = _date.today().isoformat()
        cursor = db["appointments"].find(
            {"patient_id": patient_id, "status": {"$ne": "cancelled"}},
            {"_id": 1, "appointment_date": 1, "appointment_slot": 1,
             "doctor_name": 1, "followup_reason": 1, "status": 1},
        ).sort("appointment_date", 1).limit(10)
        docs = await cursor.to_list(None)
        if not docs:
            return {"found": False, "message": "No upcoming appointments found."}
        upcoming, past = [], []
        for d in docs:
            entry = {
                "id": d["_id"],
                "date": d.get("appointment_date", ""),
                "time": d.get("appointment_slot", ""),
                "doctor": d.get("doctor_name", ""),
                "reason": d.get("followup_reason") or "General Consultation",
                "status": d.get("status", "scheduled"),
            }
            (upcoming if d.get("appointment_date", "") >= today else past).append(entry)
        return {"found": True, "upcoming": upcoming, "recent_past": past[-3:], "total": len(docs)}

    return [find_patient, view_my_appointments]


# ─── Graph ─────────────────────────────────────────────────────────────────────

def build_patient_booking_graph(db, checkpointer):
    tools = _make_tools(db)
    llm_with_tools = _llm.bind_tools(tools)
    tool_map = {t.name: t for t in tools}

    async def agent_node(state: PatientChatState) -> dict:
        last_content = state["messages"][-1].content if state["messages"] else ""

        # ── Fast-path: slot picker form submission ───────────────────────────
        if last_content.startswith("__PATIENT_BOOK__:"):
            try:
                data = json.loads(last_content[len("__PATIENT_BOOK__:"):])
                result = await _book_appointment(
                    db,
                    patient_id=data["patient_id"],
                    doctor_id=data["doctor_id"],
                    appointment_date=data["appointment_date"],
                    appointment_slot=data["appointment_slot"],
                    reason=data.get("reason", "General Consultation"),
                    patient_name=data.get("patient_name", ""),
                )
                if result.get("success"):
                    return {"messages": [AIMessage(content=f"__AGENT_UI__:{json.dumps(result['__ui__'])}")]}
                return {"messages": [AIMessage(content=result.get("message", "Booking failed. Please try again."))]}
            except Exception as e:
                logger.error("patient_book_fast_path_error", error=str(e))
                return {"messages": [AIMessage(content="Something went wrong with your booking. Please try again.")]}

        # ── Fast-path: registration + booking form submission ────────────────
        if last_content.startswith("__PATIENT_REGISTER__:"):
            try:
                data = json.loads(last_content[len("__PATIENT_REGISTER__:"):])
                reg = await _register_patient(
                    db,
                    name=data["name"],
                    phone=data["phone"],
                    date_of_birth=data.get("date_of_birth", ""),
                    sex=data.get("sex", "O"),
                    email=data.get("email", ""),
                    doctor_id=data.get("doctor_id", ""),
                )
                if not reg.get("success"):
                    return {"messages": [AIMessage(content="Registration failed. Please try again.")]}

                # Book appointment if date + slot provided
                if data.get("appointment_date") and data.get("appointment_slot") and data.get("doctor_id"):
                    book = await _book_appointment(
                        db,
                        patient_id=reg["patient_id"],
                        doctor_id=data["doctor_id"],
                        appointment_date=data["appointment_date"],
                        appointment_slot=data["appointment_slot"],
                        reason=data.get("reason", "General Consultation"),
                        patient_name=reg["name"],
                    )
                    if book.get("success"):
                        return {"messages": [AIMessage(content=f"__AGENT_UI__:{json.dumps(book['__ui__'])}")]}
                    return {"messages": [AIMessage(content=book.get("message", "Booking failed after registration."))]}

                # Registered but no slot chosen yet — show slot picker
                doctors = await _fetch_doctors(db)
                ui = {
                    "type": "patient_slot_picker",
                    "patient_id": reg["patient_id"],
                    "patient_name": reg["name"],
                    "doctors": doctors,
                }
                return {"messages": [AIMessage(content=f"__AGENT_UI__:{json.dumps(ui)}")]}
            except Exception as e:
                logger.error("patient_register_fast_path_error", error=str(e))
                return {"messages": [AIMessage(content="Registration failed. Please try again.")]}

        # ── LLM path for identity parsing and view_my_appointments ──────────
        system = SystemMessage(content=SYSTEM_PROMPT.format(today=date.today().isoformat()))
        response = await llm_with_tools.ainvoke([system, *state["messages"][-8:]])
        return {"messages": [response]}

    async def tools_node(state: PatientChatState) -> dict:
        last = state["messages"][-1]
        results = []
        ui_payload = None

        for call in last.tool_calls:
            fn = tool_map.get(call["name"])
            if fn:
                try:
                    result = await fn.ainvoke(call["args"])
                except Exception as e:
                    logger.error("patient_tool_error", tool=call["name"], error=str(e))
                    result = {"error": str(e)}
            else:
                result = {"error": f"Tool '{call['name']}' not found."}

            # Capture first __ui__ payload (skip for multiple-match results without __ui__)
            if isinstance(result, dict) and "__ui__" in result and ui_payload is None:
                ui_payload = result["__ui__"]

            results.append(ToolMessage(content=json.dumps(result), tool_call_id=call["id"]))

        # Emit __AGENT_UI__ message so route_tools sends directly to END
        if ui_payload:
            results.append(AIMessage(content=f"__AGENT_UI__:{json.dumps(ui_payload)}"))

        return {"messages": results}

    def route_agent(state: PatientChatState) -> str:
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    def route_tools(state: PatientChatState) -> str:
        """After tools: go to END if UI was emitted, else back to agent for LLM synthesis."""
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.content.startswith("__AGENT_UI__:"):
            return END
        return "agent"

    graph = StateGraph(PatientChatState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", route_agent, {"tools": "tools", END: END})
    graph.add_conditional_edges("tools", route_tools, {"agent": "agent", END: END})
    return graph.compile(checkpointer=checkpointer)
