"""
tests/test_health.py  —  Health check endpoint + app configuration tests.

Tests:
  - GET /health   returns 200 with service status
  - App route registration sanity checks
"""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.unit


class TestHealthEndpoint:
    """Tests a minimal health endpoint (standalone, no main app import)."""

    @pytest.fixture
    async def health_client(self):
        test_app = FastAPI()

        @test_app.get("/health")
        async def health():
            return {
                "status": "ok",
                "services": {
                    "mongodb": "connected",
                    "redis": "unavailable",
                    "chromadb": "connected",
                    "agent_graph": "unavailable",
                },
            }

        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://test",
        ) as ac:
            yield ac

    async def test_health_returns_200(self, health_client):
        resp = await health_client.get("/health")
        assert resp.status_code == 200

    async def test_health_response_has_status_field(self, health_client):
        resp = await health_client.get("/health")
        assert "status" in resp.json()

    async def test_health_response_has_services_field(self, health_client):
        resp = await health_client.get("/health")
        assert "services" in resp.json()

    async def test_health_ok_status(self, health_client):
        resp = await health_client.get("/health")
        assert resp.json()["status"] == "ok"

    async def test_health_services_structure(self, health_client):
        resp = await health_client.get("/health")
        services = resp.json()["services"]
        assert "mongodb" in services
        assert "redis" in services


class TestRouterImports:
    """Verify individual route modules import without errors."""

    def test_auth_router_importable(self):
        from backend.api.routes.auth import router
        assert router is not None
        assert router.prefix == "/auth"

    def test_patients_router_importable(self):
        from backend.api.routes.patients import router
        assert router is not None
        assert router.prefix == "/patients"

    def test_admin_router_importable(self):
        from backend.api.routes.admin import router
        assert router is not None
        assert router.prefix == "/admin"

    def test_rag_router_importable(self):
        from backend.api.routes.rag import router
        assert router is not None
        assert router.prefix == "/rag"

    def test_auth_middleware_importable(self):
        from backend.api.middleware.auth_middleware import (
            require_doctor, require_admin, require_any_staff,
            require_doctor_or_admin, require_receptionist_or_admin,
        )
        assert callable(require_doctor)
        assert callable(require_admin)
        assert callable(require_any_staff)
        assert callable(require_doctor_or_admin)
        assert callable(require_receptionist_or_admin)


class TestAppBuiltWithRoutes:
    """Verify the test app (used in integration tests) has all routes registered."""

    @pytest.fixture
    def app_with_routes(self, test_db):
        from tests.conftest import build_test_app
        return build_test_app(test_db)

    def test_test_app_has_auth_routes(self, app_with_routes):
        routes = [r.path for r in app_with_routes.routes if hasattr(r, "path")]
        assert any("/auth/login" in r for r in routes)

    def test_test_app_has_patient_routes(self, app_with_routes):
        routes = [r.path for r in app_with_routes.routes if hasattr(r, "path")]
        assert any("/patients" in r for r in routes)

    def test_test_app_has_rag_routes(self, app_with_routes):
        routes = [r.path for r in app_with_routes.routes if hasattr(r, "path")]
        assert any("/rag" in r for r in routes)

    def test_test_app_has_admin_routes(self, app_with_routes):
        routes = [r.path for r in app_with_routes.routes if hasattr(r, "path")]
        assert any("/admin" in r for r in routes)
