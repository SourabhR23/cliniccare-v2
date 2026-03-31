"""
api/routes/admin.py

ADMIN ROUTES FOR THE EMBEDDING PIPELINE

POST /admin/embed-batch
  Triggers the ingestion pipeline synchronously.
  Fetches all pending visits, embeds, stores in ChromaDB, marks embedded.
  Phase 4 will move this to a Celery background task.

GET /admin/queue
  Returns embedding queue status:
  - how many visits are pending/embedded/failed
  - total vectors in ChromaDB

WHY SYNCHRONOUS (vs background task):
  Phase 2 decision: keep it simple. Celery adds:
  - Redis broker (already available, but more config)
  - Worker process management
  - Task monitoring UI
  In Phase 4, we'll add Celery + scheduled nightly digest.
  For now: admin clicks "Run Embedding" → waits for response.
  For small batches (<500 visits): <30 seconds, acceptable.

ADMIN-ONLY:
  Both routes require admin role (Depends(require_admin)).
  Doctors and receptionists cannot trigger embedding — they can only query.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Header
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, EmailStr
import structlog

from backend.db.mongodb.connection import get_db
from backend.api.middleware.auth_middleware import require_admin
from backend.models.patient import TokenData, UserRoleEnum
from backend.rag.rag_service import RAGService
from backend.services.auth.auth_service import AuthService
from backend.utils.audit import log_audit
from backend.core.config import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin - RAG Pipeline"])


def get_rag_service(db: AsyncIOMotorDatabase = Depends(get_db)) -> RAGService:
    """
    FastAPI dependency: creates RAGService with db injection.

    Redis is optional here — admin routes don't use caching.
    RAGService handles redis=None gracefully (skips cache operations).
    """
    return RAGService(db=db, redis_client=None)


# ─────────────────────────────────────────────────────────────
# POST /admin/embed-batch
# ─────────────────────────────────────────────────────────────

@router.post(
    "/embed-batch",
    summary="Trigger embedding pipeline",
    description="""
    Fetches all visits with embedding_status='pending', embeds them via
    OpenAI text-embedding-3-small, stores vectors in ChromaDB, and marks
    them as 'embedded' in MongoDB.

    **Admin only.** This runs synchronously — expect 10–60 seconds for large batches.
    Phase 4 will move this to a background Celery task.
    """,
    response_model=dict,
)
async def trigger_embed_batch(
    batch_size: int = Query(default=100, ge=10, le=500, description="Visits per OpenAI batch call"),
    x_pipeline_key: Optional[str] = Header(default=None, alias="X-Pipeline-Key"),
    current_user: TokenData = Depends(require_admin),
    rag_service: RAGService = Depends(get_rag_service),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Trigger the ingestion pipeline.

    Query params:
    - batch_size: how many visits per OpenAI embedding call (default 100)
      Lower = safer for rate limits. Higher = faster overall.

    Returns:
    {
        "total": 200,        # total pending visits found
        "embedded": 198,     # successfully embedded
        "failed": 2,         # failed (marked embedding_status="failed")
        "duration_seconds": 12.4,
        "triggered_by": "admin@clinic.com"
    }
    """
    # ── Pipeline lock check ───────────────────────────────────
    settings = get_settings()
    if settings.pipeline_lock_key:
        if x_pipeline_key != settings.pipeline_lock_key:
            logger.warning(
                "embed_batch_locked",
                admin_id=current_user.user_id,
                admin_email=current_user.email,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Pipeline is locked. Access key required.",
            )

    logger.info(
        "embed_batch_triggered",
        admin_id=current_user.user_id,
        admin_email=current_user.email,
        batch_size=batch_size,
    )

    try:
        result = await rag_service.embed_pending_visits(batch_size=batch_size)
    except Exception as e:
        logger.error("embed_batch_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Embedding pipeline failed: {str(e)}",
        )

    result["triggered_by"] = current_user.email
    await log_audit(db, current_user.user_id, current_user.role, current_user.email,
                    "trigger_embed", "system", "embedding_pipeline",
                    {"embedded": result.get("embedded", 0), "failed": result.get("failed", 0),
                     "batch_size": batch_size})
    return result


# ─────────────────────────────────────────────────────────────
# GET /admin/queue
# ─────────────────────────────────────────────────────────────

@router.get(
    "/queue",
    summary="Embedding queue status",
    description="Returns counts of visits by embedding status + ChromaDB total.",
    response_model=dict,
)
async def get_embedding_queue(
    current_user: TokenData = Depends(require_admin),
    rag_service: RAGService = Depends(get_rag_service),
):
    """
    Returns:
    {
        "pending": 45,      # visits waiting to be embedded
        "embedded": 1203,   # successfully embedded visits
        "failed": 3,        # visits that failed embedding (can retry)
        "chroma_total": 1203  # total vectors stored in ChromaDB
    }

    WHY chroma_total separately:
      If MongoDB says 1203 embedded but ChromaDB has 1180,
      there's a sync gap (embedding_status updated but ChromaDB write failed).
      Surfacing both counts lets the admin detect this.
    """
    logger.info("queue_status_requested", admin_id=current_user.user_id)

    try:
        status_counts = await rag_service.get_embedding_queue_status()
    except Exception as e:
        logger.error("queue_status_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch queue status: {str(e)}",
        )

    return status_counts


# ─────────────────────────────────────────────────────────────
# POST /admin/retry-failed
# ─────────────────────────────────────────────────────────────

@router.post(
    "/retry-failed",
    summary="Reset failed visits to pending for retry",
    description="Resets embedding_status from 'failed' back to 'pending' so the next embed-batch run will retry them.",
    response_model=dict,
)
async def retry_failed_embeddings(
    current_user: TokenData = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Resets all failed visits to pending status.
    Run this after fixing the underlying issue (e.g., OpenAI rate limit resolved).
    Then call /admin/embed-batch again.
    """
    from backend.models.patient import EmbeddingStatusEnum

    visits_collection = db["visits"]
    result = await visits_collection.update_many(
        {"embedding_status": EmbeddingStatusEnum.FAILED.value},
        {"$set": {"embedding_status": EmbeddingStatusEnum.PENDING.value}},
    )

    logger.info(
        "failed_visits_reset",
        admin_id=current_user.user_id,
        reset_count=result.modified_count,
    )

    return {
        "reset_count": result.modified_count,
        "message": f"Reset {result.modified_count} failed visits to pending. Run /admin/embed-batch to retry.",
    }


# ─────────────────────────────────────────────────────────────
# GET /admin/sync-check
# ─────────────────────────────────────────────────────────────

@router.get(
    "/sync-check",
    summary="Cross-reference MongoDB pending visits against ChromaDB",
    description="""
    Checks every visit marked **pending** in MongoDB against ChromaDB.

    For each pending visit, it looks up `visit_chunk_{visit_id}` in ChromaDB:
    - **truly_pending** → not in ChromaDB, needs embedding
    - **already_in_chroma** → chunk exists in ChromaDB but MongoDB was never updated (status mismatch)

    Run **/admin/sync-fix** to automatically fix the mismatch.
    """,
    response_model=dict,
)
async def sync_check(
    current_user: TokenData = Depends(require_admin),
    rag_service: RAGService = Depends(get_rag_service),
):
    try:
        return await rag_service.sync_check()
    except Exception as e:
        logger.error("sync_check_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync check failed: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────
# POST /admin/sync-fix
# ─────────────────────────────────────────────────────────────

@router.post(
    "/sync-fix",
    summary="Fix pending visits that are already in ChromaDB",
    description="""
    Finds visits marked **pending** in MongoDB that already exist in ChromaDB,
    then marks them **embedded** — no re-embedding needed, no OpenAI cost.

    Also decrements each patient's `embedding_pending_count`.

    Safe to run multiple times (idempotent).
    """,
    response_model=dict,
)
async def sync_fix(
    current_user: TokenData = Depends(require_admin),
    rag_service: RAGService = Depends(get_rag_service),
):
    try:
        result = await rag_service.sync_fix()
        result["message"] = (
            f"Fixed {result['fixed']} visits. Their status is now 'embedded'."
            if result["fixed"]
            else "No mismatches found. Everything is already in sync."
        )
        return result
    except Exception as e:
        logger.error("sync_fix_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync fix failed: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────
# GET /admin/agent-stats
# ─────────────────────────────────────────────────────────────

@router.get(
    "/agent-stats",
    summary="Agent observability — aggregated metrics",
    description="Returns call counts, latency, token usage, fallback/error rates, and warnings.",
    response_model=dict,
)
async def get_agent_stats(
    days: int = Query(default=7, ge=1, le=90, description="Lookback window in days"),
    current_user: TokenData = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ── Overall stats ──────────────────────────────────────────
    overall_cursor = db["agent_logs"].aggregate([
        {"$match": {"timestamp": {"$gte": since}}},
        {"$group": {
            "_id": None,
            "total_calls": {"$sum": 1},
            "avg_latency_ms": {"$avg": "$latency_ms"},
            "max_latency_ms": {"$max": "$latency_ms"},
            "total_input_tokens": {"$sum": "$input_tokens"},
            "total_output_tokens": {"$sum": "$output_tokens"},
            "fallback_count": {"$sum": {"$cond": ["$fallback", 1, 0]}},
            "error_count": {"$sum": {"$cond": [{"$ne": ["$error", None]}, 1, 0]}},
        }},
    ])
    overall_list = await overall_cursor.to_list(1)
    overall = overall_list[0] if overall_list else {}
    if overall:
        overall.pop("_id", None)
        overall["avg_latency_ms"] = round(overall.get("avg_latency_ms") or 0)
        overall["max_latency_ms"] = round(overall.get("max_latency_ms") or 0)
        total = overall["total_calls"]
        overall["fallback_rate"] = round(overall["fallback_count"] / total, 4) if total else 0
        overall["error_rate"] = round(overall["error_count"] / total, 4) if total else 0

    # ── Per-agent breakdown ────────────────────────────────────
    by_agent_cursor = db["agent_logs"].aggregate([
        {"$match": {"timestamp": {"$gte": since}}},
        {"$group": {
            "_id": "$agent",
            "call_count": {"$sum": 1},
            "avg_latency_ms": {"$avg": "$latency_ms"},
            "max_latency_ms": {"$max": "$latency_ms"},
            "total_input_tokens": {"$sum": "$input_tokens"},
            "total_output_tokens": {"$sum": "$output_tokens"},
            "error_count": {"$sum": {"$cond": [{"$ne": ["$error", None]}, 1, 0]}},
            "fallback_count": {"$sum": {"$cond": ["$fallback", 1, 0]}},
        }},
        {"$sort": {"call_count": -1}},
    ])
    by_agent = await by_agent_cursor.to_list(20)
    for row in by_agent:
        row["agent"] = row.pop("_id")
        row["avg_latency_ms"] = round(row.get("avg_latency_ms") or 0)
        row["max_latency_ms"] = round(row.get("max_latency_ms") or 0)

    # ── Warnings ───────────────────────────────────────────────
    warnings = []
    if overall:
        avg_lat = overall["avg_latency_ms"]
        max_lat = overall["max_latency_ms"]
        fbr = overall["fallback_rate"]
        err = overall["error_rate"]

        if avg_lat > 8000:
            warnings.append({"level": "warning", "message": f"High average latency: {avg_lat}ms"})
        if max_lat > 20000:
            warnings.append({"level": "warning", "message": f"Latency spike: {max_lat}ms max in period"})
        if fbr > 0.15:
            warnings.append({"level": "warning", "message": f"High fallback rate: {fbr:.0%} — supervisor may need tuning"})
        if err > 0.05:
            warnings.append({"level": "error", "message": f"Elevated error rate: {err:.0%}"})

        # Per-agent warnings
        for row in by_agent:
            if row["error_count"] > 0 and row["agent"] in ("NOTIFICATION", "NotificationAgent"):
                warnings.append({"level": "warning", "message": f"SMTP errors: {row['error_count']} notification failure(s)"})
            if row["avg_latency_ms"] > 12000:
                warnings.append({"level": "warning", "message": f"{row['agent']} avg latency: {row['avg_latency_ms']}ms"})

    logger.info("agent_stats_requested", admin_id=current_user.user_id, days=days)

    return {
        "days": days,
        "since": since.isoformat(),
        "overall": overall,
        "by_agent": by_agent,
        "warnings": warnings,
    }


# ─────────────────────────────────────────────────────────────
# GET /admin/agent-logs
# ─────────────────────────────────────────────────────────────

@router.get(
    "/agent-logs",
    summary="Agent observability — raw log entries",
    description="Returns recent agent call logs, newest first. Filterable by agent name and staff role.",
    response_model=dict,
)
async def get_agent_logs(
    limit: int = Query(default=50, ge=1, le=200),
    agent: str = Query(default=None, description="Filter by agent name, e.g. CALENDAR"),
    role: str = Query(default=None, description="Filter by staff role: doctor | receptionist | admin"),
    current_user: TokenData = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    filter_q: dict = {}
    if agent:
        filter_q["agent"] = agent
    if role:
        filter_q["staff_role"] = role

    cursor = db["agent_logs"].find(filter_q, {"_id": 0}).sort("timestamp", -1).limit(limit)
    logs = await cursor.to_list(limit)

    for log in logs:
        if "timestamp" in log and isinstance(log["timestamp"], datetime):
            log["timestamp"] = log["timestamp"].isoformat()

    return {"logs": logs, "count": len(logs)}


# ─────────────────────────────────────────────────────────────
# GET /admin/users  — list all staff users
# POST /admin/users — create a new staff user
# PATCH /admin/users/{id} — update name / role / is_active
# ─────────────────────────────────────────────────────────────

class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: UserRoleEnum
    specialization: Optional[str] = None


class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[UserRoleEnum] = None
    is_active: Optional[bool] = None
    specialization: Optional[str] = None


@router.get("/users", summary="List all staff users", response_model=dict)
async def list_users(
    current_user: TokenData = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    cursor = db["users"].find(
        {},
        {"_id": 1, "name": 1, "email": 1, "role": 1, "specialization": 1, "is_active": 1, "created_at": 1},
    ).sort("name", 1)
    docs = await cursor.to_list(500)
    users = []
    for d in docs:
        users.append({
            "id": str(d["_id"]),
            "name": d.get("name", ""),
            "email": d.get("email", ""),
            "role": d.get("role", ""),
            "specialization": d.get("specialization"),
            "is_active": d.get("is_active", True),
        })
    logger.info("admin_list_users", admin_id=current_user.user_id, count=len(users))
    return {"users": users, "count": len(users)}


@router.post("/users", summary="Create a new staff user", response_model=dict, status_code=201)
async def create_user(
    body: UserCreateRequest,
    current_user: TokenData = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    existing = await db["users"].find_one({"email": body.email})
    if existing:
        raise HTTPException(status_code=409, detail=f"Email {body.email} already exists")

    import uuid
    auth_service = AuthService(db)
    hashed = auth_service.hash_password(body.password)
    user_id = "USR" + uuid.uuid4().hex[:8].upper()
    doc = {
        "_id": user_id,
        "email": body.email,
        "hashed_password": hashed,
        "name": body.name,
        "role": body.role,
        "specialization": body.specialization,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    await db["users"].insert_one(doc)
    logger.info("admin_create_user", admin_id=current_user.user_id, new_user_id=user_id, role=body.role)
    await log_audit(db, current_user.user_id, current_user.role, current_user.email,
                    "create_user", "user", user_id,
                    {"name": body.name, "email": body.email, "role": str(body.role)})
    return {"id": user_id, "name": body.name, "email": body.email, "role": body.role}


@router.patch("/users/{user_id}", summary="Update a staff user", response_model=dict)
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    current_user: TokenData = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    existing = await db["users"].find_one({"_id": user_id})
    if not existing:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    updates: dict = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.role is not None:
        updates["role"] = body.role
    if body.is_active is not None:
        updates["is_active"] = body.is_active
    if body.specialization is not None:
        updates["specialization"] = body.specialization

    if updates:
        await db["users"].update_one({"_id": user_id}, {"$set": updates})

    logger.info("admin_update_user", admin_id=current_user.user_id, target_user_id=user_id, fields=list(updates.keys()))
    await log_audit(db, current_user.user_id, current_user.role, current_user.email,
                    "update_user", "user", user_id,
                    {"fields": list(updates.keys())})
    return {"id": user_id, "updated": list(updates.keys())}


# ─────────────────────────────────────────────────────────────
# GET /admin/audit-logs
# ─────────────────────────────────────────────────────────────

@router.get(
    "/audit-logs",
    summary="Audit log — all data change events",
    description="Returns audit log entries (patient/visit/user create/update/delete). Filterable by action or resource type.",
    response_model=dict,
)
async def get_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    action: Optional[str] = Query(default=None, description="Filter by action, e.g. create_patient"),
    resource_type: Optional[str] = Query(default=None, description="Filter: patient | visit | user | system"),
    actor_id: Optional[str] = Query(default=None, description="Filter by actor user ID"),
    current_user: TokenData = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    filter_q: dict = {}
    if action:
        filter_q["action"] = action
    if resource_type:
        filter_q["resource_type"] = resource_type
    if actor_id:
        filter_q["actor_id"] = actor_id

    cursor = db["audit_logs"].find(filter_q, {"_id": 0}).sort("timestamp", -1).limit(limit)
    logs = await cursor.to_list(limit)

    for log in logs:
        if "timestamp" in log and isinstance(log["timestamp"], datetime):
            log["timestamp"] = log["timestamp"].isoformat()

    logger.info("audit_logs_requested", admin_id=current_user.user_id, count=len(logs))
    return {"logs": logs, "count": len(logs)}


# ─────────────────────────────────────────────────────────────
# GET /admin/analytics
# ─────────────────────────────────────────────────────────────

@router.get(
    "/analytics",
    summary="Patient & visit analytics for admin dashboard",
    description="Monthly patient registrations, visit counts, top diagnoses, doctor utilization.",
    response_model=dict,
)
async def get_analytics(
    months: int = Query(default=6, ge=1, le=24, description="Lookback window in months"),
    current_user: TokenData = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from datetime import date
    import calendar

    today = date.today()

    # Build list of (year, month) for the lookback window
    month_labels = []
    for i in range(months - 1, -1, -1):
        yr = today.year
        mo = today.month - i
        while mo <= 0:
            mo += 12
            yr -= 1
        month_labels.append((yr, mo))

    def month_str(yr, mo):
        return f"{yr}-{mo:02d}"

    # ── Monthly patient registrations ────────────────────────
    patients_by_month_cursor = db["patients"].aggregate([
        {"$addFields": {"reg_month": {"$substr": ["$personal.registered_date", 0, 7]}}},
        {"$group": {"_id": "$reg_month", "count": {"$sum": 1}}},
    ])
    patients_raw = {d["_id"]: d["count"] async for d in patients_by_month_cursor}

    # ── Monthly visit counts ─────────────────────────────────
    visits_by_month_cursor = db["visits"].aggregate([
        {"$addFields": {"visit_month": {"$substr": ["$visit_date", 0, 7]}}},
        {"$group": {"_id": "$visit_month", "count": {"$sum": 1}}},
    ])
    visits_raw = {d["_id"]: d["count"] async for d in visits_by_month_cursor}

    # Assemble time-series arrays in order
    monthly_patients = []
    monthly_visits = []
    for yr, mo in month_labels:
        key = month_str(yr, mo)
        monthly_patients.append({"month": key, "count": patients_raw.get(key, 0)})
        monthly_visits.append({"month": key, "count": visits_raw.get(key, 0)})

    # ── Top 8 diagnoses (all-time) ───────────────────────────
    top_diag_cursor = db["visits"].aggregate([
        {"$match": {"diagnosis": {"$exists": True, "$ne": None, "$ne": ""}}},
        {"$group": {"_id": "$diagnosis", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 8},
    ])
    top_diagnoses = [{"diagnosis": d["_id"], "count": d["count"]}
                     async for d in top_diag_cursor]

    # ── Doctor utilization (visit counts per doctor) ─────────
    doc_util_cursor = db["visits"].aggregate([
        {"$group": {"_id": "$doctor_id", "visits": {"$sum": 1}, "doctor_name": {"$first": "$doctor_name"}}},
        {"$sort": {"visits": -1}},
        {"$limit": 10},
    ])
    doctor_utilization = [{"doctor_id": d["_id"], "doctor_name": d.get("doctor_name", d["_id"]),
                           "visits": d["visits"]}
                          async for d in doc_util_cursor]

    # ── Totals ────────────────────────────────────────────────
    total_patients = await db["patients"].count_documents({})
    total_visits = await db["visits"].count_documents({})
    total_users = await db["users"].count_documents({})

    logger.info("analytics_requested", admin_id=current_user.user_id, months=months)

    return {
        "months": months,
        "total_patients": total_patients,
        "total_visits": total_visits,
        "total_staff": total_users,
        "monthly_patients": monthly_patients,
        "monthly_visits": monthly_visits,
        "top_diagnoses": top_diagnoses,
        "doctor_utilization": doctor_utilization,
    }
