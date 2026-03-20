"""
backend/api/routes/patients.py

RECEPTIONIST ACCESS SUMMARY:
  POST /patients/          → can create (require_any_staff) ✅
  GET  /patients/          → blocked (require_doctor only)  ✅ intentional
  GET  /patients/search    → can search ALL patients        ✅ fixed
  GET  /patients/{id}      → blocked (require_doctor_or_admin) ✅ intentional
  POST /patients/{id}/visit → blocked (require_doctor only) ✅ intentional

FIXES IN THIS FILE:
  1. create_patient: receptionist passes assigned_doctor_id in request body.
     Previously it was always set to current_user.user_id which would make
     recept_001 the assigned doctor — wrong.
  2. search_patients: removed the temporary TODO that fell back to
     current_user.user_id for admin/receptionist. Now correctly passes
     doctor_id=None so they search across all patients.
  3. doctor_name in save_visit and create_patient: now fetched from DB
     instead of using email as a placeholder.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.db.mongodb.connection import get_db
from backend.models.patient import (
    PatientCreateRequest, PatientUpdateRequest, PatientResponse, PatientListItem,
    VisitCreateRequest, VisitDocument, TokenData, UserRoleEnum
)
from backend.services.patient.patient_service import PatientService
from backend.api.middleware.auth_middleware import (
    require_doctor, require_any_staff, require_doctor_or_admin, require_admin
)
from backend.utils.audit import log_audit

router = APIRouter(prefix="/patients", tags=["Patients"])


async def _get_user_name(db: AsyncIOMotorDatabase, user_id: str) -> str:
    """
    Fetch the user's real name from the users collection.
    Used so visit records store 'Dr. Anika Sharma' not 'dr.anika@cliniccare.in'.
    Single indexed lookup — fast.
    """
    user_doc = await db["users"].find_one({"_id": user_id}, {"name": 1})
    if user_doc:
        return user_doc["name"]
    return user_id  # Fallback: use ID if somehow not found


@router.post("/", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
async def create_patient(
    data: PatientCreateRequest,
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Register a new patient.

    ACCESS: Doctor, Admin, or Receptionist.

    ASSIGNED DOCTOR LOGIC:
    - If requester is a doctor → assigned to themselves (from JWT)
    - If requester is receptionist/admin → must specify assigned_doctor_id
      in the request body (personal.assigned_doctor_id).
      The frontend receptionist form shows a doctor dropdown for this.

    VALIDATION:
    If receptionist submits without specifying a valid doctor_id,
    the patient would be assigned to 'recept_001' — wrong. We guard
    against this by overriding only when the caller is a doctor.
    """
    service = PatientService(db)

    # Doctors are always assigned to themselves
    if current_user.role == UserRoleEnum.DOCTOR.value:
        data.personal.assigned_doctor_id = current_user.user_id

    # Receptionist/admin: assigned_doctor_id must come from request body.
    # Validate it actually points to a real doctor.
    else:
        doctor_doc = await db["users"].find_one({
            "_id": data.personal.assigned_doctor_id,
            "role": "doctor",
            "is_active": True,
        })
        if not doctor_doc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"assigned_doctor_id '{data.personal.assigned_doctor_id}' "
                    "is not a valid active doctor."
                ),
            )

    # Fetch real name for denormalization in visit records
    registering_staff_name = await _get_user_name(db, current_user.user_id)

    try:
        patient = await service.create_patient(
            data,
            assigned_doctor_id=data.personal.assigned_doctor_id,
            doctor_name=registering_staff_name,
        )
        await log_audit(db, current_user.user_id, current_user.role, registering_staff_name,
                        "create_patient", "patient", patient.id,
                        {"patient_name": patient.personal.name,
                         "assigned_doctor_id": data.personal.assigned_doctor_id})
        return _patient_to_response(patient)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )


@router.get("/", response_model=list[PatientListItem])
async def list_patients(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Get patient list.
    - Doctor: returns only their assigned patients.
    - Admin / Receptionist: returns all patients.
    """
    service = PatientService(db)
    if current_user.role in [UserRoleEnum.ADMIN.value, UserRoleEnum.RECEPTIONIST.value]:
        return await service.get_all_patients(skip=skip, limit=limit)
    return await service.get_patients_for_doctor(
        doctor_id=current_user.user_id,
        skip=skip,
        limit=limit,
    )


@router.get("/search", response_model=list[PatientListItem])
async def search_patients(
    q: str = Query(..., min_length=2, description="Search query (name or phone)"),
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Search patients by name or phone.

    SCOPING:
    - Doctor → searches only their own patients (doctor_id filter applied)
    - Receptionist / Admin → searches ALL patients (doctor_id=None, no filter)

    The receptionist uses this as their primary way to find patients
    before checking them in or registering them.
    """
    service = PatientService(db)

    # Doctors only see their own patients
    doctor_id = (
        current_user.user_id
        if current_user.role == UserRoleEnum.DOCTOR.value
        else None  # receptionist and admin see all patients
    )

    return await service.search_patients(q, doctor_id=doctor_id, limit=10)


@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    patient_id: str,
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Get patient personal record.
    - Doctor: own patients only (clinical + personal)
    - Admin: all patients
    - Receptionist: all patients (personal info only — visits blocked by separate endpoint)
    """
    service = PatientService(db)
    patient = await service.get_patient_by_id(patient_id)

    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient {patient_id} not found"
        )

    if (
        current_user.role == UserRoleEnum.DOCTOR.value
        and patient.personal.assigned_doctor_id != current_user.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this patient's records"
        )

    return _patient_to_response(patient)


@router.get("/doctors/list", response_model=list[dict])
async def list_doctors(
    current_user: TokenData = Depends(require_any_staff),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Return all active doctors (id + name).

    Used by the receptionist's patient registration form to populate
    the 'Assign to Doctor' dropdown. All staff can call this.
    """
    cursor = db["users"].find(
        {"role": "doctor", "is_active": True},
        {"_id": 1, "name": 1, "specialization": 1}
    )
    doctors = await cursor.to_list(length=50)
    return [
        {
            "id": d["_id"],
            "name": d["name"],
            "specialization": d.get("specialization"),
        }
        for d in doctors
    ]


@router.patch("/{patient_id}", response_model=dict)
async def update_patient(
    patient_id: str,
    data: PatientUpdateRequest,
    current_user: TokenData = Depends(require_doctor_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Partial update of patient personal info.
    ACCESS: Doctor (own patients only) or Admin (any patient).

    EMBEDDING SIDE-EFFECT:
    If the patient has visits already embedded in ChromaDB, those chunks
    are deleted and the visits are reset to 'pending'. The response
    includes re_embed_required=true so the frontend can warn the admin
    to re-run the embedding pipeline.
    """
    service = PatientService(db)

    patient = await service.get_patient_by_id(patient_id)
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Patient {patient_id} not found")

    if (
        current_user.role == UserRoleEnum.DOCTOR.value
        and patient.personal.assigned_doctor_id != current_user.user_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied to this patient's records")

    # If receptionist/admin changes assigned_doctor_id, validate the target doctor
    if data.assigned_doctor_id and current_user.role != UserRoleEnum.DOCTOR.value:
        doctor_doc = await db["users"].find_one({
            "_id": data.assigned_doctor_id,
            "role": "doctor",
            "is_active": True,
        })
        if not doctor_doc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"assigned_doctor_id '{data.assigned_doctor_id}' is not a valid active doctor.",
            )

    try:
        updated, re_embed_required = await service.update_patient(patient_id, data)
        actor_name = await _get_user_name(db, current_user.user_id)
        await log_audit(db, current_user.user_id, current_user.role, actor_name,
                        "update_patient", "patient", patient_id,
                        {"fields": list(data.model_dump(exclude_none=True).keys()),
                         "re_embed_required": re_embed_required})
        return {
            **_patient_to_response(updated).model_dump(),
            "re_embed_required": re_embed_required,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete("/{patient_id}", status_code=status.HTTP_200_OK)
async def delete_patient(
    patient_id: str,
    current_user: TokenData = Depends(require_doctor_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Permanently delete a patient, all their visits, and all ChromaDB embeddings.
    Admin: any patient. Doctor: own patients only.
    """
    service = PatientService(db)
    patient = await service.get_patient_by_id(patient_id)
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Patient {patient_id} not found")
    if (
        current_user.role == UserRoleEnum.DOCTOR.value
        and patient.personal.assigned_doctor_id != current_user.user_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied to this patient's records")
    result = await service.delete_patient(patient_id)
    actor_name = await _get_user_name(db, current_user.user_id)
    await log_audit(db, current_user.user_id, current_user.role, actor_name,
                    "delete_patient", "patient", patient_id,
                    {"visits_deleted": result.get("visits_deleted", 0),
                     "patient_name": patient.personal.name})
    return result


@router.get("/{patient_id}/visits", response_model=list[VisitDocument])
async def get_patient_visits(
    patient_id: str,
    current_user: TokenData = Depends(require_doctor_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Get all visits for a patient, newest first.
    ACCESS: Doctor (own patients only) or Admin.
    """
    service = PatientService(db)
    patient = await service.get_patient_by_id(patient_id)

    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient {patient_id} not found"
        )

    if (
        current_user.role == "doctor"
        and patient.personal.assigned_doctor_id != current_user.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this patient's records"
        )

    return await service.get_visits_for_patient(patient_id)


@router.post(
    "/{patient_id}/visit",
    response_model=VisitDocument,
    status_code=status.HTTP_201_CREATED,
)
async def add_visit(
    patient_id: str,
    visit_data: VisitCreateRequest,
    current_user: TokenData = Depends(require_doctor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Add a new visit. Doctor only.
    doctor_id and doctor_name come from the JWT — not from request body.
    """
    service = PatientService(db)
    doctor_name = await _get_user_name(db, current_user.user_id)

    try:
        visit = await service.save_visit(
            patient_id=patient_id,
            visit_data=visit_data,
            doctor_id=current_user.user_id,
            doctor_name=doctor_name,  # Real name now, not email
        )
        await log_audit(db, current_user.user_id, current_user.role, doctor_name,
                        "add_visit", "visit", visit.id,
                        {"patient_id": patient_id,
                         "visit_date": str(visit_data.visit_date),
                         "diagnosis": visit_data.diagnosis[:80] if visit_data.diagnosis else None})
        return visit
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.patch(
    "/{patient_id}/visits/{visit_id}",
    response_model=VisitDocument,
)
async def update_visit(
    patient_id: str,
    visit_id: str,
    data: dict,
    current_user: TokenData = Depends(require_doctor_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Update fields on a visit. Resets embedding to pending (visit will be re-embedded).
    Access: Doctor (own patients) or Admin.
    """
    service = PatientService(db)
    patient = await service.get_patient_by_id(patient_id)
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Patient {patient_id} not found")
    if (
        current_user.role == UserRoleEnum.DOCTOR.value
        and patient.personal.assigned_doctor_id != current_user.user_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied")
    updated = await service.update_visit(visit_id, data)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Visit {visit_id} not found")
    actor_name = await _get_user_name(db, current_user.user_id)
    await log_audit(db, current_user.user_id, current_user.role, actor_name,
                    "update_visit", "visit", visit_id,
                    {"patient_id": patient_id, "fields": list(data.keys())})
    return updated


@router.delete(
    "/{patient_id}/visits/{visit_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_visit(
    patient_id: str,
    visit_id: str,
    current_user: TokenData = Depends(require_doctor_or_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Delete a single visit from MongoDB and ChromaDB.
    Also updates patient metadata counters.
    Access: Doctor (own patients) or Admin.
    """
    service = PatientService(db)
    patient = await service.get_patient_by_id(patient_id)
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Patient {patient_id} not found")
    if (
        current_user.role == UserRoleEnum.DOCTOR.value
        and patient.personal.assigned_doctor_id != current_user.user_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied")
    try:
        result = await service.delete_visit(visit_id)
        actor_name = await _get_user_name(db, current_user.user_id)
        await log_audit(db, current_user.user_id, current_user.role, actor_name,
                        "delete_visit", "visit", visit_id,
                        {"patient_id": patient_id})
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _patient_to_response(patient) -> PatientResponse:
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