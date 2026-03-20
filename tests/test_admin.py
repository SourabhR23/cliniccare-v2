"""
tests/test_admin.py  —  Admin embedding pipeline endpoints.

Tests:
  - POST /api/admin/embed-batch   trigger embedding pipeline (mocked)
  - GET  /api/admin/queue         embedding queue status (mocked)
  - POST /api/admin/retry-failed  reset failed → pending (real DB)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from tests.conftest import (
    DOCTOR_ID,
    DOC_HEADERS, ADMIN_HEADERS, RECEPT_HEADERS,
)


MOCK_EMBED_RESULT = {
    "total": 10,
    "embedded": 9,
    "failed": 1,
    "duration_seconds": 3.2,
}

MOCK_QUEUE_STATUS = {
    "pending": 5,
    "embedded": 50,
    "failed": 2,
    "chroma_total": 50,
}


def mock_rag_service_for_admin():
    service = MagicMock()
    service.embed_pending_visits = AsyncMock(return_value=MOCK_EMBED_RESULT)
    service.get_embedding_queue_status = AsyncMock(return_value=MOCK_QUEUE_STATUS)
    return service


@pytest.mark.integration
class TestEmbedBatch:
    """POST /api/admin/embed-batch"""

    async def test_admin_triggers_embed_batch(self, client):
        with patch("backend.api.routes.admin.RAGService", return_value=mock_rag_service_for_admin()):
            resp = await client.post("/api/admin/embed-batch", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert "total" in body
        assert "embedded" in body
        assert "failed" in body
        assert "duration_seconds" in body
        assert "triggered_by" in body
        assert body["triggered_by"] == "test.admin@testclinic.com"

    async def test_admin_embed_batch_with_batch_size(self, client):
        with patch("backend.api.routes.admin.RAGService", return_value=mock_rag_service_for_admin()):
            resp = await client.post(
                "/api/admin/embed-batch?batch_size=50",
                headers=ADMIN_HEADERS,
            )
        assert resp.status_code == 200

    async def test_doctor_cannot_trigger_embed(self, client):
        """Embedding pipeline is admin-only."""
        resp = await client.post("/api/admin/embed-batch", headers=DOC_HEADERS)
        assert resp.status_code == 403

    async def test_receptionist_cannot_trigger_embed(self, client):
        resp = await client.post("/api/admin/embed-batch", headers=RECEPT_HEADERS)
        assert resp.status_code == 403

    async def test_unauthenticated_cannot_embed(self, client):
        resp = await client.post("/api/admin/embed-batch")
        assert resp.status_code == 401

    async def test_embed_batch_size_too_small(self, client):
        """batch_size < 10 → 422 Unprocessable Entity."""
        resp = await client.post(
            "/api/admin/embed-batch?batch_size=5",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422

    async def test_embed_batch_size_too_large(self, client):
        """batch_size > 500 → 422 Unprocessable Entity."""
        resp = await client.post(
            "/api/admin/embed-batch?batch_size=501",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422


@pytest.mark.integration
class TestQueueStatus:
    """GET /api/admin/queue"""

    async def test_admin_gets_queue_status(self, client):
        with patch("backend.api.routes.admin.RAGService", return_value=mock_rag_service_for_admin()):
            resp = await client.get("/api/admin/queue", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert "pending" in body
        assert "embedded" in body
        assert "failed" in body
        assert "chroma_total" in body
        assert isinstance(body["pending"], int)

    async def test_doctor_cannot_view_queue(self, client):
        resp = await client.get("/api/admin/queue", headers=DOC_HEADERS)
        assert resp.status_code == 403

    async def test_receptionist_cannot_view_queue(self, client):
        resp = await client.get("/api/admin/queue", headers=RECEPT_HEADERS)
        assert resp.status_code == 403

    async def test_unauthenticated_cannot_view_queue(self, client):
        resp = await client.get("/api/admin/queue")
        assert resp.status_code == 401


@pytest.mark.integration
class TestRetryFailed:
    """POST /api/admin/retry-failed"""

    async def _seed_failed_visits(self, test_db, n: int = 3) -> list:
        """Insert n visits with embedding_status='failed' into test DB."""
        visit_ids = []
        for i in range(n):
            vid = f"VS_FAIL_{i:03d}"
            await test_db["visits"].insert_one({
                "_id": vid,
                "patient_id": "PT_TEST_ADMIN",
                "doctor_id": DOCTOR_ID,
                "doctor_name": "Dr. Test Doctor",
                "visit_type": "New complaint",
                "chief_complaint": f"Failed visit {i}",
                "symptoms": [],
                "diagnosis": "Test",
                "medications": [],
                "notes": "",
                "followup_required": False,
                "followup_date": None,
                "followup_reason": None,
                "embedding_status": "failed",
                "created_at": datetime.utcnow(),
            })
            visit_ids.append(vid)
        return visit_ids

    async def test_admin_retries_failed_visits(self, client, test_db):
        await self._seed_failed_visits(test_db, n=3)

        resp = await client.post("/api/admin/retry-failed", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert "reset_count" in body
        assert body["reset_count"] == 3

        # Verify they're now 'pending' in DB
        failed_count = await test_db["visits"].count_documents({
            "_id": {"$in": ["VS_FAIL_000", "VS_FAIL_001", "VS_FAIL_002"]},
            "embedding_status": "failed",
        })
        assert failed_count == 0

        pending_count = await test_db["visits"].count_documents({
            "_id": {"$in": ["VS_FAIL_000", "VS_FAIL_001", "VS_FAIL_002"]},
            "embedding_status": "pending",
        })
        assert pending_count == 3

    async def test_retry_when_no_failed_visits(self, client):
        """If no failed visits, reset_count should be 0."""
        resp = await client.post("/api/admin/retry-failed", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["reset_count"] == 0

    async def test_doctor_cannot_retry_failed(self, client):
        resp = await client.post("/api/admin/retry-failed", headers=DOC_HEADERS)
        assert resp.status_code == 403

    async def test_receptionist_cannot_retry_failed(self, client):
        resp = await client.post("/api/admin/retry-failed", headers=RECEPT_HEADERS)
        assert resp.status_code == 403

    async def test_unauthenticated_cannot_retry(self, client):
        resp = await client.post("/api/admin/retry-failed")
        assert resp.status_code == 401
