"""
tests/test_chroma_client.py  —  ChromaHTTPClient unit tests.

All HTTP requests are mocked with unittest.mock.
No real ChromaDB connection required.

Tests:
  - get_or_create_collection: found / not found (creates)
  - count
  - upsert
  - query (single result, multiple results)
  - delete
  - HTTP error propagation
  - ChromaVisitCollection wrapper methods
"""

import pytest
from unittest.mock import MagicMock, patch, call
import requests

from backend.rag.retrieval.chroma_client import ChromaHTTPClient, ChromaVisitCollection

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def make_response(status_code: int, json_data) -> MagicMock:
    """Create a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = (200 <= status_code < 300)
    resp.json.return_value = json_data
    resp.text = str(json_data)
    if not resp.ok:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"HTTP {status_code}", response=resp
        )
    return resp


@pytest.fixture
def chroma_client():
    """ChromaHTTPClient with mocked requests.Session."""
    with patch("backend.rag.retrieval.chroma_client.requests.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        client = ChromaHTTPClient(
            api_key="test-api-key",
            tenant="test-tenant",
            database="test-database",
        )
        client._session = mock_session
        yield client, mock_session


# ─────────────────────────────────────────────────────────────
# ChromaHTTPClient TESTS
# ─────────────────────────────────────────────────────────────

class TestGetOrCreateCollection:
    def test_collection_exists_returns_id(self, chroma_client):
        client, session = chroma_client
        col_data = {"id": "col-abc-123", "name": "clinic_visits"}
        session.get.return_value = make_response(200, col_data)

        col_id = client.get_or_create_collection("clinic_visits", {})

        assert col_id == "col-abc-123"
        session.get.assert_called_once()
        session.post.assert_not_called()

    def test_collection_not_found_creates_new(self, chroma_client):
        client, session = chroma_client

        # First GET → 404
        not_found = make_response(404, {"error": "not found"})
        # POST → 201 with new collection
        created = make_response(201, {"id": "col-new-456", "name": "clinic_visits"})

        session.get.return_value = not_found
        session.post.return_value = created

        col_id = client.get_or_create_collection(
            "clinic_visits",
            {"hnsw:space": "cosine"},
        )

        assert col_id == "col-new-456"
        session.get.assert_called_once()
        session.post.assert_called_once()

    def test_collection_server_error_raises(self, chroma_client):
        client, session = chroma_client
        session.get.return_value = make_response(500, {"error": "internal server error"})

        with pytest.raises(requests.HTTPError):
            client.get_or_create_collection("clinic_visits", {})


class TestCount:
    def test_count_returns_integer(self, chroma_client):
        client, session = chroma_client
        session.get.return_value = make_response(200, 42)

        result = client.count("col-abc-123")

        assert result == 42
        session.get.assert_called_once()
        assert "col-abc-123/count" in session.get.call_args[0][0]

    def test_count_zero(self, chroma_client):
        client, session = chroma_client
        session.get.return_value = make_response(200, 0)

        assert client.count("col-id") == 0


class TestUpsert:
    def test_upsert_sends_correct_payload(self, chroma_client):
        client, session = chroma_client
        session.post.return_value = make_response(200, {})

        client.upsert(
            collection_id="col-123",
            ids=["chunk_001"],
            embeddings=[[0.1, 0.2, 0.3]],
            documents=["Patient had fever."],
            metadatas=[{"patient_id": "PT001", "visit_id": "VS001"}],
        )

        session.post.assert_called_once()
        call_args = session.post.call_args
        url = call_args[0][0]
        body = call_args[1]["json"]

        assert "col-123/upsert" in url
        assert body["ids"] == ["chunk_001"]
        assert body["embeddings"] == [[0.1, 0.2, 0.3]]
        assert body["documents"] == ["Patient had fever."]
        assert body["metadatas"][0]["patient_id"] == "PT001"

    def test_upsert_batch_multiple_items(self, chroma_client):
        client, session = chroma_client
        session.post.return_value = make_response(200, {})

        ids = [f"chunk_{i:03d}" for i in range(5)]
        vectors = [[float(i)] * 3 for i in range(5)]
        docs = [f"Doc {i}" for i in range(5)]
        metas = [{"visit_id": f"VS{i:03d}"} for i in range(5)]

        client.upsert("col-123", ids, vectors, docs, metas)

        body = session.post.call_args[1]["json"]
        assert len(body["ids"]) == 5
        assert len(body["embeddings"]) == 5


class TestQuery:
    def _mock_query_response(self):
        return {
            "ids": [["chunk_001", "chunk_002"]],
            "documents": [["Patient had fever.", "Patient has diabetes."]],
            "metadatas": [[
                {"patient_id": "PT001", "visit_id": "VS001"},
                {"patient_id": "PT001", "visit_id": "VS002"},
            ]],
            "distances": [[0.12, 0.25]],
        }

    def test_query_returns_formatted_results(self, chroma_client):
        client, session = chroma_client
        session.post.return_value = make_response(200, self._mock_query_response())

        results = client.query(
            collection_id="col-123",
            query_embeddings=[[0.1] * 3],
            n_results=2,
            include=["documents", "metadatas", "distances"],
        )

        assert results["ids"] == [["chunk_001", "chunk_002"]]
        assert len(results["documents"][0]) == 2

    def test_query_with_where_filter(self, chroma_client):
        client, session = chroma_client
        session.post.return_value = make_response(200, self._mock_query_response())

        client.query(
            collection_id="col-123",
            query_embeddings=[[0.0] * 3],
            n_results=5,
            include=["documents", "metadatas", "distances"],
            where={"patient_id": {"$eq": "PT001"}},
        )

        body = session.post.call_args[1]["json"]
        assert body["where"] == {"patient_id": {"$eq": "PT001"}}

    def test_query_without_where_filter(self, chroma_client):
        client, session = chroma_client
        session.post.return_value = make_response(200, self._mock_query_response())

        client.query(
            collection_id="col-123",
            query_embeddings=[[0.0] * 3],
            n_results=5,
            include=["documents", "metadatas", "distances"],
            where=None,
        )

        body = session.post.call_args[1]["json"]
        assert "where" not in body  # where should NOT be sent when None


class TestDelete:
    def test_delete_sends_ids(self, chroma_client):
        client, session = chroma_client
        session.post.return_value = make_response(200, {})

        client.delete("col-123", ids=["chunk_001", "chunk_002"])

        body = session.post.call_args[1]["json"]
        assert body["ids"] == ["chunk_001", "chunk_002"]
        assert "col-123/delete" in session.post.call_args[0][0]

    def test_delete_error_raises(self, chroma_client):
        client, session = chroma_client
        session.post.return_value = make_response(404, {"error": "collection not found"})

        with pytest.raises(requests.HTTPError):
            client.delete("col-123", ids=["chunk_001"])


# ─────────────────────────────────────────────────────────────
# ChromaVisitCollection WRAPPER TESTS
# ─────────────────────────────────────────────────────────────

class TestChromaVisitCollection:
    """Test ChromaVisitCollection (the high-level wrapper)."""

    @pytest.fixture
    def collection(self):
        """
        ChromaVisitCollection with fully mocked ChromaHTTPClient.
        Bypasses __init__ to avoid calling the constructor's collection setup.
        """
        with patch("backend.rag.retrieval.chroma_client.get_chroma_client") as mock_get_client:
            mock_client = MagicMock(spec=ChromaHTTPClient)
            mock_client.get_or_create_collection.return_value = "col-test-id"
            mock_client.count.return_value = 5
            mock_get_client.return_value = mock_client

            with patch("backend.rag.retrieval.chroma_client.get_settings") as mock_settings:
                mock_settings.return_value.chroma_collection_name = "clinic_visits"
                coll = ChromaVisitCollection()
                coll._client = mock_client
                coll._col_id = "col-test-id"
                yield coll, mock_client

    def test_upsert_single(self, collection):
        coll, mock_client = collection
        coll.upsert("chunk_001", [0.1, 0.2], "Test text", {"visit_id": "VS001"})
        mock_client.upsert.assert_called_once_with(
            collection_id="col-test-id",
            ids=["chunk_001"],
            embeddings=[[0.1, 0.2]],
            documents=["Test text"],
            metadatas=[{"visit_id": "VS001"}],
        )

    def test_upsert_batch_empty_no_op(self, collection):
        coll, mock_client = collection
        coll.upsert_batch([], [], [], [])
        mock_client.upsert.assert_not_called()

    def test_upsert_batch_with_data(self, collection):
        coll, mock_client = collection
        coll.upsert_batch(
            chunk_ids=["c1", "c2"],
            vectors=[[0.1], [0.2]],
            texts=["Text 1", "Text 2"],
            metadatas=[{"v": "1"}, {"v": "2"}],
        )
        mock_client.upsert.assert_called_once()

    def test_query_returns_formatted_results(self, collection):
        coll, mock_client = collection
        mock_client.query.return_value = {
            "ids": [["chunk_001"]],
            "documents": [["Patient had fever for 3 days."]],
            "metadatas": [[{"patient_id": "PT001"}]],
            "distances": [[0.15]],
        }

        results = coll.query([0.1, 0.2, 0.3], n_results=5)

        assert len(results) == 1
        result = results[0]
        assert result["chunk_id"] == "chunk_001"
        assert result["text"] == "Patient had fever for 3 days."
        assert result["distance"] == 0.15
        assert result["score"] == pytest.approx(1.0 - 0.15)
        assert result["metadata"]["patient_id"] == "PT001"

    def test_query_with_where_filter(self, collection):
        coll, mock_client = collection
        mock_client.query.return_value = {
            "ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]
        }

        coll.query([0.1], n_results=3, where={"patient_id": {"$eq": "PT001"}})

        mock_client.query.assert_called_once_with(
            collection_id="col-test-id",
            query_embeddings=[[0.1]],
            n_results=3,
            include=["documents", "metadatas", "distances"],
            where={"patient_id": {"$eq": "PT001"}},
        )

    def test_delete_delegates_to_client(self, collection):
        coll, mock_client = collection
        coll.delete(["chunk_001", "chunk_002"])
        mock_client.delete.assert_called_once_with(
            collection_id="col-test-id",
            ids=["chunk_001", "chunk_002"],
        )

    def test_count_delegates_to_client(self, collection):
        coll, mock_client = collection
        mock_client.count.return_value = 42
        assert coll.count() == 42
        mock_client.count.assert_called_with("col-test-id")

    def test_score_is_complement_of_distance(self, collection):
        """For cosine distance, score = 1.0 - distance."""
        coll, mock_client = collection
        mock_client.query.return_value = {
            "ids": [["c1", "c2"]],
            "documents": [["Doc 1", "Doc 2"]],
            "metadatas": [[{}, {}]],
            "distances": [[0.3, 0.7]],
        }

        results = coll.query([0.0])
        assert results[0]["score"] == pytest.approx(0.7)
        assert results[1]["score"] == pytest.approx(0.3)
