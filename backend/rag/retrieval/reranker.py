"""
rag/retrieval/reranker.py

WHY A RERANKER AFTER HYBRID RETRIEVAL:
  Vector + BM25 retrieves 10 candidates fast (sparse/dense approximation).
  But "fast" means some false positives sneak through.

  A reranker reads BOTH the query AND each candidate together,
  producing a fine-grained relevance score. It's like asking a doctor:
  "Given this question, how relevant is this visit record?"

  The tradeoff:
  - Bi-encoder (embedding search): fast, independent, approximate
  - Reranker: slower, joint, precise

  Solution: use bi-encoder to fetch 10, reranker to pick top 4.
  This gives precision without paying reranker cost on all data.

CHOSEN BACKEND: Cohere Rerank API (rerank-english-v3.0)
  - No local model download, no torch/sentence-transformers dependency
  - ~200ms API latency per rerank call (10 docs)
  - Free tier: 1000 calls/month; paid: $0.001 per call
  - Quality: best-in-class, trained specifically for reranking
  - Render-safe: zero RAM overhead

FALLBACK:
  If COHERE_API_KEY is not set, reranker returns candidates in original
  RRF-fused order (no re-scoring). Quality is slightly lower but
  the pipeline keeps working without any API key.

K_FINAL = 4:
  4 chunks × ~400 tokens/chunk ≈ 1600 tokens of context.
  GPT-4o-mini context window: 128k tokens.
  4 is empirically good for clinical RAG: enough context, not overwhelming.
"""

from typing import List
import structlog

logger = structlog.get_logger(__name__)

K_FINAL = 4  # top chunks passed to LLM after reranking


class CohereReranker:
    """
    Reranks retrieval candidates using the Cohere Rerank API.

    Falls back to original RRF order if COHERE_API_KEY is not configured.

    Usage:
        reranker = CohereReranker()
        top_chunks = await reranker.rerank(query, candidates, k=4)
    """

    def __init__(self):
        from backend.core.config import get_settings
        settings = get_settings()
        self._api_key = settings.cohere_api_key
        self._model = settings.cohere_rerank_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import cohere
            self._client = cohere.AsyncClient(self._api_key)
        return self._client

    async def rerank(
        self,
        query: str,
        candidates: List[dict],
        k: int = K_FINAL,
    ) -> List[dict]:
        """
        Rerank candidates using Cohere Rerank API, return top k.

        Input candidates format (from HybridRetriever):
            [{"chunk_id": str, "text": str, "metadata": dict, "rrf_score": float}, ...]

        If Cohere API key is not set, returns the first k candidates
        in their original RRF-fused order.
        """
        if not candidates:
            return []

        if not self._api_key:
            logger.warning("cohere_api_key_missing_fallback_to_rrf_order")
            return candidates[:k]

        # Build list of document strings for Cohere
        documents = []
        for candidate in candidates:
            doc_text = candidate.get("text") or self._build_fallback_text(candidate)
            documents.append(doc_text)

        try:
            client = self._get_client()
            response = await client.rerank(
                model=self._model,
                query=query,
                documents=documents,
                top_n=k,
            )

            # Map Cohere results back to original candidate dicts
            top_k = []
            for result in response.results:
                item = dict(candidates[result.index])
                item["rerank_score"] = float(result.relevance_score)
                top_k.append(item)

            logger.info(
                "cohere_reranker_complete",
                input_count=len(candidates),
                output_count=len(top_k),
                top_score=top_k[0]["rerank_score"] if top_k else None,
                model=self._model,
            )
            return top_k

        except Exception as exc:
            logger.error("cohere_reranker_error", error=str(exc), fallback="rrf_order")
            # Graceful fallback: return top k by original RRF score
            return candidates[:k]

    def _build_fallback_text(self, candidate: dict) -> str:
        """Build text from metadata when chunk text is unavailable."""
        meta = candidate.get("metadata", {})
        parts = []
        if meta.get("chief_complaint"):
            parts.append(f"Chief Complaint: {meta['chief_complaint']}")
        if meta.get("diagnosis"):
            parts.append(f"Diagnosis: {meta['diagnosis']}")
        if meta.get("medication_names"):
            parts.append(f"Medications: {', '.join(meta['medication_names'])}")
        if meta.get("visit_date"):
            parts.append(f"Visit Date: {meta['visit_date']}")
        return "\n".join(parts) if parts else "No text available"


# Keep old name as alias so nothing else needs to change
CrossEncoderReranker = CohereReranker
