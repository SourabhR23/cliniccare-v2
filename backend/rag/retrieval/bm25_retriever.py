"""
rag/retrieval/bm25_retriever.py

WHY BM25 (KEYWORD) SEARCH ALONGSIDE VECTOR SEARCH:
  Vector search excels at semantic similarity but fails on:
  - Exact drug names: "Azithromycin" → vector might return "Amoxicillin"
    (similar embedding space = antibiotics cluster together)
  - Patient names: "Rahul Sharma" → vector doesn't retrieve exact matches
  - ICD codes: "J18.9" → no semantic signal, pure keyword match
  - Rare terms with few training examples

  BM25 (keyword) catches what vector misses.
  Hybrid = best of both worlds.

WHY MONGODB $text (vs rank-bm25 in memory):
  We already have MongoDB. Adding a separate BM25 index (Elasticsearch,
  Typesense) is operational overhead.
  MongoDB Atlas has built-in $text search with TF-IDF scoring.
  It's not pure BM25 but close enough for our hybrid fusion.

  REQUIREMENT: The `visits` collection needs a text index on the
  fields we want to search. This is created in indexes.py (Phase 1).
  Fields indexed: chief_complaint, symptoms, diagnosis, notes, medication_names

SCORE NORMALIZATION:
  MongoDB $text returns a `textScore` that varies by query and collection size.
  We normalize to [0, 1] by dividing by the max score in the result set.
  This makes BM25 scores comparable to ChromaDB cosine scores in RRF fusion.
"""

from typing import List, Optional
import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = structlog.get_logger(__name__)

# Maximum results to fetch from MongoDB text search
# We over-fetch (20) then RRF fusion reranks and narrows to k_retrieve=10
BM25_FETCH_LIMIT = 20


class BM25Retriever:
    """
    Keyword search over the visits collection using MongoDB $text.

    The visits collection must have a text index (created in indexes.py):
        db.visits.createIndex({
            chief_complaint: "text",
            symptoms: "text",
            diagnosis: "text",
            notes: "text",
            medication_names: "text"
        })

    Usage:
        retriever = BM25Retriever(db)
        results = await retriever.search("fever cough Azithromycin", patient_id="PAT001")
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db
        self._collection = db["visits"]

    async def search(
        self,
        query: str,
        patient_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        n_results: int = BM25_FETCH_LIMIT,
    ) -> List[dict]:
        """
        Full-text search on the visits collection.

        Returns list of dicts with:
          - visit_id: MongoDB _id of the matching visit
          - chunk_id: corresponding ChromaDB chunk ID (for RRF joining)
          - score: normalized BM25 score [0, 1]
          - text_score: raw MongoDB textScore
          - metadata: flattened visit fields for display/reranking

        SCOPING PRIORITY (most specific → least specific):
          1. patient_id  → restrict to one patient's visits
          2. doctor_id   → restrict to that doctor's patients only (no cross-doctor leak)
          3. neither     → no filter (admin cross-patient query)

        WHY $meta: "textScore":
          MongoDB $text assigns a relevance score based on term frequency
          in the document and across the collection (TF-IDF style).
          We project it as "text_score" and use it for normalization.
        """
        # ── Build filter ─────────────────────────────────────
        match_filter: dict = {"$text": {"$search": query}}
        if patient_id:
            match_filter["patient_id"] = patient_id
        elif doctor_id:
            # Scope to this doctor's patients — prevents cross-doctor data leak
            match_filter["doctor_id"] = doctor_id

        # ── MongoDB aggregation ───────────────────────────────
        # Stage 1: $match — text search with optional patient filter
        # Stage 2: $sort — by textScore descending (most relevant first)
        # Stage 3: $limit — cap results
        # Stage 4: $project — only fields we need
        pipeline = [
            {"$match": match_filter},
            {"$sort": {"score": {"$meta": "textScore"}}},
            {"$limit": n_results},
            {
                "$project": {
                    "_id": 1,
                    "patient_id": 1,
                    "patient_name": 1,
                    "doctor_id": 1,
                    "doctor_name": 1,
                    "visit_date": 1,
                    "visit_type": 1,
                    "chief_complaint": 1,
                    "symptoms": 1,
                    "diagnosis": 1,
                    "medication_names": 1,
                    "followup_required": 1,
                    "text_score": {"$meta": "textScore"},
                }
            },
        ]

        try:
            cursor = self._collection.aggregate(pipeline)
            docs = await cursor.to_list(length=n_results)
        except Exception as e:
            # MongoDB $text search fails if no text index exists
            # Log clearly so the developer knows to run create_all_indexes()
            logger.error(
                "bm25_search_failed",
                error=str(e),
                hint="Ensure visits collection has a text index — run indexes.py",
            )
            return []

        if not docs:
            return []

        # ── Normalize scores to [0, 1] ────────────────────────
        max_score = max(doc["text_score"] for doc in docs)
        if max_score == 0:
            max_score = 1  # avoid division by zero

        results = []
        for doc in docs:
            normalized_score = doc["text_score"] / max_score
            chunk_id = f"visit_chunk_{doc['_id']}"

            results.append({
                "visit_id": str(doc["_id"]),
                "chunk_id": chunk_id,
                "score": normalized_score,
                "text_score": doc["text_score"],
                "metadata": {
                    "patient_id": doc.get("patient_id", ""),
                    "patient_name": doc.get("patient_name", ""),
                    "doctor_id": doc.get("doctor_id", ""),
                    "visit_date": doc.get("visit_date", ""),
                    "visit_type": doc.get("visit_type", ""),
                    "chief_complaint": doc.get("chief_complaint", ""),
                    "diagnosis": doc.get("diagnosis", ""),
                    "medication_names": doc.get("medication_names", []),
                },
            })

        logger.debug(
            "bm25_results",
            query=query[:50],
            count=len(results),
            patient_scoped=patient_id is not None,
            doctor_scoped=doctor_id is not None and patient_id is None,
        )

        return results
