"""
rag/retrieval/chroma_client.py

UPDATED: Phase 2 (rev 3) — bypass chromadb Python library entirely.

WHY DIRECT HTTP:
  - chromadb 0.4.x uses /api/v1/ → ChromaDB Cloud returns 410 Gone (v1 deprecated)
  - chromadb 0.5.x/0.6.x uses /api/v2/ but crashes parsing collection config:
      KeyError: '_type'  (server's new format drops the '_type' field)
  - Direct HTTP to /api/v2/ confirmed working via check_chroma.py

COLLECTION DESIGN (unchanged):
  One collection : "clinic_visits"
  Each document  : one visit chunk
  ChromaDB ID    : "visit_chunk_<visit_id>"  (deterministic)
  Similarity     : cosine
"""

import time
from functools import lru_cache
from typing import List, Optional
import requests
import structlog

from backend.core.config import get_settings

logger = structlog.get_logger(__name__)

BASE_URL = "https://api.trychroma.com/api/v2"


# ─────────────────────────────────────────────────────────────
# LOW-LEVEL HTTP HELPER
# ─────────────────────────────────────────────────────────────

class ChromaHTTPClient:
    """
    Thin wrapper around requests for ChromaDB Cloud v2 API.
    Uses x-chroma-token header for auth (no chromadb Python SDK).
    """

    def __init__(self, api_key: str, tenant: str, database: str):
        self._tenant = tenant
        self._database = database
        self._session = requests.Session()
        self._session.headers.update({
            "x-chroma-token": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._base = f"{BASE_URL}/tenants/{tenant}/databases/{database}"

    def _url(self, path: str) -> str:
        return f"{self._base}/{path.lstrip('/')}"

    def _check(self, resp: requests.Response) -> dict:
        if not resp.ok:
            logger.error(
                "chroma_http_error",
                status=resp.status_code,
                body=resp.text[:500],
            )
            resp.raise_for_status()
        return resp.json()

    def get_or_create_collection(self, name: str, metadata: dict) -> str:
        """Returns collection_id (str)."""
        # Try GET first
        resp = self._session.get(self._url(f"collections/{name}"))
        if resp.status_code == 200:
            col = resp.json()
            logger.info("chroma_collection_found", name=name, id=col["id"])
            return col["id"]

        # 404 → create
        if resp.status_code == 404:
            body = {"name": name, "metadata": metadata}
            col = self._check(self._session.post(self._url("collections"), json=body))
            logger.info("chroma_collection_created", name=name, id=col["id"])
            return col["id"]

        # Any other error
        resp.raise_for_status()

    def count(self, collection_id: str) -> int:
        resp = self._session.get(self._url(f"collections/{collection_id}/count"))
        return self._check(resp)

    def upsert(self, collection_id: str, ids: list, embeddings: list,
               documents: list, metadatas: list) -> None:
        body = {
            "ids": ids,
            "embeddings": embeddings,
            "documents": documents,
            "metadatas": metadatas,
        }
        self._check(
            self._session.post(self._url(f"collections/{collection_id}/upsert"), json=body)
        )

    def query(self, collection_id: str, query_embeddings: list,
              n_results: int, include: list, where: Optional[dict] = None) -> dict:
        body: dict = {
            "query_embeddings": query_embeddings,
            "n_results": n_results,
            "include": include,
        }
        if where:
            body["where"] = where
        return self._check(
            self._session.post(self._url(f"collections/{collection_id}/query"), json=body)
        )

    def get_by_ids(self, collection_id: str, ids: list) -> dict:
        """
        Fetch specific chunks by ID. Returns only the IDs that actually exist.
        Used for sync-check: verify whether MongoDB-pending visits are in ChromaDB.
        """
        body = {"ids": ids, "include": ["metadatas"]}
        return self._check(
            self._session.post(self._url(f"collections/{collection_id}/get"), json=body)
        )

    def delete(self, collection_id: str, ids: list) -> None:
        body = {"ids": ids}
        self._check(
            self._session.post(self._url(f"collections/{collection_id}/delete"), json=body)
        )


# ─────────────────────────────────────────────────────────────
# SINGLETON CLIENT
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_chroma_client() -> ChromaHTTPClient:
    settings = get_settings()
    logger.info(
        "initializing_chromadb_http_client",
        tenant=settings.chroma_tenant,
        database=settings.chroma_database,
    )
    client = ChromaHTTPClient(
        api_key=settings.chroma_api_key,
        tenant=settings.chroma_tenant,
        database=settings.chroma_database,
    )
    logger.info("chromadb_http_client_ready")
    return client


# ─────────────────────────────────────────────────────────────
# COLLECTION WRAPPER  (same interface — rest of code unaffected)
# ─────────────────────────────────────────────────────────────

class ChromaVisitCollection:
    def __init__(self):
        settings = get_settings()
        self._client = get_chroma_client()
        self._collection_name = settings.chroma_collection_name

        self._col_id = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        logger.info(
            "chroma_collection_ready",
            name=self._collection_name,
            id=self._col_id,
            count=self.count(),
        )

    def upsert(self, chunk_id: str, vector: list, text: str, metadata: dict) -> None:
        self._client.upsert(
            collection_id=self._col_id,
            ids=[chunk_id],
            embeddings=[vector],
            documents=[text],
            metadatas=[metadata],
        )

    def upsert_batch(self, chunk_ids: list, vectors: list,
                     texts: list, metadatas: list) -> None:
        if not chunk_ids:
            return
        logger.info("chroma_upsert_batch", count=len(chunk_ids))
        self._client.upsert(
            collection_id=self._col_id,
            ids=chunk_ids,
            embeddings=vectors,
            documents=texts,
            metadatas=metadatas,
        )
        logger.info("chroma_upsert_complete", count=len(chunk_ids))

    def query(self, query_vector: list, n_results: int = 10,
              where: Optional[dict] = None) -> list:
        results = self._client.query(
            collection_id=self._col_id,
            query_embeddings=[query_vector],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
            where=where,
        )

        output = []
        for i, chunk_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i]
            output.append({
                "chunk_id": chunk_id,
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": distance,
                "score": 1.0 - distance,
            })
        return output

    def get_by_ids(self, chunk_ids: list) -> list:
        """Returns the subset of chunk_ids that actually exist in ChromaDB."""
        if not chunk_ids:
            return []
        result = self._client.get_by_ids(collection_id=self._col_id, ids=chunk_ids)
        return result.get("ids", [])

    def delete(self, chunk_ids: list) -> None:
        self._client.delete(collection_id=self._col_id, ids=chunk_ids)

    def count(self) -> int:
        return self._client.count(self._col_id)
