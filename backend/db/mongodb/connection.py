"""
backend/db/mongodb/connection.py

WHY MOTOR OVER PYMONGO:
  FastAPI is async. MongoDB queries on Atlas take 50-200ms (network).
  With pymongo (sync): while query runs, FastAPI's event loop is BLOCKED.
  No other requests can be served. Under 10 concurrent users, everything
  freezes.

  With motor (async): FastAPI suspends the coroutine, serves other requests,
  resumes when MongoDB responds. Same hardware handles 10x more traffic.

CONNECTION POOLING:
  Creating a MongoDB connection is expensive (~100ms, SSL handshake).
  A connection pool keeps N connections open and ready.
  Requests borrow a connection, use it, return it.

  Our settings:
  - max_pool_size=10: max 10 concurrent MongoDB operations
  - min_pool_size=2: always keep 2 warm (faster cold response)
  - serverSelectionTimeoutMS=5000: fail fast if Atlas unreachable

KNOWN ISSUE — ATLAS FREE TIER COLD START:
  MongoDB Atlas M0 (free) pauses after 60 minutes of inactivity.
  First connection after pause takes 3-5 seconds instead of <100ms.
  Solution: tenacity retry with exponential backoff (see get_database).
  Alternative: Render's free tier pings the API every 14 minutes
  via a scheduled job to keep Atlas warm.

SINGLETON PATTERN:
  We use a module-level variable _client to store the connection.
  get_client() returns the same client across all requests.
  This is intentional — you NEVER create a new MongoClient per request.
  That would open a new connection pool every time, quickly exhausting
  Atlas's connection limit (500 on M0).
"""

import structlog
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
import logging

from backend.core.config import get_settings

logger = structlog.get_logger(__name__)

# Module-level singleton — shared across entire application lifetime
_client: AsyncIOMotorClient | None = None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
    reraise=True,
)
async def connect_to_mongodb() -> None:
    """
    Initialize the MongoDB connection pool.
    Called once at application startup (see main.py lifespan).

    The @retry decorator handles Atlas cold start:
    - Attempt 1: immediate
    - Attempt 2: wait 1 second
    - Attempt 3: wait 2 seconds
    If all 3 fail, the exception propagates and app startup fails.

    WHY FAIL AT STARTUP:
    Better to fail loudly at startup than to start serving requests
    that will all fail silently. FastAPI's lifespan context makes
    startup failures visible immediately.
    """
    global _client
    settings = get_settings()

    logger.info("connecting_to_mongodb", db=settings.mongodb_db_name)

    _client = AsyncIOMotorClient(
        settings.mongodb_url,
        maxPoolSize=settings.mongodb_max_pool_size,
        minPoolSize=settings.mongodb_min_pool_size,
        # serverSelectionTimeoutMS: how long to wait to find a server
        # Without this, a wrong URL hangs for 30 seconds
        serverSelectionTimeoutMS=settings.mongodb_connect_timeout_ms,
        # connectTimeoutMS: individual socket connection timeout
        connectTimeoutMS=settings.mongodb_connect_timeout_ms,
        # tlsAllowInvalidCertificates: DO NOT set True in production
        # Atlas uses valid certs. Only set if using self-signed cert in dev.
    )

    # Verify connection is actually working
    # ping command is lightweight — just checks server is reachable
    await _client.admin.command("ping")
    logger.info("mongodb_connected", status="success")


async def disconnect_from_mongodb() -> None:
    """
    Close the connection pool cleanly.
    Called at application shutdown (see main.py lifespan).

    Without this, open connections are left dangling on Atlas,
    slowly eating into the connection limit.
    """
    global _client
    if _client:
        _client.close()
        _client = None
        logger.info("mongodb_disconnected")


def get_client() -> AsyncIOMotorClient:
    """
    Returns the motor client.
    Raises RuntimeError if called before connect_to_mongodb().

    This makes the "forgot to initialize" bug immediately obvious
    instead of a cryptic AttributeError on None.
    """
    if _client is None:
        raise RuntimeError(
            "MongoDB client not initialized. "
            "Ensure connect_to_mongodb() ran at startup."
        )
    return _client


def get_database() -> AsyncIOMotorDatabase:
    """
    Returns the database handle.
    All collection access goes through this.

    Usage:
        db = get_database()
        patients = db["patients"]
        await patients.find_one({"_id": patient_id})
    """
    settings = get_settings()
    return get_client()[settings.mongodb_db_name]


# ─────────────────────────────────────────────────────────────
# FASTAPI DEPENDENCY
# ─────────────────────────────────────────────────────────────

async def get_db() -> AsyncIOMotorDatabase:
    """
    FastAPI dependency for database access.
    Inject with: db: AsyncIOMotorDatabase = Depends(get_db)

    Why a dependency instead of importing get_database() directly?
    Testing: in tests, override this dependency to return a test database.
    from fastapi.testclient import TestClient
    app.dependency_overrides[get_db] = lambda: test_db

    This lets us test routes without touching the real Atlas database.
    """
    return get_database()
