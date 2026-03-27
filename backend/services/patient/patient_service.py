"""
backend/services/patient/patient_service.py

TWO-COLLECTION DESIGN:
  self.patients  → `patients` collection  (personal info + metadata)
  self.visits    → `visits` collection    (all visit records)

  KEY CHANGE from single-collection:
  save_visit() now:
  1. Inserts a new document into `visits` collection
  2. $addToSet allergies/conditions on `patients` collection
  3. $inc metadata counters on `patients` collection
  All three in a single MongoDB transaction.

  get_patient_with_visits() uses asyncio.gather to run both
  queries in parallel — same wall-clock time as one query.

TRANSACTION ACROSS TWO COLLECTIONS:
  Multi-document, multi-collection transactions are supported on
  MongoDB Atlas (replica set). The session wraps operations on
  BOTH collections — if any step fails, all roll back.

  This is more powerful than single-collection transactions —
  the consistency guarantee spans both collections atomically.
"""

import uuid
import asyncio
import structlog
from datetime import date, datetime
from typing import Optional, List
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument, DESCENDING

from backend.models.patient import (
    PatientCreateRequest,
    PatientUpdateRequest,
    PatientDocument,
    PatientListItem,
    PatientResponse,
    PatientWithVisits,
    PatientMetadata,
    VisitCreateRequest,
    VisitDocument,
    EmbeddingStatusEnum,
)

logger = structlog.get_logger(__name__)


def _gen_patient_id() -> str:
    """PT + 8 uppercase hex = PT92D3B32E"""
    return "PT" + uuid.uuid4().hex[:8].upper()


def _gen_visit_id() -> str:
    """VS + 8 uppercase hex = VS4A2F1B3C"""
    return "VS" + uuid.uuid4().hex[:8].upper()


class PatientService:

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.patients = db["patients"]   # Collection 1
        self.visits = db["visits"]       # Collection 2

    # ─────────────────────────────────────────────────────────
    # CREATE PATIENT
    # ─────────────────────────────────────────────────────────

    async def create_patient(
        self,
        data: PatientCreateRequest,
        assigned_doctor_id: str,
        doctor_name: str,
    ) -> PatientDocument:
        """
        Register a new patient in the `patients` collection.
        No visits created here — visits go in `visits` collection.
        If first_visit is provided, we call save_visit() after.
        """
        patient_id = _gen_patient_id()

        patient_doc = {
            "_id": patient_id,
            "personal": {
                **data.personal.model_dump(mode="json"),
                "assigned_doctor_id": assigned_doctor_id,
                "registered_date": str(date.today()),
            },
            "metadata": PatientMetadata().model_dump(mode="json"),
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }

        await self.patients.insert_one(patient_doc)
        logger.info("patient_created", patient_id=patient_id)

        if data.first_visit:
            await self.save_visit(
                patient_id=patient_id,
                visit_data=data.first_visit,
                doctor_id=assigned_doctor_id,
                doctor_name=doctor_name,
            )

        return await self.get_patient_by_id(patient_id)

    # ─────────────────────────────────────────────────────────
    # SAVE VISIT — CORE TRANSACTION
    # ─────────────────────────────────────────────────────────

    async def save_visit(
        self,
        patient_id: str,
        visit_data: VisitCreateRequest,
        doctor_id: str,
        doctor_name: str,
    ) -> VisitDocument:
        """
        Save a visit to `visits` collection AND reconcile personal info
        in `patients` collection — all in one atomic transaction.

        TRANSACTION SPANS TWO COLLECTIONS:

        ┌─────────────────────────────────────────────────────┐
        │ BEGIN TRANSACTION                                   │
        │                                                     │
        │ Collection: visits                                  │
        │   Op 1: INSERT new visit document                   │
        │                                                     │
        │ Collection: patients                                │
        │   Op 2: $addToSet known_allergies (if new found)    │
        │   Op 3: $addToSet chronic_conditions (if new found) │
        │   Op 4: $inc total_visits, embedding_pending_count  │
        │   Op 5: $set last_visit_date, pending_followup      │
        │                                                     │
        │ SUCCESS → commit all 5 operations together          │
        │ FAILURE → roll back all — no partial state          │
        └─────────────────────────────────────────────────────┘

        WHY $addToSet IS ATOMIC AND SAFE:
        Naive approach: read allergies → append → write back
        Problem: two concurrent requests both read old list,
        both add "Latex", second write overwrites first's changes.

        $addToSet: single atomic DB operation. No read needed.
        MongoDB locks the document for the duration of the operation.
        Concurrent calls are serialized automatically.

        IDEMPOTENT:
        Running save_visit twice with same allergies won't duplicate them.
        $addToSet only adds if not already present.
        """
        # Verify patient exists and belongs to this doctor
        patient_doc = await self.patients.find_one({"_id": patient_id})
        if not patient_doc:
            raise ValueError(f"Patient {patient_id} not found")

        if patient_doc["personal"]["assigned_doctor_id"] != doctor_id:
            raise PermissionError(
                f"Doctor {doctor_id} does not have access to patient {patient_id}"
            )

        patient_name = patient_doc["personal"]["name"]
        visit_id = _gen_visit_id()

        # Build the visit document
        visit_doc = VisitDocument.from_request(
            visit_id=visit_id,
            request=visit_data,
            patient_id=patient_id,
            patient_name=patient_name,
            doctor_id=doctor_id,
            doctor_name=doctor_name,
        )
        visit_dict = visit_doc.model_dump(by_alias=True)

        # Stringify dates for MongoDB storage
        # Motor handles datetime but not Python date objects cleanly
        for field in ["visit_date", "followup_date"]:
            if visit_dict.get(field):
                visit_dict[field] = str(visit_dict[field])

        # ── TRANSACTION ────────────────────────────────────────
        async with await self.db.client.start_session() as session:
            async with session.start_transaction():

                # OPERATION 1: Insert visit into `visits` collection
                await self.visits.insert_one(visit_dict, session=session)

                # OPERATION 2: $addToSet new allergies into `patients`
                # Only fires if doctor discovered new allergies this visit
                if visit_data.new_allergies_discovered:
                    await self.patients.update_one(
                        {"_id": patient_id},
                        {
                            "$addToSet": {
                                "personal.known_allergies": {
                                    "$each": visit_data.new_allergies_discovered
                                }
                            }
                        },
                        session=session,
                    )
                    logger.info(
                        "allergies_reconciled",
                        patient_id=patient_id,
                        added=visit_data.new_allergies_discovered,
                    )

                # OPERATION 3: $addToSet new conditions
                if visit_data.new_conditions_discovered:
                    await self.patients.update_one(
                        {"_id": patient_id},
                        {
                            "$addToSet": {
                                "personal.chronic_conditions": {
                                    "$each": visit_data.new_conditions_discovered
                                }
                            }
                        },
                        session=session,
                    )
                    logger.info(
                        "conditions_reconciled",
                        patient_id=patient_id,
                        added=visit_data.new_conditions_discovered,
                    )

                # OPERATIONS 4 + 5: Update patient metadata
                pending_followup = (
                    str(visit_data.followup_date)
                    if visit_data.followup_required and visit_data.followup_date
                    else None
                )

                await self.patients.update_one(
                    {"_id": patient_id},
                    {
                        "$inc": {
                            "metadata.total_visits": 1,
                            "metadata.embedding_pending_count": 1,
                        },
                        "$set": {
                            "metadata.last_visit_date": str(visit_data.visit_date),
                            "metadata.last_visit_doctor_id": doctor_id,
                            "metadata.pending_followup_date": pending_followup,
                            "metadata.pending_followup_visit_id": (
                                visit_id if pending_followup else None
                            ),
                            "updated_at": datetime.utcnow().isoformat(),
                        }
                    },
                    session=session,
                )
        # ── END TRANSACTION ────────────────────────────────────

        logger.info(
            "visit_saved",
            visit_id=visit_id,
            patient_id=patient_id,
            doctor_id=doctor_id,
        )
        return visit_doc

    # ─────────────────────────────────────────────────────────
    # UPDATE PATIENT
    # ─────────────────────────────────────────────────────────

    async def update_patient(
        self,
        patient_id: str,
        data: PatientUpdateRequest,
    ) -> tuple:
        """
        Update patient personal info (partial update).

        EMBEDDING INVALIDATION:
        If the patient has any visits already embedded in ChromaDB,
        those embeddings are stale after a patient update (name, allergies,
        conditions etc. appear in the chunk text). We:
          1. Delete the chunks from ChromaDB
          2. Reset those visits back to embedding_status = "pending"
          3. Reset the patient's embedding_pending_count

        The admin must re-run the embed pipeline after any patient edit.
        Returns (updated_patient, re_embed_required: bool).
        """
        patient_doc = await self.patients.find_one({"_id": patient_id})
        if not patient_doc:
            raise ValueError(f"Patient {patient_id} not found")

        # Build $set dict — only touch provided fields
        update_data = data.model_dump(exclude_none=True, mode="json")
        if not update_data:
            return await self.get_patient_by_id(patient_id), False

        set_fields = {f"personal.{k}": v for k, v in update_data.items()}
        set_fields["updated_at"] = datetime.utcnow().isoformat()

        await self.patients.update_one({"_id": patient_id}, {"$set": set_fields})
        logger.info("patient_updated", patient_id=patient_id, fields=list(update_data.keys()))

        # Find embedded visits that are now stale
        embedded_visits = await self.visits.find(
            {"patient_id": patient_id, "embedding_status": EmbeddingStatusEnum.EMBEDDED.value},
            {"_id": 1, "chroma_chunk_id": 1},
        ).to_list(None)

        re_embed_required = len(embedded_visits) > 0

        if re_embed_required:
            chunk_ids = [v["chroma_chunk_id"] for v in embedded_visits if v.get("chroma_chunk_id")]
            visit_ids  = [v["_id"] for v in embedded_visits]

            # Delete stale chunks from ChromaDB
            if chunk_ids:
                try:
                    from backend.rag.retrieval.chroma_client import ChromaVisitCollection
                    chroma = ChromaVisitCollection()
                    chroma.delete(chunk_ids)
                    logger.info(
                        "chroma_chunks_deleted",
                        patient_id=patient_id,
                        count=len(chunk_ids),
                    )
                except Exception as e:
                    logger.error("chroma_delete_failed", patient_id=patient_id, error=str(e))

            # Reset visits to pending
            await self.visits.update_many(
                {"_id": {"$in": visit_ids}},
                {"$set": {
                    "embedding_status": EmbeddingStatusEnum.PENDING.value,
                    "chroma_chunk_id": None,
                    "embedded_at": None,
                }},
            )

            # Sync pending count on patient doc
            await self.patients.update_one(
                {"_id": patient_id},
                {"$set": {"metadata.embedding_pending_count": len(visit_ids)}},
            )

            logger.info(
                "embeddings_invalidated",
                patient_id=patient_id,
                visits_reset=len(visit_ids),
            )

        updated = await self.get_patient_by_id(patient_id)
        return updated, re_embed_required

    # ─────────────────────────────────────────────────────────
    # READ: PATIENT
    # ─────────────────────────────────────────────────────────

    async def get_patient_by_id(
        self, patient_id: str
    ) -> Optional[PatientDocument]:
        """Fetch patient personal info + metadata. NO visits."""
        doc = await self.patients.find_one({"_id": patient_id})
        if not doc:
            return None
        return PatientDocument(**doc)

    async def get_patient_with_visits(
        self, patient_id: str
    ) -> Optional[PatientWithVisits]:
        """
        Fetch patient AND all their visits.
        Uses asyncio.gather to run both queries IN PARALLEL.

        Wall-clock time = max(patient_query, visits_query)
        ≈ same as a single query despite being two separate queries.

        This is the two-collection "penalty" — but gather() eliminates it.
        """
        patient_doc, visit_docs = await asyncio.gather(
            self.patients.find_one({"_id": patient_id}),
            self.visits.find(
                {"patient_id": patient_id}
            ).sort("visit_date", -1).to_list(None),
        )

        if not patient_doc:
            return None

        patient = PatientDocument(**patient_doc)
        visits = [VisitDocument(**v) for v in visit_docs]

        return PatientWithVisits(
            patient=_to_patient_response(patient),
            visits=visits,
        )

    async def get_patients_for_doctor(
        self,
        doctor_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> List[PatientListItem]:
        """
        Paginated patient list.

        TWO-COLLECTION BENEFIT:
        This query touches ONLY the `patients` collection.
        No visit data loaded. Fast even for doctors with many patients.

        Projection: fetch only fields needed for list view.
        """
        projection = {
            "_id": 1,
            "personal.name": 1,
            "personal.date_of_birth": 1,
            "personal.sex": 1,
            "personal.blood_group": 1,
            "personal.phone": 1,
            "personal.known_allergies": 1,
            "personal.chronic_conditions": 1,
            "metadata.total_visits": 1,
            "metadata.last_visit_date": 1,
            "metadata.pending_followup_date": 1,
        }

        cursor = (
            self.patients
            .find({"personal.assigned_doctor_id": doctor_id}, projection)
            .sort("metadata.last_visit_date", -1)
            .skip(skip)
            .limit(limit)
        )

        result = []
        async for doc in cursor:
            p = doc["personal"]
            m = doc.get("metadata", {})
            # Calculate age from date_of_birth
            dob = p.get("date_of_birth")
            age = 0
            if dob:
                try:
                    dob_date = date.fromisoformat(str(dob)[:10])
                    today = date.today()
                    age = today.year - dob_date.year - (
                        (today.month, today.day) < (dob_date.month, dob_date.day)
                    )
                except Exception:
                    pass

            result.append(PatientListItem(
                id=doc["_id"],
                name=p["name"],
                age=age,
                sex=p["sex"],
                blood_group=p.get("blood_group", "Unknown"),
                phone=p["phone"],
                known_allergies=p.get("known_allergies", []),
                chronic_conditions=p.get("chronic_conditions", []),
                total_visits=m.get("total_visits", 0),
                last_visit_date=m.get("last_visit_date"),
                pending_followup_date=m.get("pending_followup_date"),
            ))
        return result

    async def get_all_patients(
        self,
        skip: int = 0,
        limit: int = 50,
    ) -> List[PatientListItem]:
        """All patients across all doctors — admin only."""
        projection = {
            "_id": 1,
            "personal.name": 1,
            "personal.date_of_birth": 1,
            "personal.sex": 1,
            "personal.blood_group": 1,
            "personal.phone": 1,
            "personal.known_allergies": 1,
            "personal.chronic_conditions": 1,
            "metadata.total_visits": 1,
            "metadata.last_visit_date": 1,
            "metadata.pending_followup_date": 1,
        }
        cursor = (
            self.patients
            .find({}, projection)
            .sort("metadata.last_visit_date", -1)
            .skip(skip)
            .limit(limit)
        )
        result = []
        async for doc in cursor:
            p = doc["personal"]
            m = doc.get("metadata", {})
            dob = p.get("date_of_birth")
            age = 0
            if dob:
                try:
                    dob_date = date.fromisoformat(str(dob)[:10])
                    today = date.today()
                    age = today.year - dob_date.year - (
                        (today.month, today.day) < (dob_date.month, dob_date.day)
                    )
                except Exception:
                    pass
            result.append(PatientListItem(
                id=doc["_id"],
                name=p["name"],
                age=age,
                sex=p["sex"],
                blood_group=p.get("blood_group", "Unknown"),
                phone=p["phone"],
                known_allergies=p.get("known_allergies", []),
                chronic_conditions=p.get("chronic_conditions", []),
                total_visits=m.get("total_visits", 0),
                last_visit_date=m.get("last_visit_date"),
                pending_followup_date=m.get("pending_followup_date"),
            ))
        return result

    async def search_patients(
        self,
        query: str,
        doctor_id: Optional[str],   # <-- was `str`, now Optional[str]
        limit: int = 10,
    ) -> List[PatientListItem]:
        """
        Partial name/phone search.
 
        SCOPING:
        - doctor_id provided → filter to that doctor's patients only
        - doctor_id is None  → no filter, search all patients
          (used by receptionist and admin)
        """
        import re
        escaped = re.escape(query)
 
        # Build the filter conditionally
        search_filter: dict = {
            "$or": [
                {"personal.name": {"$regex": escaped, "$options": "i"}},
                {"personal.phone": {"$regex": escaped, "$options": "i"}},
            ]
        }
 
        # Doctors are scoped to their own patients only
        if doctor_id is not None:
            search_filter["personal.assigned_doctor_id"] = doctor_id
 
        cursor = self.patients.find(
            search_filter,
            {
                "_id": 1,
                "personal.name": 1,
                "personal.date_of_birth": 1,
                "personal.sex": 1,
                "personal.blood_group": 1,
                "personal.phone": 1,
                "personal.known_allergies": 1,
                "personal.chronic_conditions": 1,
                "metadata": 1,
            }
        ).limit(limit)
 
        result = []
        async for doc in cursor:
            p = doc["personal"]
            m = doc.get("metadata", {})
            dob = p.get("date_of_birth")
            age = 0
            if dob:
                try:
                    dob_date = date.fromisoformat(str(dob)[:10])
                    today = date.today()
                    age = today.year - dob_date.year - (
                        (today.month, today.day) < (dob_date.month, dob_date.day)
                    )
                except Exception:
                    pass
            result.append(PatientListItem(
                id=doc["_id"],
                name=p["name"],
                age=age,
                sex=p["sex"],
                blood_group=p.get("blood_group", "Unknown"),
                phone=p["phone"],
                known_allergies=p.get("known_allergies", []),
                chronic_conditions=p.get("chronic_conditions", []),
                total_visits=m.get("total_visits", 0),
                last_visit_date=m.get("last_visit_date"),
                pending_followup_date=m.get("pending_followup_date"),
            ))
        return result
 

    # ─────────────────────────────────────────────────────────
    # READ: VISITS
    # ─────────────────────────────────────────────────────────

    async def get_visits_for_patient(
        self,
        patient_id: str,
        limit: int = 50,
    ) -> List[VisitDocument]:
        """
        All visits for one patient, newest first.
        Direct query on `visits` collection — no aggregation needed.
        TWO-COLLECTION BENEFIT: simple, fast, indexed query.
        """
        cursor = (
            self.visits
            .find({"patient_id": patient_id})
            .sort("visit_date", -1)
            .limit(limit)
        )
        return [VisitDocument(**v) async for v in cursor]

    # ─────────────────────────────────────────────────────────
    # DELETE PATIENT
    # ─────────────────────────────────────────────────────────

    async def delete_patient(self, patient_id: str) -> dict:
        """
        Permanently delete a patient and ALL their data:
          1. Delete all ChromaDB chunks for embedded visits
          2. Delete all visits from `visits` collection
          3. Delete patient from `patients` collection
        Returns summary counts.
        """
        # 1. Find all embedded visits → delete ChromaDB chunks
        embedded = await self.visits.find(
            {"patient_id": patient_id, "embedding_status": EmbeddingStatusEnum.EMBEDDED.value},
            {"_id": 1, "chroma_chunk_id": 1},
        ).to_list(None)

        chunk_ids = [v["chroma_chunk_id"] for v in embedded if v.get("chroma_chunk_id")]
        if chunk_ids:
            try:
                from backend.rag.retrieval.chroma_client import ChromaVisitCollection
                ChromaVisitCollection().delete(chunk_ids)
                logger.info("patient_chroma_deleted", patient_id=patient_id, chunks=len(chunk_ids))
            except Exception as e:
                logger.error("patient_chroma_delete_failed", patient_id=patient_id, error=str(e))

        # 2. Delete all visits
        visits_result = await self.visits.delete_many({"patient_id": patient_id})

        # 3. Delete patient document
        await self.patients.delete_one({"_id": patient_id})

        logger.info("patient_deleted", patient_id=patient_id,
                    visits_deleted=visits_result.deleted_count,
                    chunks_deleted=len(chunk_ids))
        return {
            "patient_id": patient_id,
            "visits_deleted": visits_result.deleted_count,
            "chunks_deleted": len(chunk_ids),
        }

    # ─────────────────────────────────────────────────────────
    # UPDATE / DELETE VISIT
    # ─────────────────────────────────────────────────────────

    async def update_visit(self, visit_id: str, data: dict) -> Optional["VisitDocument"]:
        """
        Partial update a visit. Resets embedding to pending so it gets re-embedded.
        """
        visit_doc = await self.visits.find_one({"_id": visit_id})
        if not visit_doc:
            return None

        # If currently embedded, delete stale ChromaDB chunk
        if visit_doc.get("chroma_chunk_id"):
            try:
                from backend.rag.retrieval.chroma_client import ChromaVisitCollection
                ChromaVisitCollection().delete([visit_doc["chroma_chunk_id"]])
            except Exception as e:
                logger.error("visit_chroma_delete_failed", visit_id=visit_id, error=str(e))

        # Update medication_names denormalized field if medications changed
        if "medications" in data:
            data["medication_names"] = [m.get("name", "") for m in data["medications"] if m.get("name")]

        set_fields = {k: v for k, v in data.items()}
        set_fields["embedding_status"] = EmbeddingStatusEnum.PENDING.value
        set_fields["chroma_chunk_id"] = None
        set_fields["embedded_at"] = None
        set_fields["updated_at"] = datetime.utcnow().isoformat()

        await self.visits.update_one({"_id": visit_id}, {"$set": set_fields})

        # Sync patient embedding_pending_count if it was previously embedded
        if visit_doc.get("embedding_status") == EmbeddingStatusEnum.EMBEDDED.value:
            await self.patients.update_one(
                {"_id": visit_doc["patient_id"]},
                {"$inc": {"metadata.embedding_pending_count": 1}},
            )

        updated = await self.visits.find_one({"_id": visit_id})
        return VisitDocument(**updated) if updated else None

    async def delete_visit(self, visit_id: str) -> dict:
        """
        Delete a single visit from MongoDB and ChromaDB.
        Decrements patient metadata counters.
        """
        visit_doc = await self.visits.find_one({"_id": visit_id})
        if not visit_doc:
            raise ValueError(f"Visit {visit_id} not found")

        patient_id = visit_doc["patient_id"]

        # Delete ChromaDB chunk if embedded
        if visit_doc.get("chroma_chunk_id"):
            try:
                from backend.rag.retrieval.chroma_client import ChromaVisitCollection
                ChromaVisitCollection().delete([visit_doc["chroma_chunk_id"]])
            except Exception as e:
                logger.error("visit_chroma_delete_failed", visit_id=visit_id, error=str(e))

        # Delete visit document
        await self.visits.delete_one({"_id": visit_id})

        # Recompute patient metadata from remaining visits
        remaining = await self.visits.find(
            {"patient_id": patient_id},
            {"visit_date": 1},
        ).sort("visit_date", -1).to_list(None)

        new_total = len(remaining)
        new_last_visit = remaining[0]["visit_date"] if remaining else None

        pending_count = await self.visits.count_documents(
            {"patient_id": patient_id, "embedding_status": EmbeddingStatusEnum.PENDING.value}
        )

        await self.patients.update_one(
            {"_id": patient_id},
            {"$set": {
                "metadata.total_visits": new_total,
                "metadata.last_visit_date": str(new_last_visit) if new_last_visit else None,
                "metadata.embedding_pending_count": pending_count,
                "updated_at": datetime.utcnow().isoformat(),
            }},
        )

        logger.info("visit_deleted", visit_id=visit_id, patient_id=patient_id)
        return {"visit_id": visit_id, "patient_id": patient_id}

    async def get_pending_visits(
        self,
        doctor_id: Optional[str] = None,
    ) -> List[VisitDocument]:
        """
        Fetch all visits with embedding_status = pending.
        Admin uses this to build the embedding batch.

        TWO-COLLECTION BENEFIT:
        With embedded design, this required:
        db.patients.aggregate([
            {$unwind: "$visits"},
            {$match: {"visits.embedding_status": "pending"}}
        ])
        Now it's just:
        db.visits.find({"embedding_status": "pending"})
        Simple, fast, uses the idx_embedding_status index.
        """
        query: dict = {"embedding_status": EmbeddingStatusEnum.PENDING.value}
        if doctor_id:
            query["doctor_id"] = doctor_id

        cursor = self.visits.find(query).sort("visit_date", DESCENDING)
        return [VisitDocument(**v) async for v in cursor]

    async def mark_visit_embedded(
        self,
        visit_id: str,
        chroma_chunk_id: str,
    ) -> None:
        """
        Called by embedding pipeline after successful ChromaDB upsert.
        Updates embedding_status and decrements patient metadata counter.
        """
        # Get visit to find patient_id
        visit = await self.visits.find_one({"_id": visit_id})
        if not visit:
            return

        patient_id = visit["patient_id"]

        # Update visit status
        await self.visits.update_one(
            {"_id": visit_id},
            {
                "$set": {
                    "embedding_status": EmbeddingStatusEnum.EMBEDDED.value,
                    "chroma_chunk_id": chroma_chunk_id,
                    "embedded_at": datetime.utcnow().isoformat(),
                }
            }
        )

        # Decrement patient's pending count
        await self.patients.update_one(
            {"_id": patient_id},
            {"$inc": {"metadata.embedding_pending_count": -1}}
        )

        logger.info("visit_embedded", visit_id=visit_id, chunk_id=chroma_chunk_id)


def _to_patient_response(patient: PatientDocument) -> PatientResponse:
    """Convert PatientDocument to PatientResponse."""
    p = patient.personal
    m = patient.metadata
    return PatientResponse(
        id=patient.id,
        name=p.name,
        age=p.age,
        sex=p.sex.value,
        blood_group=p.blood_group.value,
        phone=p.phone,
        email=str(p.email) if p.email else None,
        address=p.address,
        known_allergies=p.known_allergies,
        chronic_conditions=p.chronic_conditions,
        assigned_doctor_id=p.assigned_doctor_id,
        total_visits=m.total_visits,
        last_visit_date=m.last_visit_date,
        pending_followup_date=m.pending_followup_date,
        registered_date=p.registered_date,
    )