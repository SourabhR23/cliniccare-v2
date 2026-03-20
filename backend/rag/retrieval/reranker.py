"""
rag/retrieval/reranker.py

WHY A RERANKER AFTER HYBRID RETRIEVAL:
  Vector + BM25 retrieves 10 candidates fast (sparse/dense approximation).
  But "fast" means some false positives sneak through.

  A CrossEncoder reranker reads BOTH the query AND each candidate together,
  producing a fine-grained relevance score. It's like asking a doctor:
  "Given this question, how relevant is this visit record?"

  The tradeoff:
  - Bi-encoder (embedding search): fast, independent, approximate
  - CrossEncoder: slow, joint, precise

  Solution: use bi-encoder to fetch 10, CrossEncoder to pick top 4.
  This gives precision without paying CrossEncoder cost on all data.

CHOSEN MODEL: cross-encoder/ms-marco-MiniLM-L-6-v2
  - 22M parameters (tiny, fast on CPU)
  - Trained on MS MARCO passage ranking (search relevance)
  - Inference: ~10ms per query-document pair on CPU
  - 10 pairs × 10ms = ~100ms total reranking latency
  - Free, local, no API calls

ALTERNATIVE MODELS:
  - cross-encoder/ms-marco-electra-base: better accuracy, 30ms/pair
  - Cohere Rerank API: best quality, $0.001/call, network latency
  We chose the smallest local model for zero-cost, low-latency inference.

K_FINAL = 4:
  4 chunks × ~400 tokens/chunk ≈ 1600 tokens of context.
  GPT-4o-mini context window: 128k tokens.
  We're nowhere near the limit, but more chunks = more noise for the LLM.
  4 is empirically good for clinical RAG: enough context, not overwhelming.
"""

from typing import List
import asyncio
import structlog

logger = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
K_FINAL = 4  # top chunks passed to LLM after reranking


# ─────────────────────────────────────────────────────────────
# RERANKER
# ─────────────────────────────────────────────────────────────

class CrossEncoderReranker:
    """
    Reranks retrieval candidates using a local CrossEncoder model.

    The model is loaded ONCE at instantiation (lazy load via _get_model).
    sentence-transformers will download the model on first use (~50MB).
    Subsequent uses load from disk cache (~100ms).

    Usage:
        reranker = CrossEncoderReranker()
        top_chunks = await reranker.rerank(query, candidates, k=4)
    """

    def __init__(self):
        # Lazy load — model is loaded only when first rerank() is called.
        # This avoids slowing down app startup.
        self._model = None

    def _get_model(self):
        """
        Lazy-load the CrossEncoder model.

        WHY LAZY:
          sentence-transformers loads model weights into memory (~100MB).
          We don't want to pay this at startup — only when first RAG query runs.
          In production, the first request after cold start is slightly slower
          (~2s for model load). All subsequent requests: ~100ms.

        WHY NOT CACHED AT MODULE LEVEL:
          Module-level model would be loaded even if RAG is never used in a session.
          For a clinic that only uses admin features that day, wasted 100MB.
        """
        if self._model is None:
            # Import here to avoid top-level import cost
            from sentence_transformers import CrossEncoder

            logger.info("loading_crossencoder", model=RERANKER_MODEL)
            self._model = CrossEncoder(RERANKER_MODEL)
            logger.info("crossencoder_loaded")
        return self._model

    async def rerank(
        self,
        query: str,
        candidates: List[dict],
        k: int = K_FINAL,
    ) -> List[dict]:
        """
        Rerank candidates using CrossEncoder, return top k.

        Input candidates format (from HybridRetriever):
            [{"chunk_id": str, "text": str, "metadata": dict, "rrf_score": float}, ...]

        IMPORTANT: "text" must be present in candidates.
          Candidates from ChromaDB vector search include "text".
          Candidates from BM25-only (not in ChromaDB) may not have "text".
          If text is missing, we use the chief_complaint + diagnosis as fallback.

        CrossEncoder runs synchronously.
        We wrap in run_in_executor to avoid blocking FastAPI's event loop.
        Even 100ms of sync code blocks all concurrent requests.
        """
        if not candidates:
            return []

        # Build (query, document) pairs for CrossEncoder
        pairs = []
        for candidate in candidates:
            doc_text = candidate.get("text") or self._build_fallback_text(candidate)
            pairs.append([query, doc_text])

        # Run CrossEncoder in thread executor (sync → non-blocking)
        loop = asyncio.get_running_loop()
        model = self._get_model()

        scores = await loop.run_in_executor(
            None,
            lambda: model.predict(pairs),
        )

        # Attach scores to candidates
        scored = []
        for candidate, score in zip(candidates, scores):
            item = dict(candidate)
            item["rerank_score"] = float(score)
            scored.append(item)

        # Sort by rerank score descending
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)

        top_k = scored[:k]

        logger.info(
            "reranker_complete",
            input_count=len(candidates),
            output_count=len(top_k),
            top_score=top_k[0]["rerank_score"] if top_k else None,
        )

        return top_k

    def _build_fallback_text(self, candidate: dict) -> str:
        """
        Build a text representation from metadata when chunk text is unavailable.

        This happens when a BM25 result wasn't found in ChromaDB
        (e.g., the visit is pending embedding but was returned by keyword search).
        We construct a minimal representation from the metadata fields.
        """
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
