"""
tests/test_models.py  —  Pure unit tests for Pydantic models and enums.

No database, no HTTP — just Python.
Marked: unit
"""

import pytest
from datetime import date
from pydantic import ValidationError

from backend.models.patient import (
    SexEnum, BloodGroupEnum, VisitTypeEnum, EmbeddingStatusEnum, UserRoleEnum,
    Medication, PersonalInfo, PatientMetadata, PatientCreateRequest,
    VisitCreateRequest, UserCreate,
)

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────
# ENUM TESTS
# ─────────────────────────────────────────────────────────────

class TestEnums:
    def test_sex_enum_values(self):
        assert SexEnum.MALE == "M"
        assert SexEnum.FEMALE == "F"
        assert SexEnum.OTHER == "O"

    def test_blood_group_enum_valid(self):
        assert BloodGroupEnum.A_POS == "A+"
        assert BloodGroupEnum.AB_NEG == "AB-"
        assert BloodGroupEnum.UNKNOWN == "Unknown"

    def test_visit_type_enum_exact_strings(self):
        """Backend enum values must match exactly what the frontend sends."""
        assert VisitTypeEnum.NEW_COMPLAINT == "New complaint"
        assert VisitTypeEnum.FOLLOW_UP == "Follow-up"
        assert VisitTypeEnum.ROUTINE == "Routine checkup"
        assert VisitTypeEnum.EMERGENCY == "Emergency"

    def test_embedding_status_enum(self):
        assert EmbeddingStatusEnum.PENDING == "pending"
        assert EmbeddingStatusEnum.EMBEDDED == "embedded"
        assert EmbeddingStatusEnum.FAILED == "failed"

    def test_user_role_enum(self):
        assert UserRoleEnum.DOCTOR == "doctor"
        assert UserRoleEnum.ADMIN == "admin"
        assert UserRoleEnum.RECEPTIONIST == "receptionist"

    def test_enum_from_string(self):
        """Enums should accept their string values."""
        assert SexEnum("M") == SexEnum.MALE
        assert VisitTypeEnum("Follow-up") == VisitTypeEnum.FOLLOW_UP
        assert BloodGroupEnum("O+") == BloodGroupEnum.O_POS


# ─────────────────────────────────────────────────────────────
# MEDICATION MODEL
# ─────────────────────────────────────────────────────────────

class TestMedication:
    def test_valid_medication(self):
        med = Medication(
            name="Paracetamol",
            dose="500mg",
            frequency="Twice daily",
            duration="5 days",
        )
        assert med.name == "Paracetamol"
        assert med.notes is None

    def test_medication_with_notes(self):
        med = Medication(
            name="Azithromycin",
            dose="500mg",
            frequency="Once daily",
            duration="3 days",
            notes="Take after meals",
        )
        assert med.notes == "Take after meals"

    def test_medication_name_required(self):
        with pytest.raises(ValidationError):
            Medication(dose="500mg", frequency="Once daily", duration="3 days")

    def test_medication_name_too_short(self):
        with pytest.raises(ValidationError):
            Medication(name="", dose="500mg", frequency="Once daily", duration="3 days")

    def test_medication_name_max_length(self):
        with pytest.raises(ValidationError):
            Medication(
                name="A" * 101,  # max is 100
                dose="500mg",
                frequency="Once daily",
                duration="3 days",
            )


# ─────────────────────────────────────────────────────────────
# PERSONAL INFO MODEL
# ─────────────────────────────────────────────────────────────

class TestPersonalInfo:
    def _valid_data(self):
        return {
            "name": "Jane Smith",
            "date_of_birth": "1990-03-20",
            "sex": "F",
            "blood_group": "A+",
            "phone": "+919876543210",
            "assigned_doctor_id": "USRTESTDOC1",
        }

    def test_valid_personal_info(self):
        info = PersonalInfo(**self._valid_data())
        assert info.name == "Jane Smith"
        assert info.sex == SexEnum.FEMALE
        assert info.blood_group == BloodGroupEnum.A_POS

    def test_invalid_sex(self):
        data = self._valid_data()
        data["sex"] = "X"  # not M, F, or O
        with pytest.raises(ValidationError):
            PersonalInfo(**data)

    def test_invalid_blood_group(self):
        data = self._valid_data()
        data["blood_group"] = "C+"  # not a valid blood group
        with pytest.raises(ValidationError):
            PersonalInfo(**data)

    def test_optional_fields_default(self):
        info = PersonalInfo(**self._valid_data())
        assert info.email is None
        assert info.address is None
        assert info.known_allergies == []
        assert info.chronic_conditions == []

    def test_known_allergies_list(self):
        data = self._valid_data()
        data["known_allergies"] = ["Penicillin", "Aspirin"]
        info = PersonalInfo(**data)
        assert len(info.known_allergies) == 2

    def test_age_computed(self):
        """PersonalInfo.age should be computed from date_of_birth."""
        from datetime import date as date_type
        data = self._valid_data()
        data["date_of_birth"] = "2000-01-01"
        info = PersonalInfo(**data)
        today = date_type.today()
        expected_age = today.year - 2000 - (
            0 if (today.month, today.day) >= (1, 1) else 1
        )
        assert info.age == expected_age


# ─────────────────────────────────────────────────────────────
# PATIENT CREATE REQUEST
# ─────────────────────────────────────────────────────────────

class TestPatientCreateRequest:
    def _payload(self):
        return {
            "personal": {
                "name": "Test Patient",
                "date_of_birth": "1985-06-15",
                "sex": "M",
                "blood_group": "B+",
                "phone": "+911234567890",
                "assigned_doctor_id": "USRTESTDOC1",
            }
        }

    def test_valid_create_request(self):
        req = PatientCreateRequest(**self._payload())
        assert req.personal.name == "Test Patient"

    def test_missing_personal(self):
        with pytest.raises(ValidationError):
            PatientCreateRequest()

    def test_missing_required_personal_fields(self):
        with pytest.raises(ValidationError):
            PatientCreateRequest(personal={"name": "John"})  # missing sex, blood_group etc.


# ─────────────────────────────────────────────────────────────
# VISIT CREATE REQUEST
# ─────────────────────────────────────────────────────────────

class TestVisitCreateRequest:
    def _payload(self):
        return {
            "visit_type": "New complaint",
            "chief_complaint": "Cough and fever",
            "symptoms": "Persistent cough, low-grade fever, sore throat",
            "diagnosis": "Viral upper respiratory tract infection",
            "medications": [
                {
                    "name": "Cetirizine",
                    "dose": "10mg",
                    "frequency": "Once daily",
                    "duration": "7 days",
                }
            ],
        }

    def test_valid_visit(self):
        visit = VisitCreateRequest(**self._payload())
        assert visit.visit_type == VisitTypeEnum.NEW_COMPLAINT
        assert len(visit.medications) == 1
        assert visit.followup_required is False
        assert isinstance(visit.symptoms, str)

    def test_invalid_visit_type(self):
        data = self._payload()
        data["visit_type"] = "consultation"  # not a valid enum value
        with pytest.raises(ValidationError):
            VisitCreateRequest(**data)

    def test_all_visit_types_valid(self):
        """Ensure all backend enum values are accepted."""
        valid_types = ["New complaint", "Follow-up", "Routine checkup", "Emergency"]
        base = self._payload()
        for vt in valid_types:
            base["visit_type"] = vt
            visit = VisitCreateRequest(**base)
            assert visit.visit_type == vt

    def test_followup_fields(self):
        data = self._payload()
        data["followup_required"] = True
        data["followup_date"] = "2026-04-01"
        data["followup_reason"] = "Recheck blood pressure"
        visit = VisitCreateRequest(**data)
        assert visit.followup_required is True
        assert str(visit.followup_date) == "2026-04-01"

    def test_medications_default_empty_list(self):
        data = self._payload()
        data.pop("medications")
        visit = VisitCreateRequest(**data)
        assert visit.medications == []

    def test_chief_complaint_required(self):
        data = self._payload()
        data.pop("chief_complaint")
        with pytest.raises(ValidationError):
            VisitCreateRequest(**data)


# ─────────────────────────────────────────────────────────────
# USER CREATE MODEL
# ─────────────────────────────────────────────────────────────

class TestUserCreate:
    def test_valid_user_create(self):
        user = UserCreate(
            email="newdoc@testclinic.com",
            password="SecurePass123!",
            name="New Doctor",
            role="doctor",
            specialization="Dermatology",
        )
        assert user.role == UserRoleEnum.DOCTOR
        assert user.email == "newdoc@testclinic.com"

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            UserCreate(
                email="x@test.com",
                password="pass",
                name="Name",
                role="superuser",  # not a valid role
            )

    def test_invalid_email(self):
        with pytest.raises(ValidationError):
            UserCreate(
                email="not-an-email",
                password="pass",
                name="Name",
                role="doctor",
            )


# ─────────────────────────────────────────────────────────────
# PATIENT METADATA
# ─────────────────────────────────────────────────────────────

class TestPatientMetadata:
    def test_default_metadata(self):
        meta = PatientMetadata()
        assert meta.total_visits == 0
        assert meta.last_visit_date is None
        assert meta.pending_followup_date is None
        assert meta.embedding_pending_count == 0
