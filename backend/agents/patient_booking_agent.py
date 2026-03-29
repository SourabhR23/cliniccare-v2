"""
backend/agents/patient_booking_agent.py — Patient-facing appointment booking assistant.

Public-facing chatbot (no staff auth). Handles BOOKING ONLY:
  - Patient identity verification (structured name + phone form)
  - Book new appointments (doctor → slot → confirm)

Out of scope (redirected to receptionist):
  - Viewing existing appointments
  - Rescheduling / cancelling appointments
  These actions require staff verification and are handled at the clinic desk.
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

CLINIC_SLOTS = [
    "09:00 AM", "09:30 AM", "10:00 AM", "10:30 AM", "11:00 AM", "11:30 AM",
    "02:00 PM", "02:30 PM", "03:00 PM", "03:30 PM", "04:00 PM", "04:30 PM",
]

SYSTEM_PROMPT = """You are ClinicCare's friendly patient self-service assistant. You help patients register, book appointments, and check their schedule.

Conversation flow:
1. Greet the patient warmly. The identity form has been shown — wait for the patient to submit their Name and Phone.
   The submitted message arrives as: "Full Name: [name] | Phone: [phone]"
   Extract name and phone, then call find_patient using name; if not found, try phone.

2. If FOUND → greet by name, then ask: "What can I help you with today? I can book a new appointment or show your upcoming schedule."

3. If NOT FOUND → warmly offer to register them:
   "Welcome! I don't have your record yet — would you like me to register you? It only takes a moment."
   If they say yes, ask in ONE message:
   "Please share: your date of birth (DD/MM/YYYY), sex (Male/Female/Other), and email address."
   Once collected, call list_doctors so they can pick a doctor, then call register_patient.
   After successful registration, proceed directly to booking.

4. BOOKING flow: Ask for preferred date → call list_doctors → patient picks doctor → call get_available_slots → patient picks slot → call book_appointment → confirm details.

5. VIEW APPOINTMENTS: If patient asks to see/check their appointments, call view_my_appointments and present the list clearly.

6. RESCHEDULE / CANCEL: Redirect politely — "To reschedule or cancel, please call the clinic or speak to our receptionist. They'll sort it out for you right away!"

7. After any completed action, ask: "Is there anything else I can help you with?"
   - Done → say a warm goodbye ending with "See you soon!" or "Goodbye!".

STRICT SCOPE:
- Clinical questions (diagnosis, medications, reports) → Decline politely, not in scope.
- NEVER share clinical data.

Rules:
- Be warm, concise, and professional
- Accept natural date formats ("25 March", "next Monday", "tomorrow")
- Today's date: {today}"""


class PatientChatState(TypedDict):
    messages: Annotated[list, add_messages]


def _make_tools(db):
    """Create patient-facing tools bound to a specific db instance."""

    @tool
    async def find_patient(query: str) -> dict:
        """
        Find a patient by name or phone number.
        Args:
            query: Patient name or phone number
        """
        import re as _re
        escaped = _re.escape(query.strip())
        cursor = db["patients"].find(
            {"$or": [
                {"personal.name": {"$regex": escaped, "$options": "i"}},
                {"personal.phone": {"$regex": escaped}},
            ]},
            {"_id": 1, "personal.name": 1, "personal.phone": 1}
        ).limit(5)
        docs = await cursor.to_list(None)
        if not docs:
            return {"found": False, "message": "No patient found with that name or phone number."}
        if len(docs) == 1:
            p = docs[0]
            return {
                "found": True,
                "patient_id": str(p["_id"]),
                "name": p["personal"]["name"],
                "phone": p["personal"].get("phone", ""),
            }
        # Multiple matches — return list for LLM to ask user to clarify
        return {
            "found": "multiple",
            "matches": [
                {"patient_id": str(p["_id"]), "name": p["personal"]["name"], "phone": p["personal"].get("phone", "")}
                for p in docs
            ],
            "message": "Multiple patients found. Ask user to clarify.",
        }

    @tool
    async def list_doctors() -> dict:
        """List available doctors at the clinic."""
        cursor = db["users"].find(
            {"role": "doctor", "is_active": True},
            {"_id": 1, "name": 1, "specialization": 1}
        )
        docs = await cursor.to_list(length=20)
        return {
            "doctors": [
                {
                    "id": str(d["_id"]),
                    "name": d["name"],
                    "specialization": d.get("specialization") or "General Physician",
                }
                for d in docs
            ]
        }

    @tool
    async def get_available_slots(doctor_id: str, appointment_date: str) -> dict:
        """
        Get available time slots for a doctor on a given date.
        Args:
            doctor_id: Doctor's ID from list_doctors
            appointment_date: ISO date YYYY-MM-DD
        """
        cursor = db["appointments"].find(
            {"doctor_id": doctor_id, "appointment_date": appointment_date, "status": {"$ne": "cancelled"}},
            {"appointment_slot": 1}
        )
        booked = [d.get("appointment_slot") for d in await cursor.to_list(None) if d.get("appointment_slot")]
        available = [s for s in CLINIC_SLOTS if s not in booked]
        return {
            "date": appointment_date,
            "available_slots": available,
            "booked_count": len(booked),
            "message": "No slots available on this date." if not available else f"{len(available)} slots available.",
        }

    @tool
    async def book_appointment(
        patient_id: str,
        doctor_id: str,
        appointment_date: str,
        appointment_slot: str,
        reason: str = "General Consultation",
    ) -> dict:
        """
        Book a new appointment for the patient.
        Args:
            patient_id: Patient's ID
            doctor_id: Doctor's ID from list_doctors
            appointment_date: ISO date YYYY-MM-DD
            appointment_slot: Time slot e.g. "10:00 AM"
            reason: Reason for visit
        """
        import uuid
        from datetime import datetime

        # Verify slot is still free
        existing = await db["appointments"].find_one({
            "doctor_id": doctor_id,
            "appointment_date": appointment_date,
            "appointment_slot": appointment_slot,
            "status": {"$ne": "cancelled"},
        })
        if existing:
            return {"success": False, "message": f"Slot {appointment_slot} on {appointment_date} is already booked. Please choose another."}

        # Resolve names
        doc = await db["users"].find_one({"_id": doctor_id}, {"name": 1})
        doctor_name = doc["name"] if doc else "Doctor"
        pat = await db["patients"].find_one({"_id": patient_id}, {"personal.name": 1})
        patient_name = pat["personal"]["name"] if pat else "Patient"

        appt_id = "APT" + uuid.uuid4().hex[:8].upper()
        record = {
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
        }
        await db["appointments"].insert_one(record)
        logger.info("patient_chatbot_appointment_booked",
                    appointment_id=appt_id, patient_id=patient_id)
        return {
            "success": True,
            "appointment_id": appt_id,
            "date": appointment_date,
            "time": appointment_slot,
            "doctor": doctor_name,
            "reason": reason,
        }

    @tool
    async def register_patient(
        name: str,
        phone: str,
        date_of_birth: str,
        sex: str,
        email: str = "",
        doctor_id: str = "",
    ) -> dict:
        """
        Register a new patient in the system.
        Args:
            name: Full name
            phone: 10-digit phone number
            date_of_birth: ISO format YYYY-MM-DD
            sex: M, F, or O
            email: Optional email address
            doctor_id: Optional doctor ID from list_doctors
        """
        from datetime import datetime
        import uuid

        # Normalise sex
        sex_map = {"male": "M", "female": "F", "other": "O", "m": "M", "f": "F", "o": "O"}
        sex_norm = sex_map.get(sex.lower().strip(), "O")

        # Resolve doctor name if provided
        assigned_doctor_id = doctor_id or None
        assigned_doctor_name = None
        if assigned_doctor_id:
            doc = await db["users"].find_one({"_id": assigned_doctor_id}, {"name": 1})
            if doc:
                assigned_doctor_name = doc["name"]

        # Parse DOB
        try:
            dob_parsed = date_of_birth  # keep as string; store as-is
        except Exception:
            dob_parsed = date_of_birth

        patient_id = str(uuid.uuid4())
        record = {
            "_id": patient_id,
            "personal": {
                "name": name,
                "phone": phone,
                "email": email or None,
                "date_of_birth": dob_parsed,
                "sex": sex_norm,
                "address": None,
                "known_allergies": [],
                "chronic_conditions": [],
                "blood_group": None,
                "assigned_doctor_id": assigned_doctor_id,
                "assigned_doctor_name": assigned_doctor_name,
            },
            "total_visits": 0,
            "last_visit_date": None,
            "pending_followup_date": None,
            "registered_date": datetime.utcnow().isoformat(),
            "source": "patient_chatbot",
        }
        await db["patients"].insert_one(record)
        logger.info("patient_self_registered", patient_id=patient_id, name=name)
        return {
            "success": True,
            "patient_id": patient_id,
            "name": name,
            "message": f"Registration successful! Welcome, {name}.",
        }

    @tool
    async def view_my_appointments(patient_id: str) -> dict:
        """
        Retrieve upcoming and recent appointments for the patient.
        Args:
            patient_id: Patient's ID from find_patient or register_patient
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
        upcoming = []
        past = []
        for d in docs:
            entry = {
                "id": d["_id"],
                "date": d.get("appointment_date", ""),
                "time": d.get("appointment_slot", ""),
                "doctor": d.get("doctor_name", ""),
                "reason": d.get("followup_reason") or "General Consultation",
                "status": d.get("status", "scheduled"),
            }
            if d.get("appointment_date", "") >= today:
                upcoming.append(entry)
            else:
                past.append(entry)
        return {
            "found": True,
            "upcoming": upcoming,
            "recent_past": past[-3:],
            "total": len(docs),
        }

    return [find_patient, list_doctors, get_available_slots, book_appointment,
            register_patient, view_my_appointments]


def build_patient_booking_graph(db, checkpointer):
    """Build and compile the patient booking LangGraph agent."""
    tools = _make_tools(db)
    llm_with_tools = _llm.bind_tools(tools)
    tool_map = {t.name: t for t in tools}

    async def agent_node(state: PatientChatState) -> dict:
        system = SystemMessage(content=SYSTEM_PROMPT.format(today=date.today().isoformat()))
        # Keep last 14 messages to limit tokens while preserving booking context
        response = await llm_with_tools.ainvoke([system, *state["messages"][-14:]])
        return {"messages": [response]}

    async def tools_node(state: PatientChatState) -> dict:
        last = state["messages"][-1]
        results = []
        for call in last.tool_calls:
            fn = tool_map.get(call["name"])
            if fn:
                try:
                    result = await fn.ainvoke(call["args"])
                except Exception as e:
                    logger.error("patient_tool_error", tool=call["name"], error=str(e))
                    result = {"error": str(e)}
            else:
                result = {"error": f"Tool '{call['name']}' not available."}
            results.append(ToolMessage(
                content=json.dumps(result),
                tool_call_id=call["id"],
            ))
        return {"messages": results}

    def route(state: PatientChatState) -> str:
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(PatientChatState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
