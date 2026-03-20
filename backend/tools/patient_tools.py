"""
backend/tools/patient_tools.py

Correct import paths (verified from existing project routes):
  PatientService → backend.services.patient.patient_service
  Models        → backend.models.patient
"""

from typing import Optional
import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase
from langchain_core.tools import tool

logger = structlog.get_logger(__name__)


def create_patient_tools(db: AsyncIOMotorDatabase):

    from backend.services.patient.patient_service import PatientService

    @tool
    async def search_patients(query: str) -> dict:
        """
        Search patients by name or phone across all patients (no doctor filter).
        Use when checking in a patient or finding if a patient exists.

        Args:
            query: Name or phone number (min 2 characters)

        Returns:
            dict with 'results' list and 'count'
        """
        try:
            service = PatientService(db)
            results = await service.search_patients(
                query=query,
                doctor_id=None,
                limit=5,
            )
            return {
                "results": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "age": p.age,
                        "phone": p.phone,
                        "total_visits": p.total_visits,
                        "last_visit_date": str(p.last_visit_date) if p.last_visit_date else None,
                        "known_allergies": p.known_allergies,
                    }
                    for p in results
                ],
                "count": len(results),
            }
        except Exception as e:
            logger.error("tool_search_patients_error", error=str(e))
            raise

    @tool
    async def get_patient(patient_id: str) -> dict:
        """
        Fetch full patient record by patient ID.
        Use when confirming a returning patient's details before check-in.

        Args:
            patient_id: MongoDB _id e.g. PT92D3B32E

        Returns:
            dict with patient demographics and metadata
        """
        try:
            service = PatientService(db)
            patient = await service.get_patient_by_id(patient_id)
            if not patient:
                return {"error": f"Patient {patient_id} not found"}

            p = patient.personal
            m = patient.metadata
            # Resolve doctor name so it's available for notification emails
            doctor_name = None
            if p.assigned_doctor_id:
                doc = await db["users"].find_one(
                    {"_id": p.assigned_doctor_id}, {"name": 1}
                )
                if doc:
                    doctor_name = doc["name"]

            return {
                "id": patient.id,
                "name": p.name,
                "age": p.age,
                "sex": p.sex.value,
                "phone": p.phone,
                "email": str(p.email) if p.email else None,
                "blood_group": p.blood_group.value,
                "known_allergies": p.known_allergies,
                "chronic_conditions": p.chronic_conditions,
                "assigned_doctor_id": p.assigned_doctor_id,
                "assigned_doctor_name": doctor_name,
                "total_visits": m.total_visits,
                "last_visit_date": str(m.last_visit_date) if m.last_visit_date else None,
                "pending_followup_date": str(m.pending_followup_date) if m.pending_followup_date else None,
            }
        except Exception as e:
            logger.error("tool_get_patient_error", error=str(e), patient_id=patient_id)
            raise

    @tool
    async def get_doctors_list() -> dict:
        """
        Get all active doctors with IDs, names, and specializations.
        Use when registering a new patient who needs a doctor assigned.

        Returns:
            dict with 'doctors' list
        """
        try:
            cursor = db["users"].find(
                {"role": "doctor", "is_active": True},
                {"_id": 1, "name": 1, "specialization": 1}
            )
            doctors = await cursor.to_list(length=50)
            return {
                "doctors": [
                    {
                        "id": d["_id"],
                        "name": d["name"],
                        "specialization": d.get("specialization"),
                    }
                    for d in doctors
                ]
            }
        except Exception as e:
            logger.error("tool_get_doctors_error", error=str(e))
            raise

    @tool
    async def create_patient(
        name: str,
        date_of_birth: str,
        sex: str,
        phone: str,
        assigned_doctor_id: str,
        email: Optional[str] = None,
        address: Optional[str] = None,
        emergency_contact: Optional[str] = None,
    ) -> dict:
        """
        Register a new patient. Only call after search_patients confirms no record exists.

        Args:
            name: Full name
            date_of_birth: ISO format "YYYY-MM-DD"
            sex: "M", "F", or "O"
            phone: 10 digits or with country code
            assigned_doctor_id: Doctor _id from get_doctors_list
            email: Optional
            address: Optional
            emergency_contact: Optional phone number

        Returns:
            dict with 'patient_id' on success, or 'error' on failure
        """
        try:
            from backend.models.patient import (
                PatientCreateRequest, PersonalInfo, SexEnum,
            )
            from datetime import date as date_type

            sex_map = {"M": SexEnum.MALE, "F": SexEnum.FEMALE, "O": SexEnum.OTHER}

            personal = PersonalInfo(
                name=name,
                date_of_birth=date_type.fromisoformat(date_of_birth),
                sex=sex_map.get(sex.upper(), SexEnum.OTHER),
                phone=phone,
                assigned_doctor_id=assigned_doctor_id,
                email=email,
                address=address,
                emergency_contact=emergency_contact,
            )

            doctor_doc = await db["users"].find_one({
                "_id": assigned_doctor_id,
                "role": "doctor",
                "is_active": True,
            })
            if not doctor_doc:
                return {"error": f"Doctor {assigned_doctor_id} not found or inactive"}

            data = PatientCreateRequest(personal=personal)
            service = PatientService(db)
            patient = await service.create_patient(
                data=data,
                assigned_doctor_id=assigned_doctor_id,
                doctor_name=doctor_doc["name"],
            )
            return {
                "patient_id": patient.id,
                "name": patient.personal.name,
                "message": "Patient registered successfully",
            }

        except ValueError as e:
            return {"error": str(e), "error_type": "duplicate"}
        except Exception as e:
            logger.error("tool_create_patient_error", error=str(e))
            raise

    return [search_patients, get_patient, get_doctors_list, create_patient]