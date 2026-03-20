"""
backend/main.py — Phase 3 (Windows-compatible, robust startup)

STARTUP SEQUENCE:
  1. Set Windows SelectorEventLoop policy (no-op on Linux/Mac)
  2. MongoDB connect + indexes
  3. ChromaDB init (non-fatal if fails)
  4. Redis connect — validates URL before connecting (non-fatal)
  5. LangGraph agent graph — validates SUPABASE_DB_URL before connecting (non-fatal)

GRACEFUL DEGRADATION:
  If Redis or Supabase URLs are placeholders / misconfigured, the app
  still starts and serves Phase 1 + 2 endpoints normally.
  Only /api/agents/* will return 503.
"""

import sys

# ── Windows event loop fix — MUST be before all other imports ──
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
# ──────────────────────────────────────────────────────────────

from contextlib import asynccontextmanager
import structlog
import uuid
import redis.asyncio as aioredis

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from backend.core.config import get_settings
from backend.db.mongodb.connection import connect_to_mongodb, disconnect_from_mongodb
from backend.db.mongodb.indexes import create_all_indexes
from backend.db.mongodb.connection import get_database
from backend.api.routes import patients, auth
from backend.api.routes.admin import router as admin_router
from backend.api.routes.rag import router as rag_router
from backend.api.routes.agents import router as agents_router
from backend.api.routes.appointments import router as appointments_router
from backend.api.routes.pdf import router as pdf_router

logger = structlog.get_logger(__name__)


def _is_placeholder(url: str) -> bool:
    """
    Returns True if a URL is still a placeholder value like 'redis://...'
    or 'postgresql://...'. These cause confusing errors — better to skip
    and log a clear warning than to crash with a codec error.
    """
    if not url:
        return True
    placeholder_signals = ["://...", "://…", "<", ">", "your-", "change-me"]
    url_lower = url.lower()
    return any(s in url_lower for s in placeholder_signals)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("app_starting", env=settings.app_env)

    # 1. MongoDB (required — fatal if fails)
    await connect_to_mongodb()
    db = get_database()
    await create_all_indexes(db)

    # 2. ChromaDB (non-fatal)
    try:
        from backend.rag.retrieval.chroma_client import ChromaVisitCollection
        _ = ChromaVisitCollection()
        logger.info("chromadb_ready")
    except Exception as e:
        logger.warning("chromadb_init_failed", error=str(e))

    # 3. Redis (non-fatal — skip if URL is placeholder or unreachable)
    redis_client = None
    redis_url = settings.redis_url

    if _is_placeholder(redis_url):
        logger.warning(
            "redis_skipped",
            reason="REDIS_URL appears to be a placeholder. Set a real URL in .env",
        )
    else:
        try:
            redis_client = aioredis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
            )
            await redis_client.ping()
            app.state.redis = redis_client
            logger.info("redis_connected")
        except Exception as e:
            logger.warning("redis_connection_failed", error=str(e))
            redis_client = None

    app.state.redis = redis_client

    # 4. LangGraph agent graph (non-fatal — skip if Supabase URL is placeholder)
    supabase_url = settings.supabase_db_url

    if _is_placeholder(supabase_url):
        logger.warning(
            "agent_graph_skipped",
            reason=(
                "SUPABASE_DB_URL appears to be a placeholder. "
                "Set a real PostgreSQL connection string in .env to enable agents."
            ),
        )
        app.state.agent_graph = None
    else:
        try:
            from backend.agents.graph import build_graph
            agent_graph = await build_graph(db, redis_client)
            app.state.agent_graph = agent_graph
            logger.info("agent_graph_ready")
        except Exception as e:
            logger.warning(
                "agent_graph_init_failed",
                error=str(e),
                detail="Agents unavailable. Check SUPABASE_DB_URL.",
            )
            app.state.agent_graph = None

    logger.info("app_ready", host=settings.app_host, port=settings.app_port)

    yield

    # ── SHUTDOWN ──────────────────────────────────────────────
    logger.info("app_shutting_down")
    await disconnect_from_mongodb()
    if redis_client:
        await redis_client.close()
    logger.info("app_stopped")


# ─────────────────────────────────────────────────────────────
# CREATE APP
# ─────────────────────────────────────────────────────────────

settings = get_settings()

app = FastAPI(
    title="ClinicCare API",
    description="""
    Enterprise clinic management system with RAG-powered clinical assistant
    and LangGraph agentic workflows.

    ## Roles
    - **doctor**: own patients, RAG queries
    - **admin**: full access + embedding pipeline + agents
    - **receptionist**: patient registration, search, agent chat

    ## Phase 3 — LangGraph Agents
    - POST /api/agents/chat
    - POST /api/agents/webhook
    - GET  /api/agents/thread/{id}
    """,
    version="3.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

# ─────────────────────────────────────────────────────────────
# MIDDLEWARE
# ─────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.is_production:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["your-api-domain.com", "*.render.com"],
    )


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        path=request.url.path,
        method=request.method,
    )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ─────────────────────────────────────────────────────────────
# EXCEPTION HANDLERS
# ─────────────────────────────────────────────────────────────

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────

app.include_router(auth.router, prefix="/api")
app.include_router(patients.router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(rag_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(appointments_router, prefix="/api")
app.include_router(pdf_router, prefix="/api")


# ─────────────────────────────────────────────────────────────
# UTILITY ENDPOINTS
# ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health_check():
    chroma_count = 0
    try:
        from backend.rag.retrieval.chroma_client import ChromaVisitCollection
        chroma_count = ChromaVisitCollection().count()
    except Exception:
        pass

    agent_ready = getattr(app.state, "agent_graph", None) is not None
    redis_ready = getattr(app.state, "redis", None) is not None

    return {
        "status": "healthy",
        "version": "3.0.0",
        "chroma_vectors": chroma_count,
        "redis_connected": redis_ready,
        "agents_ready": agent_ready,
    }


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "ClinicCare API",
        "docs": "/docs",
        "version": "3.0.0",
        "phase": "Phase 3 — LangGraph Agents Active",
    }