"""
rag/retrieval/hybrid_retriever.py

HYBRID SEARCH = VECTOR + BM25, FUSED WITH RRF

WHY RECIPROCAL RANK FUSION (RRF):
  The naive approach: average the scores.
  Problem: vector scores (cosine similarity, ~0.0–1.0) and BM25 scores
  (TF-IDF based, varies by collection) are NOT on the same scale.
  Averaging them gives BM25 disproportionate weight when raw scores are large.

  RRF solution: throw away the raw scores entirely.
  Only use RANK POSITION.

  Formula: RRF(d) = Σ 1 / (k + rank_i(d))
  where:
    d = document
    k = 60 (standard constant, reduces sensitivity to top-1 rank)
    rank_i = document's position in retrieval list i

  EXAMPLE:
    Vector results:  [A=1st, B=2nd, C=3rd]
    BM25 results:    [B=1st, C=2nd, D=3rd]

    RRF scores:
      A: 1/(60+1) = 0.0164  (only in vector)
      B: 1/(60+2) + 1/(60+1) = 0.0161 + 0.0164 = 0.0325  (in both → top)
      C: 1/(60+3) + 1/(60+2) = 0.0159 + 0.0161 = 0.0320  (in both)
      D: 1/(60+3) = 0.0159  (only in BM25)

    Final order: B > C > A > D  ✓  (B appeared in both → wins)

  Documents appearing in BOTH retrieval methods are strongly boosted.
  This is the core insight: agreement between methods signals relevance.

PATIENT SCOPING:
  For general queries: search all embedded visits.
  For pre-visit brief: scope vector search to patient_id via ChromaDB WHERE filter.
"""

from typing import List, Optional
import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.rag.retrieval.chroma_client import ChromaVisitCollection
from backend.rag.retrieval.bm25_retriever import BM25Retriever
from backend.rag.embedding.openai_embedder import OpenAIEmbedder

logger = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

RRF_K = 60          # Standard RRF constant. Higher k → more rank-stable
K_RETRIEVE = 10     # Candidates returned to reranker


# ─────────────────────────────────────────────────────────────
# RRF FUSION
# ─────────────────────────────────────────────────────────────

def reciprocal_rank_fusion(
    vector_results: List[dict],
    bm25_results: List[dict],
    k: int = RRF_K,
) -> List[dict]:
    """
    Fuse two ranked lists using Reciprocal Rank Fusion.

    Input format for both lists:
        [{"chunk_id": str, "text": str, "metadata": dict, ...}, ...]
        Must be ordered by relevance (index 0 = most relevant).

    Output: list of dicts with rrf_score added, sorted descending.

    DEDUPLICATION:
      Both lists may contain the same chunk_id (if a visit matched both).
      We accumulate RRF scores — a chunk appearing in both gets both contributions.
    """
    scores: dict[str, float] = {}
    chunk_data: dict[str, dict] = {}  # chunk_id → best available data

    # Process vector results (rank = index + 1)
    for rank, item in enumerate(vector_results, start=1):
        cid = item["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        if cid not in chunk_data:
            chunk_data[cid] = item

    # Process BM25 results (rank = index + 1)
    for rank, item in enumerate(bm25_results, start=1):
        cid = item["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
        if cid not in chunk_data:
            # BM25 result may not have "text" (it searches MongoDB, not Chroma)
            # We store it with whatever fields it has
            chunk_data[cid] = item

    # Sort by RRF score descending
    sorted_chunks = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    results = []
    for chunk_id, rrf_score in sorted_chunks:
        item = dict(chunk_data[chunk_id])
        item["rrf_score"] = rrf_score
        results.append(item)

    return results


# ─────────────────────────────────────────────────────────────
# HYBRID RETRIEVER
# ─────────────────────────────────────────────────────────────

class HybridRetriever:
    """
    Orchestrates vector search + BM25 search + RRF fusion.

    Returns top K_RETRIEVE candidates for the reranker.

    Usage:
        retriever = HybridRetriever(db)
        candidates = await retriever.retrieve(
            query="patient with recurring fever and Azithromycin",
            patient_id="PAT001",  # optional scoping
        )
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self._embedder = OpenAIEmbedder()
        self._chroma = ChromaVisitCollection()
        self._bm25 = BM25Retriever(db)

    async def retrieve(
        self,
        query: str,
        patient_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        k: int = K_RETRIEVE,
    ) -> List[dict]:
        """
        Full hybrid retrieval pipeline.

        Steps:
        1. Embed the query (async, ~200ms)
        2. Vector search ChromaDB (sync wrapped in async, ~50ms)
        3. BM25 search MongoDB (async, ~30ms)
        4. RRF fusion (CPU-bound, instant)
        5. Return top k

        Steps 2 and 3 run CONCURRENTLY with asyncio.gather.
        Total latency ≈ max(vector, bm25) ≈ ~200ms (embedding dominates).

        SCOPING PRIORITY (most specific → least specific):
          patient_id  → ChromaDB WHERE patient_id + MongoDB patient_id filter
          doctor_id   → ChromaDB WHERE doctor_id + MongoDB doctor_id filter
                        (prevents cross-doctor data leak for unscoped queries)
          neither     → no filter (admin cross-patient query)
        """
        logger.info(
            "hybrid_retrieve_start",
            query_preview=query[:60],
            patient_scoped=patient_id is not None,
            doctor_scoped=doctor_id is not None and patient_id is None,
            k=k,
        )

        # Step 1: embed the query
        query_vector = await self._embedder.embed_single(query)

        # Step 2 + 3: run vector and BM25 concurrently
        import asyncio

        # ChromaDB is synchronous — wrap in executor to avoid blocking event loop
        loop = asyncio.get_running_loop()

        # Build ChromaDB WHERE filter — patient_id takes priority over doctor_id
        if patient_id:
            chroma_where = {"patient_id": {"$eq": patient_id}}
        elif doctor_id:
            chroma_where = {"doctor_id": {"$eq": doctor_id}}
        else:
            chroma_where = None  # admin: search all

        vector_task = loop.run_in_executor(
            None,
            lambda: self._chroma.query(
                query_vector=query_vector,
                n_results=k * 2,  # over-fetch for RRF
                where=chroma_where,
            ),
        )

        bm25_task = self._bm25.search(
            query=query,
            patient_id=patient_id,
            doctor_id=doctor_id,
            n_results=k * 2,
        )

        vector_results, bm25_results = await asyncio.gather(vector_task, bm25_task)

        logger.debug(
            "hybrid_retrieve_raw",
            vector_count=len(vector_results),
            bm25_count=len(bm25_results),
        )

        # Step 4: RRF fusion
        fused = reciprocal_rank_fusion(vector_results, bm25_results)

        # Step 5: top k
        candidates = fused[:k]

        logger.info(
            "hybrid_retrieve_complete",
            candidates_returned=len(candidates),
        )

        return candidates
