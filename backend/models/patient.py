"""
backend/models/patient.py

TWO-COLLECTION DESIGN:
  Collection 1: `patients`  — personal info + metadata only
  Collection 2: `visits`    — all visit records, linked by patient_id

  Why two collections (vs embedded visits):
  1. Patient list query loads NO visit data — faster, less bandwidth
  2. Embedding pipeline: simple query on visits collection for pending items
  3. Admin analytics across all doctors — aggregate visits directly
  4. Removes 16MB MongoDB document size concern for high-visit patients
  5. Visit-level updates (embedding_status) don't touch the patient doc

  Trade-off: Full patient + visits now needs 2 queries.
  Solved with asyncio.gather — both run in parallel, same wall-clock time.
"""

from datetime import datetime, date as date_type
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr, field_validator
from enum import Enum
import re


# ─────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────

class SexEnum(str, Enum):
    MALE = "M"
    FEMALE = "F"
    OTHER = "O"


class BloodGroupEnum(str, Enum):
    A_POS = "A+"
    A_NEG = "A-"
    B_POS = "B+"
    B_NEG = "B-"
    O_POS = "O+"
    O_NEG = "O-"
    AB_POS = "AB+"
    AB_NEG = "AB-"
    UNKNOWN = "Unknown"


class VisitTypeEnum(str, Enum):
    NEW_COMPLAINT = "New complaint"
    FOLLOW_UP = "Follow-up"
    ROUTINE = "Routine checkup"
    EMERGENCY = "Emergency"


class EmbeddingStatusEnum(str, Enum):
    PENDING = "pending"
    EMBEDDED = "embedded"
    FAILED = "failed"


class UserRoleEnum(str, Enum):
    """
    Three human roles in the system:

    DOCTOR       — clinical access only
                   sees own patient list + visit records + RAG + pre-visit brief
                   calendar: read-only (cannot create or modify appointments)

    RECEPTIONIST — operational role, NO clinical data access
                   owns doctor calendars: full read+write
                   books / updates / cancels appointments
                   receives scheduling agent outputs and acts on them
                   sends WhatsApp / email notifications to patients
                   registers patients on behalf of any doctor
                   searches all patients across all doctors

    ADMIN        — full system access
                   all of the above + user management + embedding pipeline
    """
    DOCTOR = "doctor"
    ADMIN = "admin"
    RECEPTIONIST = "receptionist"


# ─────────────────────────────────────────────────────────────
# SHARED SUB-MODELS
# ─────────────────────────────────────────────────────────────

class Medication(BaseModel):
    """
    Structured medication — NOT free text.
    Reason: drug interaction checker + RAG metadata needs exact drug name.
    Free text "Azithromycin 500mg twice daily" cannot be reliably parsed.
    """
    name: str = Field(..., min_length=1, max_length=100)
    dose: str = Field(..., max_length=50)
    frequency: str = Field(..., max_length=100)
    duration: str = Field(..., max_length=100)
    notes: Optional[str] = Field(None, max_length=200)

    @field_validator("name")
    @classmethod
    def normalize_drug_name(cls, v: str) -> str:
        return v.strip().title()


# ─────────────────────────────────────────────────────────────
# COLLECTION 1: PATIENTS
# ─────────────────────────────────────────────────────────────

class PersonalInfo(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    date_of_birth: date_type
    sex: SexEnum
    blood_group: BloodGroupEnum = BloodGroupEnum.UNKNOWN
    phone: str = Field(..., max_length=20)
    email: Optional[EmailStr] = None
    address: Optional[str] = Field(None, max_length=300)
    emergency_contact: Optional[str] = Field(None, max_length=20)
    # These live on the patient doc (not visits) for instant lookup
    # Updated via $addToSet on every visit save
    known_allergies: List[str] = Field(default_factory=list)
    chronic_conditions: List[str] = Field(default_factory=list)
    registered_date: date_type = Field(default_factory=date_type.today)
    assigned_doctor_id: str

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        """
        Normalize to +91XXXXXXXXXX.
        Handles: 9876543210 / 09876543210 / +91-9876543210
        $addToSet deduplication requires consistent format.
        """
        digits = re.sub(r"[\s\-\(\)]", "", v)
        if digits.startswith("0"):
            digits = "+91" + digits[1:]
        elif not digits.startswith("+"):
            if len(digits) == 10:
                digits = "+91" + digits
        return digits

    @property
    def age(self) -> int:
        today = date_type.today()
        return (
            today.year - self.date_of_birth.year
            - ((today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
        )

    def has_allergy(self, drug_name: str) -> bool:
        drug_lower = drug_name.lower()
        return any(
            a.lower() in drug_lower or drug_lower in a.lower()
            for a in self.known_allergies
        )


class PatientMetadata(BaseModel):
    """
    Cached derived fields — updated on every visit save.
    Avoids aggregation queries for common dashboard stats.
    Always kept in sync by patient_service.save_visit().
    """
    total_visits: int = 0
    last_visit_date: Optional[date_type] = None
    last_visit_doctor_id: Optional[str] = None
    pending_followup_date: Optional[date_type] = None
    pending_followup_visit_id: Optional[str] = None
    embedding_pending_count: int = 0


class PatientCreateRequest(BaseModel):
    """Input: new patient registration."""
    personal: PersonalInfo
    first_visit: Optional["VisitCreateRequest"] = None


class PatientUpdateRequest(BaseModel):
    """
    Partial update for patient personal info.
    Only provided (non-None) fields are applied.
    Omit any field you don't want to change.
    """
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    date_of_birth: Optional[date_type] = None
    sex: Optional[SexEnum] = None
    blood_group: Optional[BloodGroupEnum] = None
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    address: Optional[str] = Field(None, max_length=300)
    emergency_contact: Optional[str] = Field(None, max_length=20)
    known_allergies: Optional[List[str]] = None
    chronic_conditions: Optional[List[str]] = None
    assigned_doctor_id: Optional[str] = None


class PatientDocument(BaseModel):
    """
    Document in `patients` collection.
    NO visits field — visits live in `visits` collection.
    """
    model_config = {"populate_by_name": True}
    id: str = Field(..., alias="_id")
    personal: PersonalInfo
    metadata: PatientMetadata = Field(default_factory=PatientMetadata)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PatientResponse(BaseModel):
    """API response — flattened for frontend convenience."""
    id: str
    name: str
    age: int
    sex: str
    blood_group: str
    phone: str
    email: Optional[str]
    address: Optional[str]
    known_allergies: List[str]
    chronic_conditions: List[str]
    assigned_doctor_id: str
    total_visits: int
    last_visit_date: Optional[date_type]
    pending_followup_date: Optional[date_type]
    registered_date: date_type


class PatientListItem(BaseModel):
    """
    Lightweight — patient list view only.
    No visits loaded. This is the efficiency win from two collections.
    """
    id: str
    name: str
    age: int
    sex: str
    blood_group: str
    phone: str
    known_allergies: List[str]
    chronic_conditions: List[str]
    total_visits: int
    last_visit_date: Optional[date_type]
    pending_followup_date: Optional[date_type]


class PatientWithVisits(BaseModel):
    """
    Combined response built from two parallel queries.
    Never stored — assembled by service layer.
    """
    patient: PatientResponse
    visits: List["VisitDocument"]


# ─────────────────────────────────────────────────────────────
# COLLECTION 2: VISITS
# ─────────────────────────────────────────────────────────────

class VisitCreateRequest(BaseModel):
    """
    Doctor's input for saving a visit.
    patient_id → from URL path (/patients/{id}/visit)
    doctor_id  → from JWT token (never from request body)

    NOTE: Field is named `visit_date` (not `date`) to avoid the Pydantic v2
    name-clash error where a field name shadows an imported type name.
    """
    visit_date: date_type = Field(default_factory=date_type.today)
    weight_kg: Optional[float] = Field(None, ge=1, le=300)
    bp: Optional[str] = Field(None, pattern=r"^\d{2,3}\/\d{2,3}$")
    visit_type: VisitTypeEnum = VisitTypeEnum.NEW_COMPLAINT
    chief_complaint: str = Field(..., min_length=3, max_length=200)
    symptoms: str = Field(..., min_length=3, max_length=2000)
    diagnosis: str = Field(..., min_length=3, max_length=500)
    diagnosis_code: Optional[str] = Field(None, max_length=50)
    medications: List[Medication] = Field(default_factory=list)
    notes: Optional[str] = Field(None, max_length=2000)
    new_allergies_discovered: List[str] = Field(default_factory=list)
    new_conditions_discovered: List[str] = Field(default_factory=list)
    followup_required: bool = False
    followup_date: Optional[date_type] = None
    followup_reason: Optional[str] = Field(None, max_length=200)

    @field_validator("new_allergies_discovered", "new_conditions_discovered", mode="before")
    @classmethod
    def normalize_medical_terms(cls, v: List[str]) -> List[str]:
        """
        Normalize BEFORE $addToSet.
        $addToSet is case-sensitive: "Penicillin" ≠ "penicillin".
        Normalizing here ensures the strings match existing entries.
        """
        return [item.strip().title() for item in v if item.strip()]

    @field_validator("followup_date")
    @classmethod
    def followup_must_be_future(cls, v: Optional[date_type]) -> Optional[date_type]:
        if v and v <= date_type.today():
            raise ValueError("Follow-up date must be in the future")
        return v


class VisitDocument(BaseModel):
    """
    Document in `visits` collection.

    DENORMALIZED FIELDS (patient_name, doctor_name, medication_names):
    These repeat data from other collections. Why?
    - patient_name: needed in RAG chunk text — avoids join
    - doctor_name: shown in visit history — avoids join
    - medication_names: flat list for ChromaDB metadata WHERE filter
      "find visits WHERE 'Azithromycin' IN medication_names"
      Much simpler than querying nested medications[].name

    EMBEDDING FIELDS:
    - embedding_status: pending → embedded → (failed)
    - embedded_at: audit trail — when was it embedded
    - chroma_chunk_id: so we can UPDATE the vector if visit is edited

    NOTE: Field is named `visit_date` (not `date`) to avoid the Pydantic v2
    name-clash with the imported `date` type.
    """
    model_config = {"populate_by_name": True}

    id: str = Field(..., alias="_id")
    patient_id: str           # FK → patients._id
    patient_name: str         # Denormalized for RAG chunk text
    doctor_id: str            # FK → users._id
    doctor_name: str          # Denormalized for display
    visit_date: date_type
    weight_kg: Optional[float] = None
    bp: Optional[str] = None
    visit_type: VisitTypeEnum
    chief_complaint: str
    symptoms: str
    diagnosis: str
    diagnosis_code: Optional[str] = None
    medications: List[Medication] = Field(default_factory=list)
    notes: Optional[str] = None
    new_allergies_discovered: List[str] = Field(default_factory=list)
    new_conditions_discovered: List[str] = Field(default_factory=list)
    followup_required: bool = False
    followup_date: Optional[date_type] = None
    followup_reason: Optional[str] = None
    medication_names: List[str] = Field(default_factory=list)  # Denormalized

    # Embedding pipeline
    embedding_status: EmbeddingStatusEnum = EmbeddingStatusEnum.PENDING
    embedded_at: Optional[datetime] = None
    chroma_chunk_id: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @classmethod
    def from_request(
        cls,
        visit_id: str,
        request: VisitCreateRequest,
        patient_id: str,
        patient_name: str,
        doctor_id: str,
        doctor_name: str,
    ) -> "VisitDocument":
        """Factory: builds VisitDocument from VisitCreateRequest."""
        return cls(
            **{"_id": visit_id},
            patient_id=patient_id,
            patient_name=patient_name,
            doctor_id=doctor_id,
            doctor_name=doctor_name,
            medication_names=[m.name for m in request.medications],
            **request.model_dump(),
        )


# ─────────────────────────────────────────────────────────────
# AUTH MODELS
# ─────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    name: str = Field(..., min_length=2, max_length=100)
    role: UserRoleEnum
    specialization: Optional[str] = None


class UserDocument(BaseModel):
    model_config = {"populate_by_name": True}
    id: str = Field(..., alias="_id")
    email: EmailStr
    hashed_password: str
    name: str
    role: UserRoleEnum
    specialization: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    specialization: Optional[str]
    is_active: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class TokenData(BaseModel):
    user_id: str
    email: str
    role: str


PatientWithVisits.model_rebuild()
PatientCreateRequest.model_rebuild()