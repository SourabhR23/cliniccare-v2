"""
tests/test_auth.py  —  Authentication: service unit tests + endpoint integration tests.

Tests:
  - AuthService: password hashing, token creation, token decode
  - POST /api/auth/login: valid, wrong password, inactive user
  - POST /api/auth/register: admin only, duplicate email
"""

import pytest
from datetime import datetime, timedelta
from jose import jwt, JWTError

from backend.core.config import get_settings
from backend.services.auth.auth_service import AuthService
from backend.models.patient import UserRoleEnum, UserDocument

from tests.conftest import (
    DOCTOR_ID, ADMIN_ID, RECEPT_ID,
    DOC_HEADERS, ADMIN_HEADERS, RECEPT_HEADERS,
)

settings = get_settings()


# ─────────────────────────────────────────────────────────────
# AUTH SERVICE — UNIT TESTS (no HTTP, direct service calls)
# ─────────────────────────────────────────────────────────────

class TestAuthServicePasswords:
    """Password hashing & verification — pure unit tests."""

    @pytest.fixture
    def auth_service(self, test_db):
        return AuthService(test_db)

    def test_hash_password_not_plain(self, auth_service):
        hashed = auth_service.hash_password("secret123")
        assert hashed != "secret123"
        assert hashed.startswith("$2b$")

    def test_verify_correct_password(self, auth_service):
        hashed = auth_service.hash_password("mypassword")
        assert auth_service.verify_password("mypassword", hashed) is True

    def test_verify_wrong_password(self, auth_service):
        hashed = auth_service.hash_password("mypassword")
        assert auth_service.verify_password("wrongpassword", hashed) is False

    def test_hash_is_unique_per_call(self, auth_service):
        """bcrypt should produce different salts each call."""
        h1 = auth_service.hash_password("same")
        h2 = auth_service.hash_password("same")
        assert h1 != h2  # different salts → different hashes


class TestAuthServiceTokens:
    """JWT creation & decode — pure unit tests."""

    @pytest.fixture
    def auth_service(self, test_db):
        return AuthService(test_db)

    @pytest.fixture
    def sample_user(self):
        return UserDocument(
            **{"_id": "USR_SAMPLE"},
            email="sample@test.com",
            hashed_password="$2b$12$dummy",
            name="Sample User",
            role=UserRoleEnum.DOCTOR,
        )

    def test_create_access_token_structure(self, auth_service, sample_user):
        token = auth_service.create_access_token(sample_user)
        assert token.access_token
        assert token.token_type == "bearer"
        assert token.user.email == "sample@test.com"
        assert token.user.role == "doctor"

    def test_token_payload_fields(self, auth_service, sample_user):
        token = auth_service.create_access_token(sample_user)
        payload = jwt.decode(
            token.access_token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        assert payload["sub"] == "USR_SAMPLE"
        assert payload["email"] == "sample@test.com"
        assert payload["role"] == "doctor"
        assert "exp" in payload
        assert "iat" in payload

    def test_decode_token_valid(self, auth_service, sample_user):
        token = auth_service.create_access_token(sample_user)
        token_data = auth_service.decode_token(token.access_token)
        assert token_data.user_id == "USR_SAMPLE"
        assert token_data.email == "sample@test.com"
        assert token_data.role == "doctor"

    def test_decode_token_invalid_signature(self, auth_service):
        bad_token = jwt.encode(
            {"sub": "x", "email": "x@x.com", "role": "doctor"},
            "wrong-secret-key",
            algorithm=settings.algorithm,
        )
        with pytest.raises(JWTError):
            auth_service.decode_token(bad_token)

    def test_decode_token_expired(self, auth_service):
        expired_payload = {
            "sub": "x",
            "email": "x@x.com",
            "role": "doctor",
            "exp": datetime.utcnow() - timedelta(hours=1),  # already expired
        }
        expired_token = jwt.encode(
            expired_payload, settings.secret_key, algorithm=settings.algorithm
        )
        with pytest.raises(JWTError):
            auth_service.decode_token(expired_token)

    def test_decode_token_missing_fields(self, auth_service):
        """Token without 'role' should raise JWTError."""
        bad_payload = jwt.encode(
            {"sub": "x", "email": "x@x.com"},  # missing 'role'
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        with pytest.raises(JWTError):
            auth_service.decode_token(bad_payload)


# ─────────────────────────────────────────────────────────────
# POST /api/auth/login — ENDPOINT TESTS
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestLoginEndpoint:
    async def test_login_valid_doctor(self, client):
        """Doctor with correct credentials gets a JWT token."""
        resp = await client.post(
            "/api/auth/login",
            data={"username": "test.doctor@testclinic.com", "password": "TestDoctor123!"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["user"]["role"] == "doctor"
        assert body["user"]["email"] == "test.doctor@testclinic.com"

    async def test_login_valid_admin(self, client):
        resp = await client.post(
            "/api/auth/login",
            data={"username": "test.admin@testclinic.com", "password": "TestAdmin123!"},
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "admin"

    async def test_login_wrong_password(self, client):
        resp = await client.post(
            "/api/auth/login",
            data={"username": "test.doctor@testclinic.com", "password": "WrongPassword"},
        )
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]

    async def test_login_nonexistent_email(self, client):
        resp = await client.post(
            "/api/auth/login",
            data={"username": "nobody@nowhere.com", "password": "anything"},
        )
        assert resp.status_code == 401

    async def test_login_empty_credentials(self, client):
        """Missing form fields → 422 Unprocessable Entity."""
        resp = await client.post("/api/auth/login", data={})
        assert resp.status_code == 422

    async def test_login_returns_expires_in(self, client):
        resp = await client.post(
            "/api/auth/login",
            data={"username": "test.admin@testclinic.com", "password": "TestAdmin123!"},
        )
        assert resp.status_code == 200
        assert resp.json()["expires_in"] > 0

    async def test_login_inactive_user(self, client, test_db):
        """Deactivated user should get 401."""
        # Create and deactivate a user
        await test_db["users"].insert_one({
            "_id": "USR_INACTIVE",
            "email": "inactive@testclinic.com",
            "hashed_password": AuthService(test_db).hash_password("Pass123!"),
            "name": "Inactive User",
            "role": "doctor",
            "specialization": None,
            "is_active": False,
            "created_at": datetime.utcnow(),
        })

        resp = await client.post(
            "/api/auth/login",
            data={"username": "inactive@testclinic.com", "password": "Pass123!"},
        )
        assert resp.status_code == 401
        assert "deactivated" in resp.json()["detail"].lower()

        await test_db["users"].delete_one({"_id": "USR_INACTIVE"})


# ─────────────────────────────────────────────────────────────
# POST /api/auth/register — ENDPOINT TESTS
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestRegisterEndpoint:
    async def test_register_user_as_admin(self, client):
        """Admin can create a new user."""
        resp = await client.post(
            "/api/auth/register",
            json={
                "email": "newdoctor@testclinic.com",
                "password": "NewDoc123!",
                "name": "New Doctor",
                "role": "doctor",
                "specialization": "Dermatology",
            },
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["email"] == "newdoctor@testclinic.com"
        assert body["role"] == "doctor"
        assert "id" in body

    async def test_register_user_as_doctor_forbidden(self, client):
        """Doctor cannot register new users (admin-only endpoint)."""
        resp = await client.post(
            "/api/auth/register",
            json={"email": "x@test.com", "password": "Pass", "name": "X", "role": "doctor"},
            headers=DOC_HEADERS,
        )
        assert resp.status_code == 403

    async def test_register_user_as_receptionist_forbidden(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={"email": "x@test.com", "password": "Pass", "name": "X", "role": "doctor"},
            headers=RECEPT_HEADERS,
        )
        assert resp.status_code == 403

    async def test_register_without_auth(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={"email": "x@test.com", "password": "Pass", "name": "X", "role": "doctor"},
        )
        assert resp.status_code == 401

    async def test_register_duplicate_email(self, client):
        """Registering with an existing email → 409 Conflict."""
        resp = await client.post(
            "/api/auth/register",
            json={
                "email": "test.doctor@testclinic.com",  # already exists
                "password": "AnotherPass123!",
                "name": "Duplicate Doctor",
                "role": "doctor",
            },
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 409

    async def test_register_invalid_role(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={"email": "x@test.com", "password": "Pass", "name": "X", "role": "superadmin"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422
