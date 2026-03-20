"""
rag/embedding/openai_embedder.py

WHY text-embedding-3-small:
  - 1536 dimensions (matches our config)
  - $0.02 / 1M tokens — cheapest OpenAI embedding model
  - Outperforms ada-002 on MTEB benchmarks despite lower cost
  - Supports Matryoshka representation: can truncate to 256d for speed
    (we use full 1536d for clinical precision)

BATCHING DESIGN:
  OpenAI embedding API accepts up to 2048 texts per call.
  We use BATCH_SIZE = 100 — safe, leaves headroom for long visit texts.

  Why batch at all? For the nightly digest:
  - 500 pending visits → 5 API calls instead of 500
  - Rate limit: 1M tokens/min on tier 1 → batching respects this

RETRY LOGIC:
  OpenAI rate limits return 429. Tenacity handles exponential backoff:
  - Wait 2^n seconds: 1s, 2s, 4s, 8s, 16s
  - Max 5 attempts before giving up
  - On permanent failure: mark visit as embedding_status="failed"

ERROR HANDLING:
  We catch OpenAI errors per-batch, not per-visit.
  If a batch fails after retries, ALL visits in that batch are marked failed.
  This is acceptable — a failed visit can be retried in the next digest run.
  Alternative (per-visit retry): slower, more API calls, harder to implement.
"""

import asyncio
from typing import List
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging
from openai import AsyncOpenAI, RateLimitError, APIStatusError

from backend.core.config import get_settings

logger = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

BATCH_SIZE = 100          # texts per OpenAI API call
MAX_RETRIES = 5           # tenacity retry attempts
RETRY_WAIT_MIN = 1        # seconds
RETRY_WAIT_MAX = 30       # seconds


# ─────────────────────────────────────────────────────────────
# EMBEDDER
# ─────────────────────────────────────────────────────────────

class OpenAIEmbedder:
    """
    Async wrapper around OpenAI's embedding endpoint.

    Responsibilities:
    1. Accept list of texts → return list of float vectors
    2. Batch automatically to stay within API limits
    3. Retry on transient errors (rate limits, 500s)
    4. Log costs for monitoring

    Usage:
        embedder = OpenAIEmbedder()
        vectors = await embedder.embed_texts(["text1", "text2"])
        # vectors[i] corresponds to texts[i]
    """

    def __init__(self):
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,   
        )
        self._model = settings.openai_embedding_model
        self._dimensions = settings.openai_embedding_dimensions  # 1536

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        Embed a list of texts. Returns vectors in same order as input.

        WHY WE DON'T EMBED ONE-AT-A-TIME:
          Embedding 100 visits = 100 API round trips = ~10 seconds latency.
          Batched: 1 API call = ~200ms.

        ORDERING GUARANTEE:
          OpenAI returns embeddings in the same order as input texts.
          We rely on this — do not sort or shuffle texts before calling.
        """
        if not texts:
            return []

        all_vectors: List[List[float]] = []
        total_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(
            "embedding_batch_start",
            total_texts=len(texts),
            total_batches=total_batches,
            model=self._model,
        )

        for batch_idx in range(0, len(texts), BATCH_SIZE):
            batch = texts[batch_idx : batch_idx + BATCH_SIZE]
            batch_num = batch_idx // BATCH_SIZE + 1

            logger.debug(
                "embedding_batch",
                batch=f"{batch_num}/{total_batches}",
                size=len(batch),
            )

            vectors = await self._embed_batch_with_retry(batch)
            all_vectors.extend(vectors)

        logger.info(
            "embedding_batch_complete",
            total_embedded=len(all_vectors),
        )

        return all_vectors

    async def embed_single(self, text: str) -> List[float]:
        """
        Embed a single query text (used during retrieval, not ingestion).

        WHY SEPARATE METHOD:
          Retrieval needs one vector per query.
          Calling embed_texts(["query"]) works but [0] indexing is awkward.
          This is a convenience wrapper with a clearer name.
        """
        vectors = await self._embed_batch_with_retry([text])
        return vectors[0]

    @retry(
        retry=retry_if_exception_type((RateLimitError, APIStatusError)),
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=RETRY_WAIT_MIN, max=RETRY_WAIT_MAX),
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )
    async def _embed_batch_with_retry(self, texts: List[str]) -> List[List[float]]:
        """
        Call OpenAI embedding API for one batch with retry.

        The @retry decorator handles:
        - RateLimitError (429): exponential backoff
        - APIStatusError (5xx): server errors, transient

        If MAX_RETRIES exhausted: reraise=True propagates the exception.
        Caller (embed_texts) lets it bubble up — the ingestion service
        catches it and marks those visits as embedding_status="failed".

        DIMENSIONS PARAMETER:
          text-embedding-3-small supports Matryoshka embeddings.
          Passing dimensions=1536 gives full resolution.
          Pass dimensions=256 for 6x faster retrieval at slight quality cost.
          For clinical use, we always use full dimensions.
        """
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
            dimensions=self._dimensions,
        )

        # OpenAI returns objects sorted by index — extract just the vectors
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
