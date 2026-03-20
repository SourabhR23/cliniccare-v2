"""
backend/tools/rag_tools.py

RAG TOOLS — Wraps RAGService as LangGraph @tool functions.

IMPORT PATH FIX:
  The project serves rag_service.py as `backend.rag_service`
  (flat file at repo root, not backend/services/rag_service.py).
  Import: from backend.rag_service import RAGService
"""

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase
from langchain_core.tools import tool

logger = structlog.get_logger(__name__)


def create_rag_tools(db: AsyncIOMotorDatabase, redis_client=None):
    """Factory — injects db and redis into RAG tools via closure."""

    # Correct import path: backend.rag_service (flat file, not services subpackage)
    from backend.rag.rag_service import RAGService

    @tool
    async def rag_query(query: str, patient_id: str) -> dict:
        """
        Answer a clinical question about a patient using their visit history.
        Uses hybrid search (vector + BM25) + CrossEncoder reranking + GPT-4o-mini.

        Args:
            query: The clinical question
            patient_id: The patient's MongoDB _id (e.g. PT92D3B32E)

        Returns:
            dict with 'answer', 'sources', 'cached'
        """
        try:
            service = RAGService(db, redis_client)
            result = await service.query(query=query, patient_id=patient_id)
            return result
        except Exception as e:
            logger.error("tool_rag_query_error", error=str(e), patient_id=patient_id)
            return {
                "answer": "Unable to retrieve patient records at this time.",
                "sources": [],
                "cached": False,
                "error": str(e),
            }

    @tool
    async def previsit_brief(patient_id: str) -> dict:
        """
        Generate a structured pre-visit brief for a patient covering chronic
        conditions, current medications, recurring symptoms, follow-ups, and alerts.

        Args:
            patient_id: The patient's MongoDB _id

        Returns:
            dict with 'brief' and 'sources'
        """
        try:
            service = RAGService(db, redis_client)
            result = await service.get_previsit_brief(patient_id=patient_id)
            return result
        except Exception as e:
            logger.error("tool_previsit_brief_error", error=str(e), patient_id=patient_id)
            return {
                "brief": "Unable to generate pre-visit brief at this time.",
                "sources": [],
                "error": str(e),
            }

    @tool
    async def lookup_patient_by_name(name: str) -> dict:
        """
        Look up a patient by name to get their real patient_id.
        Call this FIRST whenever a patient name is mentioned but no patient_id is available.

        Args:
            name: Full or partial patient name (e.g. "Arun Kumar", "Ajay")

        Returns:
            dict with 'patient_id', 'name', 'age', 'found' — or found=False if no match
        """
        try:
            import re
            name_stripped = name.strip()
            if len(name_stripped) < 2:
                return {"found": False, "error": "Name too short to search"}
            escaped = re.escape(name_stripped)
            cursor = db["patients"].find(
                {"personal.name": {"$regex": escaped, "$options": "i"}},
                {"_id": 1, "personal.name": 1, "personal.date_of_birth": 1,
                 "personal.sex": 1, "metadata.total_visits": 1},
            ).limit(5)
            docs = await cursor.to_list(5)
            if not docs:
                return {"found": False, "message": f"No patient found matching '{name}'"}
            best = docs[0]
            from datetime import date
            dob = best.get("personal", {}).get("date_of_birth")
            age = None
            if dob:
                try:
                    birth = date.fromisoformat(str(dob)[:10])
                    age = (date.today() - birth).days // 365
                except Exception:
                    pass
            return {
                "found": True,
                "patient_id": str(best["_id"]),
                "name": best.get("personal", {}).get("name", ""),
                "age": age,
                "sex": best.get("personal", {}).get("sex", ""),
                "total_visits": best.get("metadata", {}).get("total_visits", 0),
                "all_matches": [
                    {"patient_id": str(d["_id"]), "name": d.get("personal", {}).get("name", "")}
                    for d in docs
                ],
            }
        except Exception as e:
            logger.error("tool_lookup_patient_error", error=str(e), name=name)
            return {"found": False, "error": str(e)}

    return [rag_query, previsit_brief, lookup_patient_by_name]