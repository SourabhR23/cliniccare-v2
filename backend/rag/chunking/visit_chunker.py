"""
rag/chunking/visit_chunker.py

WHY A DEDICATED CHUNKER (vs chunking inside the embedder):
  Separation of concerns. The chunker knows about clinical data structure.
  The embedder knows about OpenAI API. Mixing them makes both harder to test.

CHUNKING STRATEGY — ONE VISIT = ONE CHUNK:
  Alternative considered: split long visits into overlapping windows.
  Why we don't: a visit is already a coherent semantic unit. The diagnosis,
  symptoms, and medications belong together. Splitting "Patient has fever"
  from "Diagnosed with malaria" would destroy the clinical signal.

  If a visit's chunk exceeds the embedding model's token limit (8191 for
  text-embedding-3-small), we truncate the notes field first — it's
  the least clinically critical. Diagnosis, symptoms, medications are
  always preserved.

CHROMADB METADATA DESIGN:
  ChromaDB's WHERE filter only works on metadata fields, NOT on chunk text.
  So we store all filterable dimensions as metadata:
  - patient_id: filter by patient (pre-visit brief)
  - doctor_id: filter by doctor (audit)
  - visit_date: filter by date range (recent visits)
  - medication_names: list → filter "find visits WHERE Azithromycin prescribed"
  - diagnosis: useful for clustering similar cases

  ChromaDB supports: str, int, float, bool. Lists of str are supported
  for $contains queries. We store date as ISO string (str) for range queries.
"""

from datetime import date
from typing import Optional
from backend.models.patient import VisitDocument


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

# text-embedding-3-small limit: 8191 tokens ≈ ~6000 words
# We cap notes at 1500 chars to stay well within limits
NOTES_MAX_CHARS = 1500

# Chunk ID format: deterministic from visit_id → allows upsert on re-embed
# If a visit is edited and re-embedded, same chunk_id = update, not duplicate
CHUNK_ID_PREFIX = "visit_chunk"


# ─────────────────────────────────────────────────────────────
# CHUNK BUILDER
# ─────────────────────────────────────────────────────────────

def build_chunk_text(visit: VisitDocument) -> str:
    """
    Convert a VisitDocument into a single chunk string for embedding.

    WHY THIS FORMAT:
      LLMs and embedding models are trained on natural language, not JSON.
      "Chief Complaint: fever and cough" embeds better than
      {"chief_complaint": "fever and cough"}.

      We use a structured prose format:
      - Field labels anchor semantic meaning ("Diagnosis:" not just the value)
      - Line breaks improve tokenizer efficiency
      - Clinical order matches how a doctor reads a chart

    TRUNCATION STRATEGY:
      If notes are very long, we truncate. The embedding model has a
      token limit. Notes are truncated first because they're typically
      less structured and less discriminative for retrieval.
    """
    # ── Core clinical fields (never truncated) ───────────────
    lines = [
        f"Patient: {visit.patient_name}",
        f"Doctor: {visit.doctor_name}",
        f"Visit Date: {visit.visit_date.isoformat()}",
        f"Visit Type: {visit.visit_type.value}",
        "",
        f"Chief Complaint: {visit.chief_complaint}",
        f"Symptoms: {visit.symptoms}",
        f"Diagnosis: {visit.diagnosis}",
    ]

    # ── Optional fields ──────────────────────────────────────
    if visit.diagnosis_code:
        lines.append(f"Diagnosis Code: {visit.diagnosis_code}")

    if visit.bp:
        lines.append(f"Blood Pressure: {visit.bp}")

    if visit.weight_kg:
        lines.append(f"Weight: {visit.weight_kg} kg")

    # ── Medications: structured for retrieval ────────────────
    if visit.medications:
        lines.append("")
        lines.append("Medications Prescribed:")
        for med in visit.medications:
            med_line = f"  - {med.name} {med.dose} {med.frequency} for {med.duration}"
            if med.notes:
                med_line += f" ({med.notes})"
            lines.append(med_line)

    # ── Allergies / conditions discovered this visit ─────────
    if visit.new_allergies_discovered:
        lines.append(f"New Allergies Discovered: {', '.join(visit.new_allergies_discovered)}")

    if visit.new_conditions_discovered:
        lines.append(f"New Conditions Identified: {', '.join(visit.new_conditions_discovered)}")

    # ── Follow-up ────────────────────────────────────────────
    if visit.followup_required and visit.followup_date:
        lines.append(f"Follow-up Required: {visit.followup_date.isoformat()}")
        if visit.followup_reason:
            lines.append(f"Follow-up Reason: {visit.followup_reason}")

    # ── Notes (truncated if necessary) ───────────────────────
    if visit.notes:
        truncated_notes = visit.notes[:NOTES_MAX_CHARS]
        if len(visit.notes) > NOTES_MAX_CHARS:
            truncated_notes += "... [truncated]"
        lines.append("")
        lines.append(f"Clinical Notes: {truncated_notes}")

    return "\n".join(lines)


def build_chroma_metadata(visit: VisitDocument) -> dict:
    """
    Build the ChromaDB metadata dict for a visit chunk.

    WHY THESE FIELDS:
      ChromaDB WHERE filters are how we scope retrieval.
      Without patient_id in metadata, we can't answer
      "find visits for THIS patient" — we'd get visits for all patients.

    CHROMADB TYPE CONSTRAINTS:
      Only str, int, float, bool are supported.
      - dates → ISO string (supports lexicographic range: "2024-01" to "2024-12")
      - medication_names → list[str] (supports $contains)
      - counts → int

    NOTE: doctor_id is stored even though Phase 2 doesn't filter by it.
    It's free to store and Phase 3 (audit trail, doctor analytics) will need it.
    """
    metadata = {
        # ── Primary filter keys ──────────────────────────────
        "patient_id": visit.patient_id,
        "patient_name": visit.patient_name,
        "doctor_id": visit.doctor_id,
        "doctor_name": visit.doctor_name,

        # ── Temporal ─────────────────────────────────────────
        # ISO string: supports lexicographic range queries
        # WHERE visit_date >= "2024-01-01"
        "visit_date": visit.visit_date.isoformat(),

        # ── Clinical classification ───────────────────────────
        "visit_type": visit.visit_type.value,
        "diagnosis": visit.diagnosis,

        # ── Medication filter ────────────────────────────────
        # ChromaDB list[str] → supports $contains
        # WHERE medication_names $contains "Azithromycin"
        # AFTER ✅ — ChromaDB rejects empty lists, use "none" as placeholder
        "medication_names": visit.medication_names if visit.medication_names else ["none"],

        # ── Flags ────────────────────────────────────────────
        "followup_required": visit.followup_required,
        "has_allergies": len(visit.new_allergies_discovered) > 0,

        # ── Source tracking ──────────────────────────────────
        "visit_id": visit.id,
    }

    # Optional: add follow-up date if present (for urgency filtering)
    if visit.followup_date:
        metadata["followup_date"] = visit.followup_date.isoformat()

    return metadata


def make_chunk_id(visit_id: str) -> str:
    """
    Generate a deterministic ChromaDB chunk ID from visit_id.

    WHY DETERMINISTIC:
      If a visit is edited and re-embedded, we call chroma.upsert()
      with the SAME chunk_id → ChromaDB updates the existing vector.
      Random IDs would create duplicate entries that degrade retrieval.

    Format: "visit_chunk_<visit_id>"
    Example: "visit_chunk_PAT001_V003"
    """
    return f"{CHUNK_ID_PREFIX}_{visit_id}"


# ─────────────────────────────────────────────────────────────
# PUBLIC API — single entry point
# ─────────────────────────────────────────────────────────────

class VisitChunker:
    """
    Converts a VisitDocument into everything ChromaDB needs:
    - chunk_id: deterministic, stable across re-embeds
    - chunk_text: natural language string to embed
    - metadata: filterable key-value pairs

    Usage:
        chunker = VisitChunker()
        chunk_id, chunk_text, metadata = chunker.chunk(visit)
    """

    def chunk(self, visit: VisitDocument) -> tuple[str, str, dict]:
        """
        Returns: (chunk_id, chunk_text, metadata)

        This is the ONLY method callers need. Everything else is internal.
        Keeping the public API to one method makes the embedder simple:
            for visit in pending_visits:
                chunk_id, text, meta = chunker.chunk(visit)
                embedder.embed_and_store(chunk_id, text, meta)
        """
        chunk_id = make_chunk_id(visit.id)
        chunk_text = build_chunk_text(visit)
        metadata = build_chroma_metadata(visit)
        return chunk_id, chunk_text, metadata
