"""
tests/conftest.py

DESIGN:
  - Sync pymongo  → seeding users + per-test cleanup (zero event-loop issues)
  - Motor async   → test_db reference used by HTTP client and service tests
                    (Motor creates a connection pool per event loop;
                     each test's function loop gets its own pool — Motor 3.x supports this)
  - No session-scoped async fixtures → avoids loop-mismatch teardown errors

HOW TO RUN:
  pytest tests/                     all tests
  pytest tests/ -m unit             no MongoDB needed
  pytest tests/ -m integration      requires MongoDB
  pytest tests/ -v --tb=short       verbose with short tracebacks
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from typing import AsyncGenerator

from pymongo import MongoClient as SyncMongoClient        # sync, no event loop
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from jose import jwt
from passlib.context import CryptContext

from backend.core.config import get_settings
from backend.db.mongodb.connection import get_db
from backend.api.routes import patients as patients_router_module
from backend.api.routes import auth as auth_router_module
from backend.api.routes.admin import router as admin_router
from backend.api.routes.rag import router as rag_router, get_redis

settings = get_settings()
TEST_DB_NAME = "cliniccare_test"
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ─────────────────────────────────────────────────────────────
# FIXED TEST USER IDs
# ─────────────────────────────────────────────────────────────
DOCTOR_ID  = "USRTESTDOC1"
ADMIN_ID   = "USRTESTADM1"
RECEPT_ID  = "USTRTESTREC1"
DOCTOR2_ID = "USRTESTDOC2"

# ─────────────────────────────────────────────────────────────
# JWT HELPERS
# ─────────────────────────────────────────────────────────────

def make_token(user_id: str, email: str, role: str) -> str:
    payload = {
        "sub": user_id, "email": email, "role": role,
        "exp": datetime.utcnow() + timedelta(hours=8),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


DOCTOR_TOKEN  = make_token(DOCTOR_ID,  "test.doctor@testclinic.com",  "doctor")
ADMIN_TOKEN   = make_token(ADMIN_ID,   "test.admin@testclinic.com",   "admin")
RECEPT_TOKEN  = make_token(RECEPT_ID,  "test.recept@testclinic.com",  "receptionist")
DOCTOR2_TOKEN = make_token(DOCTOR2_ID, "test.doctor2@testclinic.com", "doctor")

DOC_HEADERS    = auth_headers(DOCTOR_TOKEN)
ADMIN_HEADERS  = auth_headers(ADMIN_TOKEN)
RECEPT_HEADERS = auth_headers(RECEPT_TOKEN)
DOC2_HEADERS   = auth_headers(DOCTOR2_TOKEN)

# ─────────────────────────────────────────────────────────────
# TEST USER SEED DATA
# ─────────────────────────────────────────────────────────────

_PASSWORDS = {
    DOCTOR_ID:  "TestDoctor123!",
    DOCTOR2_ID: "TestDoctor2123!",
    ADMIN_ID:   "TestAdmin123!",
    RECEPT_ID:  "TestRecept123!",
}

_TEST_USERS = [
    {
        "_id": DOCTOR_ID,
        "email": "test.doctor@testclinic.com",
        "hashed_password": _pwd_context.hash(_PASSWORDS[DOCTOR_ID]),
        "name": "Dr. Test Doctor",
        "role": "doctor",
        "specialization": "General Practice",
        "is_active": True,
        "created_at": datetime.utcnow(),
    },
    {
        "_id": DOCTOR2_ID,
        "email": "test.doctor2@testclinic.com",
        "hashed_password": _pwd_context.hash(_PASSWORDS[DOCTOR2_ID]),
        "name": "Dr. Second Doctor",
        "role": "doctor",
        "specialization": "Cardiology",
        "is_active": True,
        "created_at": datetime.utcnow(),
    },
    {
        "_id": ADMIN_ID,
        "email": "test.admin@testclinic.com",
        "hashed_password": _pwd_context.hash(_PASSWORDS[ADMIN_ID]),
        "name": "Test Admin",
        "role": "admin",
        "specialization": None,
        "is_active": True,
        "created_at": datetime.utcnow(),
    },
    {
        "_id": RECEPT_ID,
        "email": "test.recept@testclinic.com",
        "hashed_password": _pwd_context.hash(_PASSWORDS[RECEPT_ID]),
        "name": "Test Receptionist",
        "role": "receptionist",
        "specialization": None,
        "is_active": True,
        "created_at": datetime.utcnow(),
    },
]

# ─────────────────────────────────────────────────────────────
# SYNC MONGODB — for seeding + cleanup (no event-loop issues)
# ─────────────────────────────────────────────────────────────

_sync_client: SyncMongoClient | None = None
_sync_db = None


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """
    Session-scoped SYNC fixture.
    Connects via pymongo (sync), seeds test users, drops DB at end.
    Using sync pymongo for setup/teardown avoids ALL event-loop mismatch issues.
    """
    global _sync_client, _sync_db

    try:
        _sync_client = SyncMongoClient(
            settings.mongodb_url,
            serverSelectionTimeoutMS=5000,
        )
        _sync_client.admin.command("ping")          # verify connectivity
    except Exception as e:
        pytest.skip(f"MongoDB not reachable — skipping all tests: {e}")
        return

    _sync_db = _sync_client[TEST_DB_NAME]

    # Upsert test users (idempotent)
    for user in _TEST_USERS:
        _sync_db["users"].replace_one({"_id": user["_id"]}, user, upsert=True)

    yield

    # Drop the entire test database when all tests finish
    _sync_client.drop_database(TEST_DB_NAME)
    _sync_client.close()
    _sync_client = None
    _sync_db = None


@pytest.fixture(autouse=True)
def clean_test_data():
    """
    SYNC per-test cleanup — runs after every test function.
    Deletes all patients and visits created during the test.
    Users are kept (seeded once at session start).
    No async / no event-loop involved.
    """
    yield
    if _sync_db is not None:
        _sync_db["patients"].delete_many({})
        _sync_db["visits"].delete_many({})


# ─────────────────────────────────────────────────────────────
# ASYNC MOTOR CLIENT — function-scoped to match test event loop
# ─────────────────────────────────────────────────────────────
# Motor 3.x caches the event loop per connection pool. When a
# function-scoped test loop closes, any session-scoped Motor client
# that ran on it becomes invalid for the next test's loop.
# Solution: create a fresh Motor client per test function. This is
# safe because the test database is cleaned between tests via sync
# pymongo, and each Motor client closes cleanly after the test.

@pytest.fixture
def motor_client() -> AsyncIOMotorClient:
    """Function-scoped Motor client — matches the test's event loop."""
    client = AsyncIOMotorClient(
        settings.mongodb_url,
        serverSelectionTimeoutMS=5000,
    )
    yield client
    client.close()


@pytest.fixture
def test_db(motor_client: AsyncIOMotorClient) -> AsyncIOMotorDatabase:
    """Function-scoped Motor database (fresh per test)."""
    return motor_client[TEST_DB_NAME]


# ─────────────────────────────────────────────────────────────
# TEST APP + HTTP CLIENT
# ─────────────────────────────────────────────────────────────

def build_test_app(db: AsyncIOMotorDatabase) -> FastAPI:
    """
    Minimal FastAPI app for testing — no lifespan, just routes.
    get_db → overridden with test database
    get_redis → always None (RAGService degrades gracefully)
    """
    test_app = FastAPI(title="ClinicCare Test App")

    async def get_test_db():
        return db

    async def get_no_redis():
        return None

    test_app.dependency_overrides[get_db] = get_test_db
    test_app.dependency_overrides[get_redis] = get_no_redis

    test_app.include_router(auth_router_module.router, prefix="/api")
    test_app.include_router(patients_router_module.router, prefix="/api")
    test_app.include_router(admin_router, prefix="/api")
    test_app.include_router(rag_router, prefix="/api")

    return test_app


@pytest.fixture
async def client(test_db: AsyncIOMotorDatabase) -> AsyncGenerator[AsyncClient, None]:
    """
    Async HTTP test client.
    Function-scoped — each test gets a fresh client.
    Runs entirely within the test's own event loop (no session-loop conflict).
    """
    app = build_test_app(test_db)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ─────────────────────────────────────────────────────────────
# SAMPLE DATA FACTORIES
# ─────────────────────────────────────────────────────────────

def sample_patient_payload(doctor_id: str = DOCTOR_ID, **overrides) -> dict:
    """Valid PatientCreateRequest payload."""
    base = {
        "personal": {
            "name": "John Test Patient",
            "date_of_birth": "1985-06-15",
            "sex": "M",
            "blood_group": "O+",
            "phone": "+919876543210",
            "email": "john.test@example.com",
            "address": "123 Test Street",
            "known_allergies": ["Penicillin"],
            "chronic_conditions": ["Hypertension"],
            "assigned_doctor_id": doctor_id,
        }
    }
    base["personal"].update(overrides)
    return base


def sample_visit_payload(**overrides) -> dict:
    """Valid VisitCreateRequest payload."""
    base = {
        "visit_type": "New complaint",
        "chief_complaint": "Persistent headache for 3 days",
        "symptoms": "Headache, mild fever, neck stiffness",
        "diagnosis": "Tension headache",
        "medications": [
            {
                "name": "Paracetamol",
                "dose": "500mg",
                "frequency": "Twice daily",
                "duration": "5 days",
                "notes": "Take after meals",
            }
        ],
        "notes": "Patient advised to rest and stay hydrated.",
        "followup_required": False,
    }
    base.update(overrides)
    return base
