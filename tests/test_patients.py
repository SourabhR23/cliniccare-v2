"""
tests/test_patients.py  —  Patient CRUD endpoints + RBAC enforcement.

Tests:
  - POST   /api/patients/              create patient
  - GET    /api/patients/              list doctor's patients
  - GET    /api/patients/search        search by name/phone
  - GET    /api/patients/doctors/list  list active doctors
  - GET    /api/patients/{id}          get single patient
"""

import pytest
from datetime import datetime

from tests.conftest import (
    DOCTOR_ID, ADMIN_ID, RECEPT_ID, DOCTOR2_ID,
    DOC_HEADERS, ADMIN_HEADERS, RECEPT_HEADERS, DOC2_HEADERS,
    sample_patient_payload,
)


@pytest.mark.integration
class TestCreatePatient:
    """POST /api/patients/"""

    async def test_doctor_creates_patient_for_self(self, client):
        """Doctor creates a patient → assigned to themselves automatically."""
        payload = sample_patient_payload(doctor_id=DOCTOR_ID)
        resp = await client.post("/api/patients/", json=payload, headers=DOC_HEADERS)
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "John Test Patient"
        assert body["sex"] == "M"
        assert body["blood_group"] == "O+"
        assert body["assigned_doctor_id"] == DOCTOR_ID

    async def test_receptionist_creates_patient_for_doctor(self, client):
        """Receptionist can create a patient and assign to a valid doctor."""
        payload = sample_patient_payload(doctor_id=DOCTOR_ID)
        resp = await client.post("/api/patients/", json=payload, headers=RECEPT_HEADERS)
        assert resp.status_code == 201
        assert resp.json()["assigned_doctor_id"] == DOCTOR_ID

    async def test_receptionist_invalid_doctor_id(self, client):
        """Receptionist submits a non-existent doctor_id → 400."""
        payload = sample_patient_payload(doctor_id="NON_EXISTENT_DOC")
        resp = await client.post("/api/patients/", json=payload, headers=RECEPT_HEADERS)
        assert resp.status_code == 400
        assert "not a valid active doctor" in resp.json()["detail"]

    async def test_admin_creates_patient(self, client):
        """Admin can also create patients."""
        payload = sample_patient_payload(doctor_id=DOCTOR_ID)
        resp = await client.post("/api/patients/", json=payload, headers=ADMIN_HEADERS)
        assert resp.status_code == 201

    async def test_create_patient_unauthenticated(self, client):
        payload = sample_patient_payload(doctor_id=DOCTOR_ID)
        resp = await client.post("/api/patients/", json=payload)
        assert resp.status_code == 401

    async def test_create_patient_missing_required_fields(self, client):
        """Missing required fields → 422 Unprocessable Entity."""
        resp = await client.post(
            "/api/patients/",
            json={"personal": {"name": "Incomplete"}},  # missing sex, blood_group etc.
            headers=DOC_HEADERS,
        )
        assert resp.status_code == 422

    async def test_create_patient_invalid_sex(self, client):
        payload = sample_patient_payload()
        payload["personal"]["sex"] = "X"  # invalid enum
        resp = await client.post("/api/patients/", json=payload, headers=DOC_HEADERS)
        assert resp.status_code == 422

    async def test_create_patient_duplicate_phone(self, client):
        """Same phone number cannot be used twice."""
        payload = sample_patient_payload(phone="+919999999999")
        await client.post("/api/patients/", json=payload, headers=DOC_HEADERS)

        # Second patient with same phone
        payload2 = sample_patient_payload(phone="+919999999999")
        payload2["personal"]["name"] = "Second Patient Same Phone"
        resp = await client.post("/api/patients/", json=payload2, headers=DOC_HEADERS)
        assert resp.status_code == 409  # conflict

    async def test_create_patient_optional_fields(self, client):
        """Patient without optional fields (email, address, allergies) should work."""
        payload = {
            "personal": {
                "name": "Minimal Patient",
                "date_of_birth": "1975-08-22",
                "sex": "F",
                "blood_group": "B-",
                "phone": "+911111111111",
                "assigned_doctor_id": DOCTOR_ID,
            }
        }
        resp = await client.post("/api/patients/", json=payload, headers=DOC_HEADERS)
        assert resp.status_code == 201
        body = resp.json()
        assert body["email"] is None
        assert body["known_allergies"] == []


@pytest.mark.integration
class TestListPatients:
    """GET /api/patients/"""

    async def test_doctor_sees_own_patients(self, client, test_db):
        """Doctor gets only their own patients."""
        # Create 2 patients for Doctor 1
        for i in range(2):
            payload = sample_patient_payload(
                doctor_id=DOCTOR_ID,
                phone=f"+9199900001{i}",
                name=f"Doc1 Patient {i}"
            )
            await client.post("/api/patients/", json=payload, headers=DOC_HEADERS)

        # Create 1 patient for Doctor 2
        payload = sample_patient_payload(
            doctor_id=DOCTOR2_ID,
            phone="+919990000199",
            name="Doc2 Patient"
        )
        # Admin creates patient for doc2
        await client.post("/api/patients/", json=payload, headers=ADMIN_HEADERS)

        resp = await client.get("/api/patients/", headers=DOC_HEADERS)
        assert resp.status_code == 200
        patients = resp.json()
        assert len(patients) == 2
        names = [p["name"] for p in patients]
        assert "Doc2 Patient" not in names

    async def test_doctor2_sees_own_patients_only(self, client, test_db):
        """Doctor 2 cannot see Doctor 1's patients."""
        # Create a patient for Doctor 1
        payload = sample_patient_payload(doctor_id=DOCTOR_ID, phone="+919001000100")
        await client.post("/api/patients/", json=payload, headers=DOC_HEADERS)

        # Doctor 2 lists — should see 0
        resp = await client.get("/api/patients/", headers=DOC2_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_receptionist_cannot_list_all_patients(self, client):
        """Receptionist does not have access to GET /patients/ (doctor-only)."""
        resp = await client.get("/api/patients/", headers=RECEPT_HEADERS)
        assert resp.status_code == 403

    async def test_admin_cannot_list_patients_via_this_route(self, client):
        """GET /api/patients/ is doctor-only; admin uses search or direct patient ID."""
        resp = await client.get("/api/patients/", headers=ADMIN_HEADERS)
        assert resp.status_code == 403

    async def test_list_patients_pagination(self, client):
        """Test skip/limit query params."""
        resp = await client.get("/api/patients/?skip=0&limit=5", headers=DOC_HEADERS)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_list_patients_unauthenticated(self, client):
        resp = await client.get("/api/patients/")
        assert resp.status_code == 401


@pytest.mark.integration
class TestSearchPatients:
    """GET /api/patients/search"""

    async def _create_patient(self, client, name: str, phone: str, doctor_id: str, headers: dict):
        payload = sample_patient_payload(doctor_id=doctor_id, phone=phone, name=name)
        resp = await client.post("/api/patients/", json=payload, headers=headers)
        assert resp.status_code == 201
        return resp.json()

    async def test_doctor_search_by_name_own_patients(self, client):
        await self._create_patient(client, "Ravi Kumar", "+919001001001", DOCTOR_ID, DOC_HEADERS)
        resp = await client.get("/api/patients/search?q=Ravi", headers=DOC_HEADERS)
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) >= 1
        assert any("Ravi" in p["name"] for p in results)

    async def test_doctor_search_does_not_see_other_doctor_patients(self, client):
        """Doctor's search is scoped to their own patients only."""
        await self._create_patient(client, "Hidden Patient", "+919001002001", DOCTOR2_ID, ADMIN_HEADERS)

        resp = await client.get("/api/patients/search?q=Hidden", headers=DOC_HEADERS)
        assert resp.status_code == 200
        results = resp.json()
        assert not any("Hidden" in p["name"] for p in results)

    async def test_receptionist_search_sees_all_patients(self, client):
        """Receptionist can search all patients across all doctors."""
        await self._create_patient(client, "Global Patient", "+919001003001", DOCTOR_ID, DOC_HEADERS)

        resp = await client.get("/api/patients/search?q=Global", headers=RECEPT_HEADERS)
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) >= 1
        assert any("Global" in p["name"] for p in results)

    async def test_search_by_phone(self, client):
        await self._create_patient(client, "Phone Searcher", "+919001004001", DOCTOR_ID, DOC_HEADERS)
        resp = await client.get("/api/patients/search?q=9001004001", headers=DOC_HEADERS)
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) >= 1

    async def test_search_query_too_short(self, client):
        """Query must be at least 2 characters."""
        resp = await client.get("/api/patients/search?q=a", headers=DOC_HEADERS)
        assert resp.status_code == 422

    async def test_search_no_results(self, client):
        resp = await client.get(
            "/api/patients/search?q=ZZZNonExistentPatient", headers=DOC_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_search_unauthenticated(self, client):
        resp = await client.get("/api/patients/search?q=test")
        assert resp.status_code == 401


@pytest.mark.integration
class TestGetPatient:
    """GET /api/patients/{patient_id}"""

    async def _create_and_get_id(self, client, doctor_id: str, headers: dict, phone: str) -> str:
        payload = sample_patient_payload(doctor_id=doctor_id, phone=phone)
        resp = await client.post("/api/patients/", json=payload, headers=headers)
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_doctor_gets_own_patient(self, client):
        pid = await self._create_and_get_id(client, DOCTOR_ID, DOC_HEADERS, "+919500000001")
        resp = await client.get(f"/api/patients/{pid}", headers=DOC_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["id"] == pid

    async def test_doctor_cannot_get_other_doctor_patient(self, client):
        """Doctor cannot access another doctor's patient."""
        pid = await self._create_and_get_id(client, DOCTOR2_ID, ADMIN_HEADERS, "+919500000002")
        resp = await client.get(f"/api/patients/{pid}", headers=DOC_HEADERS)
        assert resp.status_code == 403

    async def test_admin_gets_any_patient(self, client):
        """Admin can access any patient regardless of assigned doctor."""
        pid = await self._create_and_get_id(client, DOCTOR_ID, DOC_HEADERS, "+919500000003")
        resp = await client.get(f"/api/patients/{pid}", headers=ADMIN_HEADERS)
        assert resp.status_code == 200

    async def test_receptionist_cannot_get_patient_record(self, client):
        """Receptionist does not have access to full patient clinical records."""
        pid = await self._create_and_get_id(client, DOCTOR_ID, DOC_HEADERS, "+919500000004")
        resp = await client.get(f"/api/patients/{pid}", headers=RECEPT_HEADERS)
        assert resp.status_code == 403

    async def test_patient_not_found(self, client):
        resp = await client.get("/api/patients/PT_DOES_NOT_EXIST", headers=DOC_HEADERS)
        assert resp.status_code == 404

    async def test_get_patient_response_structure(self, client):
        """Verify response contains all expected fields."""
        pid = await self._create_and_get_id(client, DOCTOR_ID, DOC_HEADERS, "+919500000005")
        resp = await client.get(f"/api/patients/{pid}", headers=DOC_HEADERS)
        body = resp.json()
        required_fields = [
            "id", "name", "age", "sex", "blood_group", "phone",
            "known_allergies", "chronic_conditions", "assigned_doctor_id",
            "total_visits", "last_visit_date", "registered_date",
        ]
        for field in required_fields:
            assert field in body, f"Missing field: {field}"

    async def test_unauthenticated_get_patient(self, client):
        resp = await client.get("/api/patients/PT_ANY")
        assert resp.status_code == 401


@pytest.mark.integration
class TestListDoctors:
    """GET /api/patients/doctors/list"""

    async def test_list_doctors_as_doctor(self, client):
        resp = await client.get("/api/patients/doctors/list", headers=DOC_HEADERS)
        assert resp.status_code == 200
        doctors = resp.json()
        assert isinstance(doctors, list)
        # At least our 2 test doctors should be there
        assert len(doctors) >= 2
        for d in doctors:
            assert "id" in d
            assert "name" in d

    async def test_list_doctors_as_receptionist(self, client):
        """Receptionist can see doctors list (for patient assignment dropdown)."""
        resp = await client.get("/api/patients/doctors/list", headers=RECEPT_HEADERS)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_list_doctors_as_admin(self, client):
        resp = await client.get("/api/patients/doctors/list", headers=ADMIN_HEADERS)
        assert resp.status_code == 200

    async def test_list_doctors_unauthenticated(self, client):
        resp = await client.get("/api/patients/doctors/list")
        assert resp.status_code == 401

    async def test_list_doctors_excludes_inactive(self, client, test_db):
        """Inactive doctors should not appear in the list."""
        await test_db["users"].insert_one({
            "_id": "USR_INACTIVE_DOC",
            "email": "inactive.doc@test.com",
            "hashed_password": "$2b$12$dummy",
            "name": "Inactive Doctor",
            "role": "doctor",
            "specialization": None,
            "is_active": False,
            "created_at": datetime.utcnow(),
        })

        resp = await client.get("/api/patients/doctors/list", headers=DOC_HEADERS)
        ids = [d["id"] for d in resp.json()]
        assert "USR_INACTIVE_DOC" not in ids

        await test_db["users"].delete_one({"_id": "USR_INACTIVE_DOC"})
