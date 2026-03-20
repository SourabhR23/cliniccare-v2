"""
rag/rag_service.py

THE CENTRAL ORCHESTRATOR FOR BOTH RAG PIPELINES:

PIPELINE 1 — INGESTION (admin-triggered):
  MongoDB pending visits
    → VisitChunker (chunk text + metadata)
    → OpenAIEmbedder (batch embed)
    → ChromaDB upsert
    → MongoDB update (embedding_status = "embedded")

PIPELINE 2 — RETRIEVAL + SYNTHESIS (doctor query):
  Doctor query string
    → HybridRetriever (vector + BM25, RRF fused → top 10)
    → CrossEncoderReranker (top 10 → top 4)
    → GPT-4o-mini synthesis (4 chunks → clinical answer)
    → Redis cache (TTL = redis_ttl_rag_query = 1hr)

REDIS CACHING DESIGN:
  Cache key: sha256(query + patient_id) — deterministic for same input.
  Cache TTL: 1 hour (redis_ttl_rag_query from config).

  WHY CACHE:
    Doctors frequently ask the same pre-visit brief before appointments.
    "Summarize Mr. Patel's history" → same answer for the whole morning.
    Without cache: OpenAI API call on every request (~$0.001 each).
    With cache: one API call, rest served from Redis in <1ms.

  CACHE INVALIDATION:
    We cache query → answer pairs. If a new visit is added for a patient,
    the cached pre-visit brief becomes stale.
    Simple solution: TTL = 1hr. Stale answers are acceptable for clinic workflow.
    The doctor can force-refresh by rephrasing the query (changes cache key).

SYNTHESIS PARAMETERS:
  temperature=0.1: minimal randomness — clinical answers must be consistent
  top_p=0.3: narrow probability mass — avoids creative fabrication
  max_tokens=600: enough for a thorough clinical summary, not a novel
"""

import hashlib
import json
from datetime import datetime
from typing import List, Optional
import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase
from openai import AsyncOpenAI

from backend.core.config import get_settings
from backend.models.patient import VisitDocument, EmbeddingStatusEnum
from backend.rag.chunking.visit_chunker import VisitChunker
from backend.rag.embedding.openai_embedder import OpenAIEmbedder
from backend.rag.retrieval.chroma_client import ChromaVisitCollection
from backend.rag.retrieval.hybrid_retriever import HybridRetriever
from backend.rag.retrieval.reranker import CrossEncoderReranker

logger = structlog.get_logger(__name__)

settings = get_settings()


# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────

RAG_SYSTEM_PROMPT = """You are a clinical assistant helping doctors review patient history.
You have been given relevant visit records from the patient's chart.

Your job:
1. Synthesize the provided visit records into a clear, clinically useful answer.
2. Cite specific dates and details from the records.
3. Flag any patterns (recurring symptoms, escalating conditions, medication changes).
4. If the records don't contain enough information to answer, say so clearly — do NOT hallucinate.
5. Use clinical terminology appropriate for a doctor audience.
6. Be concise. The doctor is busy. Prefer bullet points for summaries.

IMPORTANT: Only use information from the provided visit records. Do not add clinical knowledge not present in the records."""

PREVISIT_BRIEF_PROMPT = """Based on the patient's visit history below, provide a pre-visit brief covering:
1. **Chronic conditions and ongoing issues**
2. **Current medications** (most recently prescribed)
3. **Recurring symptoms or patterns**
4. **Pending follow-ups** (if any)
5. **Key alerts** (allergies, adverse reactions, escalating symptoms)

Be brief. The doctor has 5 minutes before the appointment."""


# ─────────────────────────────────────────────────────────────
# RAG SERVICE
# ─────────────────────────────────────────────────────────────

class RAGService:
    """
    Orchestrates both RAG pipelines: ingestion and retrieval+synthesis.

    Injected into FastAPI routes via Depends().
    The db dependency is passed from the route — not created here.

    Usage in routes:
        rag_service = RAGService(db, redis_client)
        result = await rag_service.query("Has this patient had TB?", patient_id="PAT001")
    """

    def __init__(self, db: AsyncIOMotorDatabase, redis_client=None):
        self._db = db
        self._redis = redis_client
        self._chunker = VisitChunker()
        self._embedder = OpenAIEmbedder()
        self._chroma = ChromaVisitCollection()
        self._hybrid = HybridRetriever(db)
        self._reranker = CrossEncoderReranker()
        self._openai = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url
        )

    # ─────────────────────────────────────────────────────────
    # PIPELINE 1: INGESTION
    # ─────────────────────────────────────────────────────────

    async def embed_pending_visits(self, batch_size: int = 100) -> dict:
        """
        Fetch pending visits, embed them, store in ChromaDB, update MongoDB.

        Returns a summary dict:
            {"total": 200, "embedded": 198, "failed": 2, "duration_seconds": 12.4}

        TRANSACTION DESIGN:
          MongoDB and ChromaDB are separate systems — no distributed transaction.
          Strategy:
          1. Embed + upsert to ChromaDB first.
          2. Only then update MongoDB to "embedded".
          3. If ChromaDB succeeds but MongoDB update fails:
             visit remains "pending" → will be re-embedded next run.
             ChromaDB upsert is idempotent (same chunk_id → overwrite).
             So re-embedding an already-embedded visit is safe.

        FAILURE ISOLATION:
          If one batch fails, we mark those visits "failed" and continue
          with the next batch. A partial run is better than all-or-nothing.
        """
        start_time = datetime.utcnow()
        total_embedded = 0
        total_failed = 0

        visits_collection = self._db["visits"]

        logger.info("ingestion_pipeline_start")

        # ── Fetch pending visits ─────────────────────────────
        cursor = visits_collection.find(
            {"embedding_status": EmbeddingStatusEnum.PENDING.value}
        )
        pending_visits_raw = await cursor.to_list(length=None)

        total = len(pending_visits_raw)
        logger.info("ingestion_pending_count", total=total)

        if total == 0:
            return {"total": 0, "embedded": 0, "failed": 0, "duration_seconds": 0.0}

        # ── Parse into VisitDocument models ──────────────────
        pending_visits = []
        for raw in pending_visits_raw:
            try:
                raw["_id"] = str(raw["_id"])
                # Convert date fields
                for date_field in ["visit_date", "followup_date"]:
                    if isinstance(raw.get(date_field), datetime):
                        raw[date_field] = raw[date_field].date()
                pending_visits.append(VisitDocument(**raw))
            except Exception as e:
                logger.error("visit_parse_error", visit_id=raw.get("_id"), error=str(e))
                total_failed += 1

        # ── Process in batches ────────────────────────────────
        for batch_start in range(0, len(pending_visits), batch_size):
            batch = pending_visits[batch_start : batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            logger.info("ingestion_batch", batch=batch_num, size=len(batch))

            try:
                # 1. Chunk all visits in batch
                chunk_ids, texts, metadatas = [], [], []
                for visit in batch:
                    cid, text, meta = self._chunker.chunk(visit)
                    chunk_ids.append(cid)
                    texts.append(text)
                    metadatas.append(meta)

                # 2. Embed all texts (one batched OpenAI call)
                vectors = await self._embedder.embed_texts(texts)

                # 3. Upsert to ChromaDB
                self._chroma.upsert_batch(chunk_ids, vectors, texts, metadatas)

                # 4. Update MongoDB: mark each visit embedded with its chunk_id
                import asyncio
                update_tasks = [
                    visits_collection.update_one(
                        {"_id": v.id},
                        {"$set": {
                            "embedding_status": EmbeddingStatusEnum.EMBEDDED.value,
                            "embedded_at": datetime.utcnow(),
                            "chroma_chunk_id": cid,
                        }},
                    )
                    for v, cid in zip(batch, chunk_ids)
                ]
                await asyncio.gather(*update_tasks)

                total_embedded += len(batch)
                logger.info("ingestion_batch_complete", embedded=len(batch))

            except Exception as e:
                logger.error("ingestion_batch_failed", batch=batch_num, error=str(e))

                # Mark batch as failed
                visit_ids = [v.id for v in batch]
                await visits_collection.update_many(
                    {"_id": {"$in": visit_ids}},
                    {"$set": {"embedding_status": EmbeddingStatusEnum.FAILED.value}},
                )
                total_failed += len(batch)

        duration = (datetime.utcnow() - start_time).total_seconds()
        result = {
            "total": total,
            "embedded": total_embedded,
            "failed": total_failed,
            "duration_seconds": round(duration, 2),
        }

        logger.info("ingestion_pipeline_complete", **result)
        return result

    async def get_embedding_queue_status(self) -> dict:
        """
        Returns counts of visits by embedding status.
        Used by GET /admin/queue.
        """
        visits_collection = self._db["visits"]

        pipeline = [
            {"$group": {"_id": "$embedding_status", "count": {"$sum": 1}}}
        ]
        cursor = visits_collection.aggregate(pipeline)
        docs = await cursor.to_list(length=None)

        counts = {doc["_id"]: doc["count"] for doc in docs}
        return {
            "pending": counts.get("pending", 0),
            "embedded": counts.get("embedded", 0),
            "failed": counts.get("failed", 0),
            "chroma_total": self._chroma.count(),
        }

    # ─────────────────────────────────────────────────────────
    # SYNC CHECK / FIX
    # ─────────────────────────────────────────────────────────

    async def sync_check(self) -> dict:
        """
        Cross-reference MongoDB pending visits against ChromaDB.

        For every visit marked 'pending' in MongoDB, we compute its expected
        chunk_id (visit_chunk_{visit_id}) and ask ChromaDB if it exists.

        Returns:
          {
            "total_pending": 12,
            "truly_pending": 8,       ← not in ChromaDB, need embedding
            "already_in_chroma": 4,   ← in ChromaDB but MongoDB not updated
            "already_in_chroma_ids": ["VS1234...", ...]
          }
        """
        from backend.rag.chunking.visit_chunker import make_chunk_id

        visits_col = self._db["visits"]
        pending_docs = await visits_col.find(
            {"embedding_status": EmbeddingStatusEnum.PENDING.value},
            {"_id": 1},
        ).to_list(None)

        if not pending_docs:
            return {"total_pending": 0, "truly_pending": 0,
                    "already_in_chroma": 0, "already_in_chroma_ids": []}

        visit_ids   = [d["_id"] for d in pending_docs]
        chunk_ids   = [make_chunk_id(vid) for vid in visit_ids]

        # Ask ChromaDB which of these chunk IDs actually exist
        found_chunk_ids = set(self._chroma.get_by_ids(chunk_ids))

        # Map chunk_id back to visit_id for the response
        chunk_to_visit = {make_chunk_id(vid): vid for vid in visit_ids}
        already_in_chroma = [
            chunk_to_visit[cid] for cid in found_chunk_ids if cid in chunk_to_visit
        ]

        logger.info(
            "sync_check_complete",
            total_pending=len(visit_ids),
            already_in_chroma=len(already_in_chroma),
        )

        return {
            "total_pending": len(visit_ids),
            "truly_pending": len(visit_ids) - len(already_in_chroma),
            "already_in_chroma": len(already_in_chroma),
            "already_in_chroma_ids": already_in_chroma,
        }

    async def sync_fix(self) -> dict:
        """
        Fix the mismatch: visits that exist in ChromaDB but are still marked
        'pending' in MongoDB get updated to 'embedded'.

        Also decrements the patient's embedding_pending_count for each fixed visit.

        Returns: { "fixed": 4, "visit_ids": [...] }
        """
        from backend.rag.chunking.visit_chunker import make_chunk_id

        check = await self.sync_check()
        visit_ids_to_fix = check["already_in_chroma_ids"]

        if not visit_ids_to_fix:
            return {"fixed": 0, "visit_ids": []}

        visits_col   = self._db["visits"]
        patients_col = self._db["patients"]
        now          = datetime.utcnow().isoformat()

        for visit_id in visit_ids_to_fix:
            chunk_id = make_chunk_id(visit_id)

            # Mark as embedded in visits collection
            await visits_col.update_one(
                {"_id": visit_id},
                {"$set": {
                    "embedding_status": EmbeddingStatusEnum.EMBEDDED.value,
                    "chroma_chunk_id": chunk_id,
                    "embedded_at": now,
                }},
            )

            # Decrement the patient's pending count
            visit_doc = await visits_col.find_one({"_id": visit_id}, {"patient_id": 1})
            if visit_doc:
                await patients_col.update_one(
                    {"_id": visit_doc["patient_id"]},
                    {"$inc": {"metadata.embedding_pending_count": -1}},
                )

        logger.info("sync_fix_complete", fixed=len(visit_ids_to_fix))
        return {"fixed": len(visit_ids_to_fix), "visit_ids": visit_ids_to_fix}

    # ─────────────────────────────────────────────────────────
    # PIPELINE 2: RETRIEVAL + SYNTHESIS
    # ─────────────────────────────────────────────────────────

    async def query(
        self,
        query: str,
        patient_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
    ) -> dict:
        """
        Full RAG pipeline: query → retrieve → rerank → synthesize → cache.

        Returns:
            {
                "answer": str,
                "sources": [{"visit_id": str, "visit_date": str, "diagnosis": str, ...}],
                "cached": bool,
                "retrieval_count": int,
            }
        """
        # ── Cache check ───────────────────────────────────────
        cache_key = self._make_cache_key(query, patient_id)
        if self._redis:
            cached = await self._redis.get(cache_key)
            if cached:
                logger.info("rag_cache_hit", query_preview=query[:40])
                result = json.loads(cached)
                result["cached"] = True
                return result

        # ── Retrieve ──────────────────────────────────────────
        candidates = await self._hybrid.retrieve(query, patient_id=patient_id, doctor_id=doctor_id)

        if not candidates:
            return {
                "answer": "No relevant visit records found for this query.",
                "sources": [],
                "cached": False,
                "retrieval_count": 0,
            }

        # ── Rerank ────────────────────────────────────────────
        top_chunks = await self._reranker.rerank(query, candidates)

        # ── Synthesize ────────────────────────────────────────
        answer = await self._synthesize(query, top_chunks)

        # ── Build sources list ────────────────────────────────
        sources = self._build_sources(top_chunks)

        result = {
            "answer": answer,
            "sources": sources,
            "cached": False,
            "retrieval_count": len(candidates),
        }

        # ── Cache result ──────────────────────────────────────
        if self._redis:
            await self._redis.setex(
                cache_key,
                settings.redis_ttl_rag_query,
                json.dumps(result),
            )

        return result

    async def get_previsit_brief(self, patient_id: str) -> dict:
        """
        Generate a structured pre-visit brief for a specific patient.

        Differs from query() in:
        - Always scoped to one patient (patient_id required)
        - Uses PREVISIT_BRIEF_PROMPT (structured output format)
        - Cache key includes "previsit" prefix (different TTL bucket)
        """
        cache_key = self._make_cache_key(f"previsit:{patient_id}", patient_id)
        if self._redis:
            cached = await self._redis.get(cache_key)
            if cached:
                result = json.loads(cached)
                result["cached"] = True
                return result

        candidates = await self._hybrid.retrieve(
            query=PREVISIT_BRIEF_PROMPT,
            patient_id=patient_id,
        )

        if not candidates:
            return {
                "brief": "No visit history found for this patient.",
                "sources": [],
                "cached": False,
            }

        top_chunks = await self._reranker.rerank(PREVISIT_BRIEF_PROMPT, candidates)
        brief = await self._synthesize(PREVISIT_BRIEF_PROMPT, top_chunks, system_override=PREVISIT_BRIEF_PROMPT)
        sources = self._build_sources(top_chunks)

        result = {"brief": brief, "sources": sources, "cached": False}

        if self._redis:
            await self._redis.setex(cache_key, settings.redis_ttl_rag_query, json.dumps(result))

        return result

    # ─────────────────────────────────────────────────────────
    # PRIVATE HELPERS
    # ─────────────────────────────────────────────────────────

    async def _synthesize(
        self,
        query: str,
        chunks: List[dict],
        system_override: Optional[str] = None,
    ) -> str:
        """
        Build the context string from chunks and call GPT-4o-mini.

        CONTEXT CONSTRUCTION:
          We number each chunk (RECORD 1, RECORD 2, ...) so the LLM can
          reference them in its answer: "As seen in RECORD 2 from 2024-03-15..."
        """
        context_parts = []
        for i, chunk in enumerate(chunks, start=1):
            meta = chunk.get("metadata", {})
            date_str = meta.get("visit_date", "unknown date")
            context_parts.append(
                f"--- RECORD {i} (Visit on {date_str}) ---\n{chunk.get('text', '')}"
            )

        context = "\n\n".join(context_parts)

        user_message = f"""Patient Visit Records:
{context}

---
Doctor's Query: {query}

Please answer the query based only on the records above."""

        response = await self._openai.chat.completions.create(
            model=settings.openai_chat_model,   # gpt-4o-mini
            messages=[
                {"role": "system", "content": system_override or RAG_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,   # clinical precision: minimal randomness
            top_p=0.3,          # narrow probability mass
            max_tokens=600,
        )

        return response.choices[0].message.content or ""

    def _build_sources(self, chunks: List[dict]) -> List[dict]:
        """Extract source metadata from chunks for the API response."""
        sources = []
        for chunk in chunks:
            meta = chunk.get("metadata", {})
            sources.append({
                "visit_id": meta.get("visit_id", ""),
                "visit_date": meta.get("visit_date", ""),
                "visit_type": meta.get("visit_type", ""),
                "diagnosis": meta.get("diagnosis", ""),
                "doctor_name": meta.get("doctor_name", ""),
                "rerank_score": round(chunk.get("rerank_score", 0.0), 4),
            })
        return sources

    # ─────────────────────────────────────────────────────────
    # PIPELINE 3: CHAT WITH CONVERSATION HISTORY
    # ─────────────────────────────────────────────────────────

    async def chat_query(
        self,
        message: str,
        patient_id: Optional[str] = None,
        history: Optional[List[dict]] = None,
        doctor_id: Optional[str] = None,
    ) -> dict:
        """
        RAG query that includes prior conversation turns in the synthesis prompt.

        Unlike query(), this method passes conversation history to the LLM so
        follow-up questions work naturally:
          Q: "How many visits has this patient had?"
          A: "2 visits — Nov 2024 and Feb 2025"
          Q: "What was prescribed in the second visit?"   ← references prior answer

        History format:
            [{"role": "user"|"assistant", "content": str}, ...]

        History is capped at 20 messages (10 turns) to control context length.
        No Redis caching for chat — history changes the cache key every turn.
        """
        capped_history = (history or [])[-20:]

        candidates = await self._hybrid.retrieve(message, patient_id=patient_id, doctor_id=doctor_id)

        if not candidates:
            return {
                "answer": "No relevant visit records found for this query.",
                "sources": [],
                "cached": False,
                "retrieval_count": 0,
            }

        top_chunks = await self._reranker.rerank(message, candidates)
        answer = await self._synthesize_with_history(message, top_chunks, capped_history)
        sources = self._build_sources(top_chunks)

        return {
            "answer": answer,
            "sources": sources,
            "cached": False,
            "retrieval_count": len(candidates),
        }

    async def _synthesize_with_history(
        self,
        query: str,
        chunks: List[dict],
        history: List[dict],
    ) -> str:
        """
        Build the GPT prompt with retrieved records AND conversation history.

        Message structure sent to the LLM:
          [system]           ← clinical assistant system prompt
          [user/assistant]*  ← prior conversation turns (for follow-up context)
          [user]             ← current query + fresh retrieved records

        This lets the model answer "What was prescribed?" after the doctor
        already asked "How many visits?" — it has the prior A in context.
        """
        context_parts = []
        for i, chunk in enumerate(chunks, start=1):
            meta = chunk.get("metadata", {})
            date_str = meta.get("visit_date", "unknown date")
            context_parts.append(
                f"--- RECORD {i} (Visit on {date_str}) ---\n{chunk.get('text', '')}"
            )
        context = "\n\n".join(context_parts)

        messages: List[dict] = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]

        # Inject prior conversation turns so LLM can handle follow-ups
        for turn in history:
            messages.append({"role": turn["role"], "content": turn["content"]})

        # Current question with freshly retrieved records
        messages.append({
            "role": "user",
            "content": (
                f"Patient Visit Records:\n{context}\n\n"
                f"---\n"
                f"Doctor's Question: {query}\n\n"
                f"Please answer based only on the records above and our conversation so far."
            ),
        })

        response = await self._openai.chat.completions.create(
            model=settings.openai_chat_model,
            messages=messages,
            temperature=0.1,
            top_p=0.3,
            max_tokens=600,
        )

        return response.choices[0].message.content or ""

    def _make_cache_key(self, query: str, patient_id: Optional[str]) -> str:
        """
        Deterministic Redis key from query + patient_id.

        SHA256 ensures:
        1. Fixed length (always 64 hex chars) regardless of query length
        2. No special characters that Redis keys disallow
        3. Collision resistance (two different queries → different keys)
        """
        raw = f"rag:{query}:{patient_id or 'all'}"
        return f"rag_cache:{hashlib.sha256(raw.encode()).hexdigest()}"
