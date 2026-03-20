"""
backend/agents/drug_checker.py

DRUG INTERACTION CHECKER — Optional background agent.

TRIGGER: FastAPI background task after POST /patients/{id}/visit
         (not routed through supervisor — called directly)

GRAPH TYPE: Sequential
  extract_medications → query_openfda → check_interactions → update_visit → END
  (runs as background task after visit is saved — non-blocking)

CACHING:
  OpenFDA responses cached in Redis for 7 days (redis_ttl_drug_interaction).
  Same drug combination rarely needs re-checking within a week.

GUARDRAILS:
  - OpenFDA down → log warning, skip silently (visit saves regardless)
  - No drugs in visit → skip entirely
  - All queries use asyncio.gather for parallelism

OPENFDA API:
  Endpoint: https://api.fda.gov/drug/label.json?search=openfda.brand_name:"{drug}"
  Free: 240 req/min without key, 1000/min with key.
  Rate limit is per IP — Upstash Redis caching keeps actual API calls minimal.
"""

import asyncio
import json
import httpx
import structlog
from typing import List
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

OPENFDA_INTERACTION_URL = "https://api.fda.gov/drug/label.json"


async def check_drug_interactions(
    visit_id: str,
    patient_id: str,
    medication_names: List[str],
    db: AsyncIOMotorDatabase,
    redis_client=None,
) -> dict:
    """
    Main entry point — called as FastAPI background task.

    Args:
        visit_id: The visit that was just saved
        patient_id: Patient's MongoDB _id
        medication_names: List of drug names from the visit
        db: MongoDB connection
        redis_client: Optional Redis for caching

    Returns:
        dict with alerts list (empty if no interactions found)
    """
    if not medication_names or len(medication_names) < 2:
        # Need at least 2 drugs to have an interaction
        logger.info("drug_checker_skipped_insufficient_drugs",
                    visit_id=visit_id, drug_count=len(medication_names))
        return {"alerts": []}

    logger.info(
        "drug_checker_started",
        visit_id=visit_id,
        drugs=medication_names,
    )

    try:
        # Fetch interaction data for all drugs in parallel
        drug_data = await _fetch_all_drug_data(medication_names, redis_client)

        # Cross-check all drug pairs for interactions
        alerts = _find_interactions(medication_names, drug_data)

        # Write results back to visit document
        await db["visits"].update_one(
            {"_id": visit_id},
            {"$set": {"drug_interaction_alerts": alerts}},
        )

        if alerts:
            logger.warning(
                "drug_interactions_found",
                visit_id=visit_id,
                alert_count=len(alerts),
                drugs=medication_names,
            )
        else:
            logger.info("drug_checker_no_interactions", visit_id=visit_id)

        return {"alerts": alerts}

    except Exception as e:
        # Non-blocking: if checker fails, visit still saved successfully
        logger.error("drug_checker_error", visit_id=visit_id, error=str(e))
        return {"alerts": [], "error": str(e)}


async def _fetch_all_drug_data(
    medication_names: List[str],
    redis_client=None,
) -> dict:
    """
    Fetch OpenFDA label data for all drugs in parallel.
    Uses Redis cache to avoid redundant API calls.
    """
    async def fetch_one(drug_name: str) -> tuple:
        cache_key = f"openfda:{drug_name.lower().replace(' ', '_')}"

        # Check Redis cache first
        if redis_client:
            try:
                cached = await redis_client.get(cache_key)
                if cached:
                    return drug_name, json.loads(cached)
            except Exception:
                pass  # Cache miss or Redis error — proceed to API

        # Fetch from OpenFDA
        try:
            params = {
                "search": f'openfda.generic_name:"{drug_name}"',
                "limit": 1,
            }
            if settings.openfda_api_key:
                params["api_key"] = settings.openfda_api_key

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(OPENFDA_INTERACTION_URL, params=params)
                response.raise_for_status()
                data = response.json()

                # Extract drug_interactions field from label
                results = data.get("results", [])
                interactions = []
                if results:
                    interactions = results[0].get("drug_interactions", [])

                drug_data = {"interactions_text": interactions, "drug": drug_name}

                # Cache for 7 days
                if redis_client:
                    try:
                        await redis_client.setex(
                            cache_key,
                            settings.redis_ttl_drug_interaction,
                            json.dumps(drug_data),
                        )
                    except Exception:
                        pass

                return drug_name, drug_data

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return drug_name, {"interactions_text": [], "drug": drug_name}
            logger.warning("openfda_api_error", drug=drug_name, status=e.response.status_code)
            return drug_name, {"interactions_text": [], "drug": drug_name}
        except Exception as e:
            logger.warning("openfda_fetch_error", drug=drug_name, error=str(e))
            return drug_name, {"interactions_text": [], "drug": drug_name}

    # Fetch all drugs in parallel
    results = await asyncio.gather(*[fetch_one(drug) for drug in medication_names])
    return dict(results)


def _find_interactions(medication_names: List[str], drug_data: dict) -> List[dict]:
    """
    Cross-checks all drug pairs against interaction text.

    Simple string matching: if drug B's name appears in drug A's
    drug_interactions text, flag it as a potential interaction.

    In production: use a dedicated interaction database (DrugBank, RxNorm).
    OpenFDA interactions text is a starting point, not definitive.
    """
    alerts = []

    for i, drug_a in enumerate(medication_names):
        data_a = drug_data.get(drug_a, {})
        interactions_text = " ".join(data_a.get("interactions_text", []))

        for j, drug_b in enumerate(medication_names):
            if i >= j:  # Avoid duplicate pairs (A+B = B+A)
                continue

            if drug_b.lower() in interactions_text.lower():
                alerts.append({
                    "drugs": [drug_a, drug_b],
                    "severity": "moderate",  # OpenFDA doesn't classify severity
                    "detail": f"Potential interaction between {drug_a} and {drug_b}. "
                              f"Review clinical notes in OpenFDA database.",
                    "source": "OpenFDA",
                })

    return alerts
