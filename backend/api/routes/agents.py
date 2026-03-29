"""
backend/api/routes/agents.py

AGENT API ROUTES

POST /api/agents/chat     — Main agent entry point (staff messages)
POST /api/agents/webhook  — External event receiver (patient replies, cron)
GET  /api/agents/thread/{thread_id} — Fetch conversation history

AUTH:
  /agents/chat    → require_receptionist (not admin, not doctors)
  /agents/webhook → webhook_secret header verification (not JWT)
  /agents/thread  → require_any_staff

RATE LIMITING:
  /agents/chat: 20 req/min per user (enforced by slowapi middleware)

THREAD MANAGEMENT:
  Every conversation has a thread_id (UUID).
  If the client sends no thread_id → new thread is created.
  All state is stored in Postgres keyed by thread_id.
  Thread ID is returned in every response so the client
  can resume the conversation in subsequent calls.
"""

import uuid
import time
import asyncio
import structlog
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from typing import Optional
from langgraph.types import Command

from backend.db.mongodb.connection import get_db
from backend.models.patient import TokenData
from backend.api.middleware.auth_middleware import (
    require_receptionist_or_doctor, require_any_staff,
    require_receptionist_or_doctor_or_admin,
)
from backend.core.config import get_settings

router = APIRouter(prefix="/agents", tags=["Agents — Phase 3"])
logger = structlog.get_logger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────────────────────
# REQUEST / RESPONSE MODELS
# ─────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None        # Omit to start a new conversation
    patient_id: Optional[str] = None       # Pre-populate patient context if known


class WebhookRequest(BaseModel):
    thread_id: str                          # Which scheduling thread to resume
    event_type: str                         # patient_reply | cron_reminder | cron_timeout
    payload: str                            # The patient's reply text or cron signal


class ChatResponse(BaseModel):
    thread_id: str
    response: str                           # Last AIMessage content
    current_agent: str
    patient_id: Optional[str] = None
    session_done: bool = False              # True when staff ends session after booking
    # RAG-specific — populated when current_agent == "RAGAgent"
    sources: list = []
    cached: bool = False
    retrieval_count: int = 0


class ThreadHistoryResponse(BaseModel):
    thread_id: str
    messages: list
    current_agent: str


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _get_graph(db: AsyncIOMotorDatabase):
    """
    Retrieves the compiled graph from FastAPI app state.
    The graph is built once at startup in main.py lifespan.
    """
    from backend.main import app
    graph = getattr(app.state, "agent_graph", None)
    if not graph:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent system not initialised. Try again in a moment.",
        )
    return graph


def _extract_last_ai_response(result: dict) -> str:
    """Extract the last AIMessage content from graph output."""
    from langchain_core.messages import AIMessage
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg.content
    return "Processing complete."


def _extract_token_usage(result: dict) -> dict:
    """Sum input/output tokens across all AIMessages in the result."""
    from langchain_core.messages import AIMessage
    input_tokens = 0
    output_tokens = 0
    for msg in result.get("messages", []):
        if isinstance(msg, AIMessage):
            meta = getattr(msg, "usage_metadata", None) or {}
            input_tokens += meta.get("input_tokens", 0)
            output_tokens += meta.get("output_tokens", 0)
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


async def _log_agent_call(
    db: AsyncIOMotorDatabase,
    *,
    thread_id: str,
    staff_id: str,
    staff_role: str,
    agent: str,
    latency_ms: int,
    tokens: dict,
    confidence: float,
    tool_calls_made: int,
    fallback: bool,
    error: str | None,
    cache_hit: bool | None,
    smtp_sent: bool | None,
) -> None:
    """Fire-and-forget: write one observability record to agent_logs."""
    try:
        await db["agent_logs"].insert_one({
            "thread_id": thread_id,
            "timestamp": datetime.now(timezone.utc),
            "staff_id": staff_id,
            "staff_role": staff_role,
            "agent": agent,
            "latency_ms": latency_ms,
            "input_tokens": tokens["input_tokens"],
            "output_tokens": tokens["output_tokens"],
            "supervisor_confidence": confidence,
            "tool_calls_made": tool_calls_made,
            "fallback": fallback,
            "error": error,
            "cache_hit": cache_hit,
            "smtp_sent": smtp_sent,
        })
    except Exception as e:
        logger.warning("agent_log_write_failed", error=str(e))


# ─────────────────────────────────────────────────────────────
# POST /agents/chat
# ─────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def agent_chat(
    request: ChatRequest,
    current_user: TokenData = Depends(require_receptionist_or_doctor_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Unified agent chat endpoint.

    ROLE-BASED ROUTING:
      Receptionist → all agents: RECEPTIONIST, RAG, SCHEDULING, NOTIFICATION, CALENDAR
      Doctor       → RAG + CALENDAR only (supervisor enforces this via staff_role)
        - RAG: clinical history, medications, diagnoses
        - CALENDAR: own patients' appointments + follow-ups (auto-scoped by doctor_id)

    FLOW:
    1. Generate thread_id if not provided (new conversation)
    2. Build initial state with staff context + patient context
    3. Invoke compiled graph — supervisor routes based on message + staff_role
    4. Return last AIMessage + thread_id + RAG sources (if RAGAgent)

    THREAD RESUMPTION:
    Client sends the same thread_id in subsequent messages.
    LangGraph loads state from Postgres and continues the conversation.
    """
    # Generate thread ID for new conversations
    thread_id = request.thread_id or str(uuid.uuid4())

    graph = _get_graph(db)

    # Fetch staff name from DB (not in JWT)
    staff_doc = await db["users"].find_one({"_id": current_user.user_id}, {"name": 1})
    staff_name = staff_doc["name"] if staff_doc else current_user.email

    from langchain_core.messages import HumanMessage

    # Initial state for new threads
    initial_state = {
        "messages": [HumanMessage(content=request.message)],
        "current_agent": "",
        "intent": "",
        "confidence": 0.0,
        "fallback_reason": None,
        "staff_id": current_user.user_id,
        "staff_name": staff_name,
        "staff_role": current_user.role,
        "patient_id": request.patient_id,
        "patient_name": None,
        "patient_email": None,
        "patient_phone": None,
        "is_new_patient": None,
        "assigned_doctor_id": None,
        "assigned_doctor_name": None,
        "collected_fields": {},
        "registration_attempts": 0,
        "rag_query": None,
        "rag_answer": None,
        "rag_sources": [],
        "tool_calls_made": 0,
        "appointment_date": None,
        "appointment_slot": None,
        "followup_reason": None,
        "confirmation_status": None,
        "reminder_sent": False,
        "scheduling_retry_count": 0,
        "email_type": None,
        "email_body": None,
        "email_sent": False,
        "email_attempt": 1,
        "notification_thread_id": None,
        "medications_to_check": [],
        "drug_alerts": [],
        "error": None,
        "error_count": 0,
        "thread_id": thread_id,
    }

    config = {"configurable": {"thread_id": thread_id}}

    try:
        # For existing threads, only send the new message — LangGraph
        # loads the rest from Postgres checkpoint.
        if request.thread_id:
            invoke_input = {"messages": [HumanMessage(content=request.message)]}
        else:
            invoke_input = initial_state

        t0 = time.time()
        result = await graph.ainvoke(invoke_input, config=config)
        latency_ms = int((time.time() - t0) * 1000)

        response_text = _extract_last_ai_response(result)
        current_agent = result.get("current_agent", "unknown")

        # Attach RAG metadata when the RAG agent handled the query
        rag_sources = result.get("rag_sources", []) or []
        is_rag = current_agent in ("RAG_AGENT", "RAGAgent")
        is_notification = current_agent in ("NOTIFICATION", "NotificationAgent")

        logger.info(
            "agent_chat_complete",
            thread_id=thread_id,
            agent=current_agent,
            latency_ms=latency_ms,
            staff_id=current_user.user_id,
        )

        # Observability log — non-blocking
        tokens = _extract_token_usage(result)
        asyncio.create_task(_log_agent_call(
            db,
            thread_id=thread_id,
            staff_id=current_user.user_id,
            staff_role=current_user.role,
            agent=current_agent,
            latency_ms=latency_ms,
            tokens=tokens,
            confidence=float(result.get("confidence") or 0.0),
            tool_calls_made=int(result.get("tool_calls_made") or 0),
            fallback=current_agent == "fallback",
            error=result.get("error"),
            cache_hit=result.get("rag_cached", False) if is_rag else None,
            smtp_sent=result.get("email_sent") if is_notification else None,
        ))

        return ChatResponse(
            thread_id=thread_id,
            response=response_text,
            current_agent=current_agent,
            patient_id=result.get("patient_id"),
            session_done=current_agent == "SESSION_END",
            sources=rag_sources if is_rag else [],
            cached=result.get("rag_cached", False) if is_rag else False,
            retrieval_count=len(rag_sources) if is_rag else 0,
        )

    except Exception as e:
        logger.error("agent_chat_error", thread_id=thread_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent error: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────
# POST /agents/webhook
# ─────────────────────────────────────────────────────────────

@router.post("/webhook")
async def agent_webhook(
    request: WebhookRequest,
    x_webhook_secret: str = Header(..., alias="X-Webhook-Secret"),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Receives external events and resumes suspended agent graphs.

    Used by:
    - D-1 cron job: resumes scheduling threads to send reminders
    - Patient email reply handler: passes patient's reply to scheduling agent
    - Timeout checker: marks stale threads as timed out

    AUTH: X-Webhook-Secret header (shared secret, not JWT).
    This endpoint is called by system services, not humans.

    SECURITY: Never expose the webhook secret in client code.
    Store it in environment variables on both sides.
    """
    # Verify webhook secret
    if x_webhook_secret != settings.webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )

    graph = _get_graph(db)
    config = {"configurable": {"thread_id": request.thread_id}}

    try:
        # Resume the suspended graph with the external event payload
        result = await graph.ainvoke(
            Command(resume=request.payload),
            config=config,
        )

        logger.info(
            "webhook_processed",
            thread_id=request.thread_id,
            event_type=request.event_type,
        )

        return {
            "thread_id": request.thread_id,
            "event_type": request.event_type,
            "status": "processed",
            "current_agent": result.get("current_agent"),
        }

    except Exception as e:
        logger.error(
            "webhook_error",
            thread_id=request.thread_id,
            event_type=request.event_type,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook processing error: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────
# GET /agents/thread/{thread_id}
# ─────────────────────────────────────────────────────────────

@router.get("/thread/{thread_id}", response_model=ThreadHistoryResponse)
async def get_thread_history(
    thread_id: str,
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Fetch the conversation history for a thread.
    Used by frontend to display previous messages on page load.
    """
    graph = _get_graph(db)
    config = {"configurable": {"thread_id": thread_id}}

    try:
        state = await graph.aget_state(config)

        if not state or not state.values:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found",
            )

        messages = []
        for msg in state.values.get("messages", []):
            messages.append({
                "role": msg.type,
                "content": msg.content,
            })

        return ThreadHistoryResponse(
            thread_id=thread_id,
            messages=messages,
            current_agent=state.values.get("current_agent", "unknown"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_thread_error", thread_id=thread_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
