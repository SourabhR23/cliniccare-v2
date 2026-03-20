"""
tests/test_rag.py  —  RAG endpoints with mocked ChromaDB + OpenAI.

Tests:
  - POST /api/rag/query          clinical query
  - GET  /api/rag/previsit-brief/{patient_id}  pre-visit summary

All ChromaDB and OpenAI calls are mocked — tests run without real embeddings.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import (
    DOCTOR_ID, DOCTOR2_ID, ADMIN_ID,
    DOC_HEADERS, ADMIN_HEADERS, RECEPT_HEADERS, DOC2_HEADERS,
    sample_patient_payload,
)


# ─────────────────────────────────────────────────────────────
# MOCK RAG SERVICE
# ─────────────────────────────────────────────────────────────

MOCK_RAG_RESULT = {
    "answer": "Based on the patient's records, they have no history of respiratory infections.",
    "sources": [
        {
            "chunk_id": "visit_chunk_VS001",
            "text": "Patient presented with cough; diagnosed with common cold.",
            "metadata": {"patient_id": "PT001", "visit_id": "VS001"},
            "score": 0.87,
        }
    ],
    "cached": False,
    "retrieval_count": 1,
}

MOCK_PREVISIT_RESULT = {
    "brief": "Patient is a 40-year-old male with hypertension. Last visit: Jan 2026.",
    "sources": [],
    "cached": False,
}


def mock_rag_service():
    """Create a mock RAGService for injection."""
    service = MagicMock()
    service.query = AsyncMock(return_value=MOCK_RAG_RESULT)
    service.get_previsit_brief = AsyncMock(return_value=MOCK_PREVISIT_RESULT)
    return service


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

async def _create_patient(client, doctor_id: str, headers: dict, phone: str) -> str:
    payload = sample_patient_payload(doctor_id=doctor_id, phone=phone)
    resp = await client.post("/api/patients/", json=payload, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


# ─────────────────────────────────────────────────────────────
# POST /api/rag/query
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestRAGQuery:
    async def test_doctor_query_without_patient_id(self, client):
        """Doctor can query without scoping to a patient (global search)."""
        with patch("backend.api.routes.rag.RAGService", return_value=mock_rag_service()):
            resp = await client.post(
                "/api/rag/query",
                json={"query": "Has this patient had any respiratory infections?"},
                headers=DOC_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "answer" in body
        assert "sources" in body
        assert "cached" in body
        assert "retrieval_count" in body

    async def test_doctor_query_own_patient(self, client):
        """Doctor can query their own patient."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919800000001")

        with patch("backend.api.routes.rag.RAGService", return_value=mock_rag_service()):
            resp = await client.post(
                "/api/rag/query",
                json={
                    "query": "What medications has this patient been prescribed?",
                    "patient_id": pid,
                },
                headers=DOC_HEADERS,
            )
        assert resp.status_code == 200

    async def test_doctor_query_other_doctor_patient_forbidden(self, client):
        """Doctor cannot query another doctor's patient."""
        pid = await _create_patient(client, DOCTOR2_ID, ADMIN_HEADERS, "+919800000002")

        with patch("backend.api.routes.rag.RAGService", return_value=mock_rag_service()):
            resp = await client.post(
                "/api/rag/query",
                json={
                    "query": "What medications?",
                    "patient_id": pid,
                },
                headers=DOC_HEADERS,
            )
        assert resp.status_code == 403

    async def test_admin_query_any_patient(self, client):
        """Admin can query any patient regardless of assigned doctor."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919800000003")

        with patch("backend.api.routes.rag.RAGService", return_value=mock_rag_service()):
            resp = await client.post(
                "/api/rag/query",
                json={
                    "query": "Medical history?",
                    "patient_id": pid,
                },
                headers=ADMIN_HEADERS,
            )
        assert resp.status_code == 200

    async def test_receptionist_cannot_query_rag(self, client):
        """Receptionist does not have RAG access (clinical data restriction)."""
        resp = await client.post(
            "/api/rag/query",
            json={"query": "Medical history?"},
            headers=RECEPT_HEADERS,
        )
        assert resp.status_code == 403

    async def test_rag_query_unauthenticated(self, client):
        resp = await client.post(
            "/api/rag/query",
            json={"query": "Medical history?"},
        )
        assert resp.status_code == 401

    async def test_rag_query_too_short(self, client):
        """Query must be at least 5 characters."""
        resp = await client.post(
            "/api/rag/query",
            json={"query": "Hi"},
            headers=DOC_HEADERS,
        )
        assert resp.status_code == 422

    async def test_rag_query_too_long(self, client):
        """Query exceeding 500 characters → 422."""
        resp = await client.post(
            "/api/rag/query",
            json={"query": "X" * 501},
            headers=DOC_HEADERS,
        )
        assert resp.status_code == 422

    async def test_rag_query_patient_not_found(self, client):
        """Querying with a non-existent patient_id → 404."""
        with patch("backend.api.routes.rag.RAGService", return_value=mock_rag_service()):
            resp = await client.post(
                "/api/rag/query",
                json={"query": "Medical history?", "patient_id": "PT_NONEXISTENT"},
                headers=DOC_HEADERS,
            )
        assert resp.status_code == 404

    async def test_rag_response_structure(self, client):
        """Verify exact response shape matches RAGQueryResponse."""
        with patch("backend.api.routes.rag.RAGService", return_value=mock_rag_service()):
            resp = await client.post(
                "/api/rag/query",
                json={"query": "What are the recent diagnoses?"},
                headers=DOC_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["answer"], str)
        assert isinstance(body["sources"], list)
        assert isinstance(body["cached"], bool)
        assert isinstance(body["retrieval_count"], int)


# ─────────────────────────────────────────────────────────────
# GET /api/rag/previsit-brief/{patient_id}
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestPrevisitBrief:
    async def test_doctor_gets_own_patient_brief(self, client):
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919800001001")

        with patch("backend.api.routes.rag.RAGService", return_value=mock_rag_service()):
            resp = await client.get(
                f"/api/rag/previsit-brief/{pid}",
                headers=DOC_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "brief" in body
        assert "sources" in body
        assert "cached" in body

    async def test_doctor_cannot_get_other_doctor_patient_brief(self, client):
        pid = await _create_patient(client, DOCTOR2_ID, ADMIN_HEADERS, "+919800001002")

        with patch("backend.api.routes.rag.RAGService", return_value=mock_rag_service()):
            resp = await client.get(
                f"/api/rag/previsit-brief/{pid}",
                headers=DOC_HEADERS,
            )
        assert resp.status_code == 403

    async def test_admin_gets_any_patient_brief(self, client):
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919800001003")

        with patch("backend.api.routes.rag.RAGService", return_value=mock_rag_service()):
            resp = await client.get(
                f"/api/rag/previsit-brief/{pid}",
                headers=ADMIN_HEADERS,
            )
        assert resp.status_code == 200

    async def test_receptionist_cannot_get_brief(self, client):
        resp = await client.get("/api/rag/previsit-brief/ANY_ID", headers=RECEPT_HEADERS)
        assert resp.status_code == 403

    async def test_brief_patient_not_found(self, client):
        with patch("backend.api.routes.rag.RAGService", return_value=mock_rag_service()):
            resp = await client.get(
                "/api/rag/previsit-brief/PT_NONEXISTENT",
                headers=DOC_HEADERS,
            )
        assert resp.status_code == 404

    async def test_brief_unauthenticated(self, client):
        resp = await client.get("/api/rag/previsit-brief/SOME_ID")
        assert resp.status_code == 401
