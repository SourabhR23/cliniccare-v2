"""
api/routes/rag.py

DOCTOR-FACING RAG ENDPOINTS

POST /rag/query
  Doctor asks a clinical question. Returns synthesized answer + sources.
  Optional: scope to a specific patient_id.

GET /rag/previsit-brief/{patient_id}
  Returns a structured pre-appointment brief for a patient.
  Cached in Redis for 1 hour.

ACCESS CONTROL:
  Both routes: require_doctor_or_admin.
  Receptionists cannot query clinical RAG (HIPAA: role-based data access).

  Doctor scoping:
  - Doctors can only query their OWN patients.
  - When doctor provides patient_id, we verify they are the assigned doctor.
  - Admins can query any patient (audit purposes).

  This follows the same pattern as patients.py (Phase 1).
"""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field
import redis.asyncio as aioredis
import structlog

from backend.db.mongodb.connection import get_db
from backend.api.middleware.auth_middleware import require_doctor_or_admin
from backend.models.patient import TokenData, UserRoleEnum
from backend.rag.rag_service import RAGService
from backend.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/rag", tags=["RAG - Clinical Assistant"])


# ─────────────────────────────────────────────────────────────
# REDIS DEPENDENCY
# ─────────────────────────────────────────────────────────────

async def get_redis() -> Optional[aioredis.Redis]:
    """
    FastAPI dependency for Redis connection.

    Returns None if Redis is unavailable — RAGService degrades gracefully
    (answers are not cached but still returned correctly).

    WHY OPTIONAL:
      In development, Redis may not be running.
      We don't want the RAG endpoints to crash because of a missing cache.
      Cache is an optimization, not a requirement.
    """
    try:
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        return client
    except Exception as e:
        logger.warning("redis_unavailable", error=str(e), detail="RAG caching disabled")
        return None


def get_rag_service(
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis_client=Depends(get_redis),
) -> RAGService:
    """FastAPI dependency: creates RAGService with db + redis injection."""
    return RAGService(db=db, redis_client=redis_client)


# ─────────────────────────────────────────────────────────────
# REQUEST / RESPONSE SCHEMAS
# ─────────────────────────────────────────────────────────────

class RAGQueryRequest(BaseModel):
    """
    Doctor's query to the clinical assistant.

    patient_id is optional:
    - With patient_id: retrieval scoped to that patient's history only
    - Without patient_id: retrieval across ALL embedded visits
      (useful for questions like "show me cases where we prescribed Azithromycin to diabetics")
    """
    query: str = Field(
        ...,
        min_length=5,
        max_length=500,
        example="Has this patient had any respiratory infections in the past year?",
    )
    patient_id: Optional[str] = Field(
        None,
        description="Scope retrieval to a specific patient. If omitted, searches all visits.",
        example="PAT001",
    )


class RAGQueryResponse(BaseModel):
    answer: str
    sources: list
    cached: bool
    retrieval_count: int


class RAGChatHistoryItem(BaseModel):
    role: str   # "user" or "assistant"
    content: str


class RAGChatRequest(BaseModel):
    message: str = Field(..., min_length=2, max_length=500)
    patient_id: Optional[str] = None
    history: List[RAGChatHistoryItem] = Field(
        default_factory=list,
        description="Prior conversation turns (max 20 messages = 10 turns)",
    )


class PreVisitBriefResponse(BaseModel):
    brief: str
    sources: list
    cached: bool


# ─────────────────────────────────────────────────────────────
# POST /rag/query
# ─────────────────────────────────────────────────────────────

@router.post(
    "/query",
    response_model=RAGQueryResponse,
    summary="Ask the clinical assistant a question",
    description="""
    Retrieve relevant visit records and synthesize a clinical answer.

    **Doctor access**: When providing patient_id, doctor must be the assigned doctor for that patient.
    **Admin access**: Can query any patient.

    The pipeline:
    1. Hybrid retrieval (vector + BM25, fused with RRF) → top 10 candidates
    2. CrossEncoder reranking → top 4 chunks
    3. GPT-4o-mini synthesis → clinical answer
    4. Redis caching (1 hour TTL)
    """,
)
async def rag_query(
    request: RAGQueryRequest,
    current_user: TokenData = Depends(require_doctor_or_admin),
    rag_service: RAGService = Depends(get_rag_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Clinical query endpoint.

    AUTHORIZATION:
    If patient_id is provided and user is a doctor:
    - Verify the patient's assigned_doctor_id matches current_user.user_id
    - If not → 403 (doctor cannot access another doctor's patient)
    """
    # ── Doctor scoping check ──────────────────────────────────
    if request.patient_id and current_user.role == UserRoleEnum.DOCTOR.value:
        await _verify_doctor_patient_access(db, request.patient_id, current_user.user_id)

    logger.info(
        "rag_query_received",
        user_id=current_user.user_id,
        patient_id=request.patient_id,
        query_preview=request.query[:50],
    )

    # Doctors are scoped to their own patients only.
    # Admins pass None so they can query across all patients.
    doctor_id_scope = (
        current_user.user_id if current_user.role == UserRoleEnum.DOCTOR.value else None
    )

    try:
        result = await rag_service.query(
            query=request.query,
            patient_id=request.patient_id,
            doctor_id=doctor_id_scope,
        )
    except Exception as e:
        logger.error("rag_query_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"RAG query failed: {str(e)}",
        )

    return RAGQueryResponse(**result)


# ─────────────────────────────────────────────────────────────
# GET /rag/previsit-brief/{patient_id}
# ─────────────────────────────────────────────────────────────

@router.get(
    "/previsit-brief/{patient_id}",
    response_model=PreVisitBriefResponse,
    summary="Get pre-visit brief for a patient",
    description="""
    Generates a structured clinical summary for a doctor to review before an appointment.

    Covers:
    - Chronic conditions and ongoing issues
    - Current medications (most recently prescribed)
    - Recurring symptoms or patterns
    - Pending follow-ups
    - Key alerts (allergies, adverse reactions)

    Response is cached in Redis for 1 hour.
    """,
)
async def get_previsit_brief(
    patient_id: str,
    current_user: TokenData = Depends(require_doctor_or_admin),
    rag_service: RAGService = Depends(get_rag_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Pre-visit brief for a specific patient.

    Called by the doctor's dashboard before each appointment.
    Heavy use of caching: doctor clicks into appointment → cache hit → <5ms.
    """
    # ── Doctor scoping check ──────────────────────────────────
    if current_user.role == UserRoleEnum.DOCTOR.value:
        await _verify_doctor_patient_access(db, patient_id, current_user.user_id)

    logger.info(
        "previsit_brief_requested",
        patient_id=patient_id,
        doctor_id=current_user.user_id,
    )

    try:
        result = await rag_service.get_previsit_brief(patient_id=patient_id)
    except Exception as e:
        logger.error("previsit_brief_failed", error=str(e), patient_id=patient_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pre-visit brief generation failed: {str(e)}",
        )

    return PreVisitBriefResponse(**result)


# ─────────────────────────────────────────────────────────────
# POST /rag/chat  — multi-turn chat with conversation memory
# ─────────────────────────────────────────────────────────────

@router.post(
    "/chat",
    response_model=RAGQueryResponse,
    summary="Clinical chat with conversation memory",
    description="""
    Multi-turn RAG chat for doctors. Each request includes the full
    conversation history so the LLM can answer follow-up questions.

    **History**: client sends all prior user+assistant turns on every request.
    The backend caps history at 20 messages (10 turns) to control context length.
    No Redis caching — history makes each turn unique.

    **Authorization** follows the same rules as /rag/query:
    - Doctors can only query their own patients (if patient_id provided).
    - Admins can query any patient.
    """,
)
async def rag_chat(
    request: RAGChatRequest,
    current_user: TokenData = Depends(require_doctor_or_admin),
    rag_service: RAGService = Depends(get_rag_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    if request.patient_id and current_user.role == UserRoleEnum.DOCTOR.value:
        await _verify_doctor_patient_access(db, request.patient_id, current_user.user_id)

    logger.info(
        "rag_chat_received",
        user_id=current_user.user_id,
        patient_id=request.patient_id,
        history_turns=len(request.history),
        message_preview=request.message[:50],
    )

    # Same scoping rule as /rag/query — doctors see own patients only.
    doctor_id_scope = (
        current_user.user_id if current_user.role == UserRoleEnum.DOCTOR.value else None
    )

    try:
        result = await rag_service.chat_query(
            message=request.message,
            patient_id=request.patient_id,
            history=[{"role": h.role, "content": h.content} for h in request.history],
            doctor_id=doctor_id_scope,
        )
    except Exception as e:
        logger.error("rag_chat_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Clinical chat failed: {str(e)}",
        )

    return RAGQueryResponse(**result)


# ─────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────

async def _verify_doctor_patient_access(
    db: AsyncIOMotorDatabase,
    patient_id: str,
    doctor_id: str,
) -> None:
    """
    Verify the doctor is the assigned doctor for this patient.

    Reuses the same authorization pattern as patients.py (Phase 1).
    If patient doesn't exist → 404.
    If patient exists but assigned to a different doctor → 403.

    WHY HERE AND NOT IN RAGService:
      Authorization is a route-layer concern.
      The service layer assumes authorization is already done.
      This keeps RAGService testable without auth mocking.
    """
    patient = await db["patients"].find_one(
        {"_id": patient_id},
        {"personal.assigned_doctor_id": 1},
    )

    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient {patient_id} not found",
        )

    assigned_doctor = patient.get("personal", {}).get("assigned_doctor_id")
    if assigned_doctor != doctor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the assigned doctor for this patient",
        )
