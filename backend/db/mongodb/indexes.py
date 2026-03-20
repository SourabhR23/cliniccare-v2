"""
backend/db/mongodb/indexes.py

WHY INDEXES MATTER:
  Without indexes, MongoDB does a "collection scan" — reads every single
  document to find matches. With 43 patients that's fine. With 10,000
  patients, a query takes 2-3 seconds instead of <5ms.

  More importantly for us: MongoDB's text index enables BM25-style
  keyword search across visit content — the "keyword" half of our
  hybrid search pipeline.

INDEX TYPES WE USE:

  1. SINGLE FIELD INDEX:
     db.patients.createIndex({"personal.name": 1})
     1 = ascending order. Used for exact lookups and sorting by name.
     Time: O(log n) instead of O(n)

  2. COMPOUND INDEX:
     db.patients.createIndex({"personal.assigned_doctor_id": 1, "visits.visit_date": -1})
     Covers queries that filter by doctor AND sort by date.
     Rule: put equality fields first, range/sort fields last.
     -1 = descending (latest visits first)

  3. TEXT INDEX (BM25 keyword search):
     db.patients.createIndex({
       "visits.symptoms": "text",
       "visits.diagnosis": "text",
       "visits.medications.name": "text",
       "visits.notes": "text"
     }, {weights: {diagnosis: 3, medications: 2, symptoms: 1}})

     TEXT index enables $text search — MongoDB's built-in full-text search
     using BM25 scoring algorithm. Weights control relevance:
     - A match in "diagnosis" scores 3x a match in "symptoms"
     - This mimics clinical relevance (diagnosis is more authoritative)

     LIMITATION: Only one text index per collection allowed.
     All text fields must be in the same index definition.

  4. UNIQUE INDEX:
     db.users.createIndex({"email": 1}, {unique: true})
     Prevents duplicate emails at the DB level, not just app level.
     Even if application code has a bug, DB enforces uniqueness.

WHEN TO CREATE INDEXES:
  We create indexes at application startup (called from main.py).
  MongoDB's createIndex is idempotent — safe to call on every startup.
  It checks if the index already exists and skips creation if so.

  EXCEPTION: Text indexes take time to build on large collections.
  For large production datasets, create text indexes during maintenance
  windows with {background: true}.

POTENTIAL ISSUE — EMBEDDED DOCUMENTS:
  Our visits are embedded in the patient document (not a separate collection).
  Indexes on embedded array fields (visits.symptoms) work but have limits:
  - MongoDB text index on array fields indexes all array elements
  - Very large visit arrays (100+ visits) can slow down text indexing
  - Alternative at scale: move visits to a separate collection with
    patient_id as a foreign key (joins done in application layer)
  For 150 visits this is NOT an issue. Document it for future migration.
"""

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import IndexModel, ASCENDING, DESCENDING, TEXT

logger = structlog.get_logger(__name__)


async def create_all_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Creates all indexes for all collections.
    Called once at application startup.
    Safe to call multiple times — MongoDB skips existing indexes.

    TWO-COLLECTION DESIGN:
    We now index both `patients` and `visits` collections separately.
    The BM25 text index moves to `visits` (where the clinical text lives).
    The `patients` collection gets lightweight lookup indexes only.
    """
    await _create_patient_indexes(db)
    await _create_visit_indexes(db)
    await _create_user_indexes(db)
    await _create_appointment_indexes(db)
    logger.info("all_indexes_created")


async def _create_patient_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Indexes for the `patients` collection.

    TWO-COLLECTION DESIGN:
    The patients collection now only stores personal info + metadata.
    No visit data here — so NO text index needed here.
    All clinical text indexes moved to _create_visit_indexes().

    COLLECTION STRUCTURE:
    {
      _id: "PT92D3B32E",
      personal: { name, assigned_doctor_id, known_allergies, ... },
      metadata: { total_visits, last_visit_date, ... }
    }
    """
    collection = db["patients"]

    indexes = [
        # Patient name search (receptionist search bar, Add Visit modal)
        IndexModel(
            [("personal.name", ASCENDING)],
            name="idx_patient_name",
        ),

        # Doctor's patient list — most frequent query pattern
        IndexModel(
            [("personal.assigned_doctor_id", ASCENDING)],
            name="idx_doctor_patients",
        ),

        # Compound: doctor + last visit date — doctor dashboard sorted list
        IndexModel(
            [
                ("personal.assigned_doctor_id", ASCENDING),
                ("metadata.last_visit_date", DESCENDING),
            ],
            name="idx_doctor_patients_by_date",
        ),

        # Phone lookup — reception identifies returning patients by phone
        IndexModel(
            [("personal.phone", ASCENDING)],
            name="idx_patient_phone",
            unique=True,    # No two patients with same phone
            sparse=True,    # Sparse: skip docs where phone is null
        ),

        # Email lookup
        IndexModel(
            [("personal.email", ASCENDING)],
            name="idx_patient_email",
            sparse=True,
        ),

        # Pending followup — scheduling agent queries
        # "Find all patients with pending followup tomorrow"
        IndexModel(
            [("metadata.pending_followup_date", ASCENDING)],
            name="idx_pending_followup",
            sparse=True,
        ),
    ]

    await collection.create_indexes(indexes)
    logger.info("patient_indexes_created", count=len(indexes), collection="patients")


async def _create_visit_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Indexes for the `visits` collection.

    TWO-COLLECTION DESIGN:
    All visit data lives here now. This is where:
    - The BM25 text index for hybrid search lives
    - Embedding pipeline queries run
    - Scheduling agent queries follow-up dates
    - Admin analytics aggregate across doctors

    COLLECTION STRUCTURE:
    {
      _id: "VS4A2F1B3C",
      patient_id: "PT92D3B32E",
      doctor_id: "doc_001",
      visit_date: "2025-03-10",
      diagnosis: "...",
      symptoms: "...",
      medications: [...],
      medication_names: ["Azithromycin", "Paracetamol"],
      embedding_status: "pending",
      ...
    }
    """
    collection = db["visits"]

    indexes = [
        # ── PRIMARY LOOKUP INDEXES ─────────────────────────────

        # All visits for one patient — visit history tab
        IndexModel(
            [("patient_id", ASCENDING)],
            name="idx_visit_patient",
        ),

        # All visits by one doctor — doctor's own visit log
        IndexModel(
            [("doctor_id", ASCENDING)],
            name="idx_visit_doctor",
        ),

        # Compound: patient + visit_date — visit timeline, sorted newest first
        IndexModel(
            [
                ("patient_id", ASCENDING),
                ("visit_date", DESCENDING),
            ],
            name="idx_visit_patient_date",
        ),

        # Compound: doctor + patient — doctor sees their patient's visits
        # Most common RAG query: "visits for THIS patient by THIS doctor"
        IndexModel(
            [
                ("doctor_id", ASCENDING),
                ("patient_id", ASCENDING),
                ("visit_date", DESCENDING),
            ],
            name="idx_visit_doctor_patient_date",
        ),

        # ── EMBEDDING PIPELINE INDEXES ─────────────────────────

        # Admin dashboard: count pending embeddings
        # Scheduling: "find all pending visits for batch"
        IndexModel(
            [("embedding_status", ASCENDING)],
            name="idx_embedding_status",
        ),

        # Combined: status + doctor — per-doctor pending count
        IndexModel(
            [
                ("embedding_status", ASCENDING),
                ("doctor_id", ASCENDING),
            ],
            name="idx_embedding_status_doctor",
        ),

        # ── SCHEDULING AGENT INDEXES ───────────────────────────

        # Follow-up date — scheduling agent: "what follow-ups are tomorrow?"
        IndexModel(
            [("followup_date", ASCENDING)],
            name="idx_followup_date",
            sparse=True,    # Most visits have no follow-up date — sparse saves space
        ),

        # ── RAG METADATA FILTERING ─────────────────────────────

        # medication_names is a denormalized flat array
        # "find visits WHERE 'Azithromycin' IN medication_names AND doctor_id=X"
        # Used for: drug interaction history, RAG metadata filter
        IndexModel(
            [("medication_names", ASCENDING)],
            name="idx_medication_names",
        ),

        # ── TEXT INDEX (BM25 — HYBRID SEARCH COMPONENT) ───────
        #
        # This is the keyword half of our hybrid search pipeline.
        # Vector search (ChromaDB) handles semantic similarity.
        # This text index handles exact term matching.
        #
        # WHY WEIGHTS:
        # diagnosis:3 — most authoritative (structured clinical term)
        # medication_names:2 — exact drug names must rank high
        # chief_complaint:2 — structured field, precise
        # symptoms:1 — descriptive prose, less precise
        # notes:1 — supplementary, least precise
        #
        # USAGE EXAMPLE:
        # await db.visits.find(
        #     {
        #       "$text": {"$search": "Azithromycin URTI"},
        #       "doctor_id": "doc_001",
        #       "patient_id": "PT92D3B32E"
        #     },
        #     {"score": {"$meta": "textScore"}}
        # ).sort([("score", {"$meta": "textScore"})])
        #
        # LIMITATION: Only ONE text index per collection allowed.
        # Plan all text-searchable fields upfront.
        # Adding a new field later requires DROP + RECREATE the index.
        IndexModel(
            [
                ("diagnosis", TEXT),
                ("medication_names", TEXT),
                ("chief_complaint", TEXT),
                ("symptoms", TEXT),
                ("notes", TEXT),
            ],
            weights={
                "diagnosis": 3,
                "medication_names": 2,
                "chief_complaint": 2,
                "symptoms": 1,
                "notes": 1,
            },
            name="idx_visits_text_bm25",
            default_language="english",  # Enables stemming: "prescribed" matches "prescribe"
        ),
    ]

    await collection.create_indexes(indexes)
    logger.info("visit_indexes_created", count=len(indexes), collection="visits")


async def _create_user_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Indexes for the users collection (doctors, admins, receptionists).
    """
    collection = db["users"]

    indexes = [
        # Email must be unique — enforced at DB level
        # Even if app code has a bug, DB won't allow duplicates
        IndexModel(
            [("email", ASCENDING)],
            name="idx_user_email",
            unique=True,    # Attempting to insert duplicate email → DuplicateKeyError
        ),

        # Role lookup — admin queries all doctors, etc.
        IndexModel(
            [("role", ASCENDING)],
            name="idx_user_role",
        ),
    ]

    await collection.create_indexes(indexes)
    logger.info(
        "user_indexes_created",
        count=len(indexes),
        collection="users"
    )

async def _create_appointment_indexes(db: AsyncIOMotorDatabase) -> None:
    """Indexes for the appointments collection (Phase 3)."""
    collection = db["appointments"]
    indexes = [
        IndexModel([("patient_id", ASCENDING)], name="idx_apt_patient"),
        IndexModel([("doctor_id", ASCENDING)], name="idx_apt_doctor"),
        IndexModel([("appointment_date", ASCENDING)], name="idx_apt_date"),
        IndexModel([("status", ASCENDING)], name="idx_apt_status"),
        IndexModel(
            [("scheduling_thread_id", ASCENDING)],
            name="idx_apt_thread", unique=True, sparse=True,
        ),
        IndexModel(
            [("appointment_date", ASCENDING), ("status", ASCENDING)],
            name="idx_apt_date_status",
        ),
    ]
    await collection.create_indexes(indexes)
    logger.info("appointment_indexes_created", count=len(indexes))