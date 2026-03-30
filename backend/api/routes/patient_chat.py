"""
backend/api/routes/patient_chat.py — Public patient-facing booking chatbot.

NO authentication required — this endpoint is accessible without a JWT token.
Patients identify themselves via the conversational agent (name / phone).

Session management:
  - First request: session_id is None → backend generates one and returns it
  - Subsequent requests: client sends the same session_id → LangGraph loads
    the thread state from the MemorySaver checkpointer
  - Session is considered done when the bot says goodbye

Security:
  - No clinical data is accessible via patient tools
  - Only appointment logistics (date, time, doctor name)
  - Rate-limit at nginx / API gateway level for production
"""

import uuid
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from typing import Optional

from langchain_core.messages import HumanMessage, AIMessage

from backend.db.mongodb.connection import get_db

router = APIRouter(prefix="/patient", tags=["patient-chat"])
logger = structlog.get_logger(__name__)


def _get_patient_graph():
    """Retrieve the pre-built patient graph from app state."""
    from backend.main import app
    graph = getattr(app.state, "patient_graph", None)
    if not graph:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Patient chat unavailable. Please try again shortly.",
        )
    return graph

# Session done keywords — if bot reply contains any, frontend closes the session
_DONE_SIGNALS = [
    "see you soon", "goodbye", "good bye", "see you at",
    "have a great", "take care", "session complete", "all the best",
]


CLINIC_SLOTS = [
    "09:00 AM", "09:30 AM", "10:00 AM", "10:30 AM", "11:00 AM", "11:30 AM",
    "02:00 PM", "02:30 PM", "03:00 PM", "03:30 PM", "04:00 PM", "04:30 PM",
]


@router.get("/doctors")
async def patient_get_doctors(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public endpoint — list all active doctors for patient booking form."""
    cursor = db["users"].find(
        {"role": "doctor", "is_active": True},
        {"_id": 1, "name": 1, "specialization": 1},
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


@router.get("/slots")
async def patient_get_slots(
    doctor_id: str,
    date: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Public endpoint — return available slots for a doctor on a given date."""
    cursor = db["appointments"].find(
        {
            "doctor_id": doctor_id,
            "appointment_date": date,
            "status": {"$ne": "cancelled"},
        },
        {"appointment_slot": 1},
    )
    booked = [d.get("appointment_slot") for d in await cursor.to_list(None) if d.get("appointment_slot")]
    available = [s for s in CLINIC_SLOTS if s not in booked]
    return {"date": date, "slots": available}


class PatientChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class PatientChatResponse(BaseModel):
    reply: str
    session_id: str
    session_done: bool


@router.post("/chat", response_model=PatientChatResponse)
async def patient_chat(
    request: PatientChatRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Conversational patient booking endpoint.
    Accepts a message + optional session_id, returns bot reply + session_id.
    session_done=True signals the frontend to offer a "Start new session" option.
    """
    session_id = request.session_id or str(uuid.uuid4())
    graph = _get_patient_graph()

    config = {"configurable": {"thread_id": f"patient:{session_id}"}}

    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=request.message)]},
            config=config,
        )
    except Exception as e:
        logger.error("patient_chat_graph_error", session_id=session_id, error=str(e))
        return PatientChatResponse(
            reply="I'm sorry, something went wrong. Please try again.",
            session_id=session_id,
            session_done=False,
        )

    # Extract last AI message (skip empty or tool-call-only messages)
    messages = result.get("messages", [])
    reply = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and not getattr(msg, "tool_calls", None):
            reply = msg.content
            break

    if not reply:
        reply = "I'm here to help! How can I assist you today?"

    reply_lower = reply.lower()
    session_done = any(signal in reply_lower for signal in _DONE_SIGNALS)

    logger.info("patient_chat_response",
                session_id=session_id,
                session_done=session_done,
                reply_length=len(reply))

    return PatientChatResponse(reply=reply, session_id=session_id, session_done=session_done)
