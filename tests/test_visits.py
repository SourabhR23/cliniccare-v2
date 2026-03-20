"""
tests/test_visits.py  —  Visit CRUD + PatientService integration tests.

Tests:
  - POST /api/patients/{id}/visit    add a new visit
  - GET  /api/patients/{id}/visits   get visit history
  - PatientService.save_visit        service-level behaviour (metadata updates)
"""

import pytest
from datetime import datetime

from backend.services.patient.patient_service import PatientService
from backend.models.patient import PatientCreateRequest, VisitCreateRequest

from tests.conftest import (
    DOCTOR_ID, DOCTOR2_ID, ADMIN_ID,
    DOC_HEADERS, ADMIN_HEADERS, RECEPT_HEADERS, DOC2_HEADERS,
    sample_patient_payload, sample_visit_payload,
)


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

async def _create_patient(client, doctor_id: str, headers: dict, phone: str) -> str:
    payload = sample_patient_payload(doctor_id=doctor_id, phone=phone)
    resp = await client.post("/api/patients/", json=payload, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


# ─────────────────────────────────────────────────────────────
# POST /api/patients/{id}/visit — ENDPOINT TESTS
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestAddVisit:
    async def test_doctor_adds_visit_to_own_patient(self, client):
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919600001001")
        resp = await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(),
            headers=DOC_HEADERS,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["patient_id"] == pid
        assert body["doctor_id"] == DOCTOR_ID
        assert body["chief_complaint"] == "Persistent headache for 3 days"
        assert body["visit_type"] == "New complaint"
        assert len(body["medications"]) == 1
        assert "_id" in body

    async def test_visit_updates_patient_total_visits(self, client):
        """After adding a visit, patient.total_visits should increment."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919600001002")

        # Verify initial total_visits = 0
        p1 = await client.get(f"/api/patients/{pid}", headers=DOC_HEADERS)
        assert p1.json()["total_visits"] == 0

        await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(),
            headers=DOC_HEADERS,
        )

        # After visit: total_visits = 1
        p2 = await client.get(f"/api/patients/{pid}", headers=DOC_HEADERS)
        assert p2.json()["total_visits"] == 1

    async def test_doctor_cannot_add_visit_to_other_doctor_patient(self, client):
        """Doctor 1 cannot add a visit to Doctor 2's patient."""
        pid = await _create_patient(client, DOCTOR2_ID, ADMIN_HEADERS, "+919600001003")
        resp = await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(),
            headers=DOC_HEADERS,
        )
        assert resp.status_code == 403

    async def test_receptionist_cannot_add_visit(self, client):
        """Receptionist does not have access to clinical data (visit-only for doctors)."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919600001004")
        resp = await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(),
            headers=RECEPT_HEADERS,
        )
        assert resp.status_code == 403

    async def test_admin_cannot_add_visit(self, client):
        """Admin is not a doctor; cannot add clinical visits."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919600001005")
        resp = await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(),
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 403

    async def test_add_visit_patient_not_found(self, client):
        resp = await client.post(
            "/api/patients/PT_NONEXISTENT/visit",
            json=sample_visit_payload(),
            headers=DOC_HEADERS,
        )
        assert resp.status_code == 404

    async def test_add_followup_visit(self, client):
        """Visit with followup_required=True should store followup_date."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919600001006")
        visit_data = sample_visit_payload(
            visit_type="Follow-up",
            followup_required=True,
            followup_date="2026-05-01",
            followup_reason="Recheck BP",
        )
        resp = await client.post(
            f"/api/patients/{pid}/visit",
            json=visit_data,
            headers=DOC_HEADERS,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["followup_required"] is True
        assert body["followup_date"] == "2026-05-01"

    async def test_add_visit_invalid_visit_type(self, client):
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919600001007")
        resp = await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(visit_type="checkup"),  # invalid enum
            headers=DOC_HEADERS,
        )
        assert resp.status_code == 422

    async def test_visit_stores_doctor_name(self, client):
        """Visit record should store the doctor's real name, not just ID."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919600001008")
        resp = await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(),
            headers=DOC_HEADERS,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["doctor_name"] == "Dr. Test Doctor"

    async def test_add_visit_unauthenticated(self, client):
        resp = await client.post(
            "/api/patients/PT_ANY/visit",
            json=sample_visit_payload(),
        )
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────
# GET /api/patients/{id}/visits — ENDPOINT TESTS
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestGetVisits:
    async def _setup_patient_with_visits(self, client, n_visits: int = 2) -> str:
        """Create a patient and add n_visits visits. Returns patient ID."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919700000100")
        for i in range(n_visits):
            await client.post(
                f"/api/patients/{pid}/visit",
                json=sample_visit_payload(chief_complaint=f"Complaint {i+1}"),
                headers=DOC_HEADERS,
            )
        return pid

    async def test_doctor_gets_own_patient_visits(self, client):
        pid = await self._setup_patient_with_visits(client, n_visits=2)
        resp = await client.get(f"/api/patients/{pid}/visits", headers=DOC_HEADERS)
        assert resp.status_code == 200
        visits = resp.json()
        assert len(visits) == 2

    async def test_visits_ordered_newest_first(self, client):
        pid = await self._setup_patient_with_visits(client, n_visits=2)
        resp = await client.get(f"/api/patients/{pid}/visits", headers=DOC_HEADERS)
        visits = resp.json()
        if len(visits) >= 2:
            # Verify visit_date is descending (service sorts by visit_date DESC)
            dates = [v["visit_date"] for v in visits]
            assert dates == sorted(dates, reverse=True)

    async def test_doctor_cannot_get_other_doctor_patient_visits(self, client):
        pid = await _create_patient(client, DOCTOR2_ID, ADMIN_HEADERS, "+919700000200")
        await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(),
            # This would fail — Doctor 2's patient, so we skip and just test GET
            headers=DOC2_HEADERS,
        )
        # Doctor 1 tries to get Doctor 2's patient visits → 403
        resp = await client.get(f"/api/patients/{pid}/visits", headers=DOC_HEADERS)
        assert resp.status_code == 403

    async def test_admin_gets_any_patient_visits(self, client):
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919700000300")
        await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(),
            headers=DOC_HEADERS,
        )
        resp = await client.get(f"/api/patients/{pid}/visits", headers=ADMIN_HEADERS)
        assert resp.status_code == 200

    async def test_receptionist_cannot_get_visits(self, client):
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919700000400")
        resp = await client.get(f"/api/patients/{pid}/visits", headers=RECEPT_HEADERS)
        assert resp.status_code == 403

    async def test_get_visits_patient_not_found(self, client):
        resp = await client.get("/api/patients/PT_NONEXISTENT/visits", headers=DOC_HEADERS)
        assert resp.status_code == 404

    async def test_get_visits_empty_list(self, client):
        """Patient with no visits returns empty list."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919700000500")
        resp = await client.get(f"/api/patients/{pid}/visits", headers=DOC_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_visit_response_structure(self, client):
        """Verify visit document has all expected fields."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919700000600")
        await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(),
            headers=DOC_HEADERS,
        )
        resp = await client.get(f"/api/patients/{pid}/visits", headers=DOC_HEADERS)
        visit = resp.json()[0]
        required_fields = [
            "_id", "patient_id", "doctor_id", "doctor_name",
            "visit_type", "chief_complaint", "symptoms", "diagnosis",
            "medications", "notes", "followup_required", "created_at",
            "embedding_status",
        ]
        for field in required_fields:
            assert field in visit, f"Missing field: {field}"

    async def test_new_visit_has_pending_embedding_status(self, client):
        """Newly created visit should have embedding_status='pending'."""
        pid = await _create_patient(client, DOCTOR_ID, DOC_HEADERS, "+919700000700")
        await client.post(
            f"/api/patients/{pid}/visit",
            json=sample_visit_payload(),
            headers=DOC_HEADERS,
        )
        resp = await client.get(f"/api/patients/{pid}/visits", headers=DOC_HEADERS)
        assert resp.json()[0]["embedding_status"] == "pending"


# ─────────────────────────────────────────────────────────────
# PatientService UNIT TESTS
# ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestPatientService:
    """Direct service-level tests (bypass HTTP layer)."""

    @pytest.fixture
    def service(self, test_db):
        return PatientService(test_db)

    async def _create_patient_doc(self, service: PatientService) -> str:
        from backend.models.patient import PersonalInfo, PatientCreateRequest
        req = PatientCreateRequest(
            personal=PersonalInfo(
                name="Service Test Patient",
                date_of_birth="1990-01-01",
                sex="F",
                blood_group="A+",
                phone="+918888888888",
                assigned_doctor_id=DOCTOR_ID,
            )
        )
        doc = await service.create_patient(
            data=req,
            assigned_doctor_id=DOCTOR_ID,
            doctor_name="Dr. Test Doctor",
        )
        return doc.id

    async def test_get_patient_by_id_found(self, service):
        pid = await self._create_patient_doc(service)
        doc = await service.get_patient_by_id(pid)
        assert doc is not None
        assert doc.id == pid
        assert doc.personal.name == "Service Test Patient"

    async def test_get_patient_by_id_not_found(self, service):
        result = await service.get_patient_by_id("PT_NONEXISTENT_ID")
        assert result is None

    async def test_save_visit_increments_total_visits(self, service):
        pid = await self._create_patient_doc(service)

        # initial state
        patient = await service.get_patient_by_id(pid)
        assert patient.metadata.total_visits == 0

        # save a visit
        visit_req = VisitCreateRequest(
            visit_type="New complaint",
            chief_complaint="Stomach pain",
            symptoms="Epigastric pain, nausea, mild bloating",
            diagnosis="Gastritis",
        )
        await service.save_visit(
            patient_id=pid,
            visit_data=visit_req,
            doctor_id=DOCTOR_ID,
            doctor_name="Dr. Test Doctor",
        )

        # check metadata updated
        patient_after = await service.get_patient_by_id(pid)
        assert patient_after.metadata.total_visits == 1
        assert patient_after.metadata.last_visit_date is not None

    async def test_save_visit_raises_for_wrong_doctor(self, service):
        """Saving a visit with wrong doctor_id should raise PermissionError."""
        pid = await self._create_patient_doc(service)  # assigned to DOCTOR_ID

        visit_req = VisitCreateRequest(
            visit_type="Routine checkup",
            chief_complaint="Annual checkup",
            symptoms="No specific complaints, routine checkup requested",
            diagnosis="Healthy, no abnormalities detected",
        )
        with pytest.raises(PermissionError):
            await service.save_visit(
                patient_id=pid,
                visit_data=visit_req,
                doctor_id=DOCTOR2_ID,  # wrong doctor
                doctor_name="Dr. Second Doctor",
            )

    async def test_get_visits_for_patient_empty(self, service):
        pid = await self._create_patient_doc(service)
        visits = await service.get_visits_for_patient(pid)
        assert visits == []

    async def test_get_patients_for_doctor(self, service):
        # Create 2 patients for DOCTOR_ID
        for i in range(2):
            from backend.models.patient import PersonalInfo, PatientCreateRequest
            phone = f"+9177700{i:05d}"
            req = PatientCreateRequest(
                personal=PersonalInfo(
                    name=f"Bulk Patient {i}",
                    date_of_birth="1985-05-05",
                    sex="M",
                    blood_group="O-",
                    phone=phone,
                    assigned_doctor_id=DOCTOR_ID,
                )
            )
            await service.create_patient(
                data=req,
                assigned_doctor_id=DOCTOR_ID,
                doctor_name="Dr. Test Doctor",
            )

        results = await service.get_patients_for_doctor(DOCTOR_ID, skip=0, limit=10)
        assert len(results) >= 2
