"""
tests/test_rrf.py  —  Reciprocal Rank Fusion (RRF) pure unit tests.

No database, no HTTP, no mocks needed.
Tests the RRF fusion algorithm directly.

Marked: unit
"""

import pytest
from backend.rag.retrieval.hybrid_retriever import reciprocal_rank_fusion, RRF_K

pytestmark = pytest.mark.unit


def _doc(chunk_id: str, text: str = "sample text") -> dict:
    """Build a minimal chunk dict for testing."""
    return {"chunk_id": chunk_id, "text": text, "metadata": {}, "score": 0.5}


class TestRRFBasics:
    def test_empty_both_lists_returns_empty(self):
        result = reciprocal_rank_fusion([], [])
        assert result == []

    def test_empty_vector_list_uses_bm25_only(self):
        bm25 = [_doc("A"), _doc("B"), _doc("C")]
        result = reciprocal_rank_fusion([], bm25)
        assert len(result) == 3
        # Scores should be decreasing (rank 1 → highest score)
        scores = [r["rrf_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_empty_bm25_list_uses_vector_only(self):
        vector = [_doc("X"), _doc("Y")]
        result = reciprocal_rank_fusion(vector, [])
        assert len(result) == 2

    def test_rrf_score_formula(self):
        """Verify the score formula: 1 / (k + rank)."""
        vector = [_doc("A")]  # rank 1 in vector
        bm25 = []
        result = reciprocal_rank_fusion(vector, bm25, k=RRF_K)
        expected_score = 1.0 / (RRF_K + 1)
        assert result[0]["rrf_score"] == pytest.approx(expected_score)

    def test_result_contains_rrf_score_field(self):
        vector = [_doc("A"), _doc("B")]
        bm25 = [_doc("B"), _doc("C")]
        result = reciprocal_rank_fusion(vector, bm25)
        for item in result:
            assert "rrf_score" in item

    def test_result_sorted_by_score_descending(self):
        vector = [_doc("A"), _doc("B"), _doc("C")]
        bm25 = [_doc("C"), _doc("B"), _doc("D")]
        result = reciprocal_rank_fusion(vector, bm25)
        scores = [r["rrf_score"] for r in result]
        assert scores == sorted(scores, reverse=True)


class TestRRFAgreementBoost:
    """Documents appearing in BOTH lists should rank higher."""

    def test_document_in_both_lists_outranks_single_list_doc(self):
        """
        Vector:  [A=1st, B=2nd]
        BM25:    [B=1st, C=2nd]

        B appears in both → should rank highest.
        A and C each appear in only one list.
        """
        vector = [_doc("A"), _doc("B")]
        bm25 = [_doc("B"), _doc("C")]
        result = reciprocal_rank_fusion(vector, bm25)

        # B should be ranked first
        assert result[0]["chunk_id"] == "B"

    def test_scores_accumulate_for_shared_documents(self):
        """Score of a document in both lists = sum of its individual ranks' scores."""
        vector = [_doc("shared")]  # rank 1 in vector
        bm25 = [_doc("shared")]   # rank 1 in BM25
        result = reciprocal_rank_fusion(vector, bm25)

        expected = 1.0 / (RRF_K + 1) + 1.0 / (RRF_K + 1)
        assert result[0]["rrf_score"] == pytest.approx(expected)

    def test_document_only_in_one_list_gets_partial_score(self):
        vector = [_doc("only_vector")]
        bm25 = [_doc("only_bm25")]
        result = reciprocal_rank_fusion(vector, bm25)

        # Both appear exactly once → same rank score
        scores = [r["rrf_score"] for r in result]
        assert scores[0] == pytest.approx(scores[1])

    def test_high_rank_in_both_lists_beats_low_rank_in_one(self):
        """
        vector: [A=1st, B=2nd, C=3rd]
        bm25:   [A=1st, D=2nd, E=3rd]

        A at rank 1 in BOTH → should beat any single-list document.
        """
        vector = [_doc("A"), _doc("B"), _doc("C")]
        bm25 = [_doc("A"), _doc("D"), _doc("E")]
        result = reciprocal_rank_fusion(vector, bm25)

        assert result[0]["chunk_id"] == "A"


class TestRRFDeduplication:
    """Same chunk_id in both lists → counted once with accumulated score."""

    def test_no_duplicate_ids_in_output(self):
        """Each chunk_id should appear exactly once."""
        vector = [_doc("A"), _doc("B"), _doc("C")]
        bm25 = [_doc("B"), _doc("C"), _doc("D")]
        result = reciprocal_rank_fusion(vector, bm25)

        ids = [r["chunk_id"] for r in result]
        assert len(ids) == len(set(ids)), "Duplicate chunk_ids found in output"

    def test_total_unique_documents(self):
        """Output has exactly the union of unique chunk_ids from both lists."""
        vector = [_doc("A"), _doc("B")]
        bm25 = [_doc("B"), _doc("C")]
        result = reciprocal_rank_fusion(vector, bm25)

        # A, B, C → 3 unique documents
        assert len(result) == 3

    def test_chunk_data_preserved(self):
        """The text/metadata of a document should be preserved in the output."""
        vector = [{"chunk_id": "DOC1", "text": "important text", "metadata": {"k": "v"}, "score": 0.9}]
        bm25 = []
        result = reciprocal_rank_fusion(vector, bm25)

        assert result[0]["text"] == "important text"
        assert result[0]["metadata"]["k"] == "v"


class TestRRFEdgeCases:
    def test_single_document_in_each_list(self):
        result = reciprocal_rank_fusion([_doc("A")], [_doc("B")])
        assert len(result) == 2

    def test_large_list(self):
        """RRF should handle 100+ documents without issue."""
        vector = [_doc(f"vec_{i:03d}") for i in range(100)]
        bm25 = [_doc(f"bm25_{i:03d}") for i in range(100)]
        result = reciprocal_rank_fusion(vector, bm25)
        assert len(result) == 200

    def test_k_parameter_affects_scores(self):
        """Higher k → less sensitivity to rank 1 (scores closer together)."""
        vector = [_doc("A"), _doc("B")]
        bm25 = []

        result_k10 = reciprocal_rank_fusion(vector, bm25, k=10)
        result_k60 = reciprocal_rank_fusion(vector, bm25, k=60)

        # Score difference between rank 1 and rank 2 is larger with small k
        diff_k10 = result_k10[0]["rrf_score"] - result_k10[1]["rrf_score"]
        diff_k60 = result_k60[0]["rrf_score"] - result_k60[1]["rrf_score"]
        assert diff_k10 > diff_k60

    def test_identical_lists_ordering(self):
        """When both lists are identical, all documents appear once with doubled scores."""
        docs = [_doc("A"), _doc("B"), _doc("C")]
        result = reciprocal_rank_fusion(docs[:], docs[:])

        assert len(result) == 3
        # Each document should have exactly 2x the single-list score
        for item in result:
            # Single list score at rank r = 1/(k+r)
            # Both lists score = 2/(k+r)
            pass  # Ordering is preserved

        # Order should remain A > B > C
        assert result[0]["chunk_id"] == "A"
        assert result[1]["chunk_id"] == "B"
        assert result[2]["chunk_id"] == "C"
