"""
backend/api/middleware/auth_middleware.py

RBAC (ROLE-BASED ACCESS CONTROL):

  DOCTOR       → own patients only, full visit + RAG access
  RECEPTIONIST → scheduling, follow-ups, notifications via AI Agent;
                 registers/searches patients; no clinical data
  ADMIN        → system tasks only: embedding pipeline, sync, user mgmt,
                 delete patients, global patient list; NO agent chat

  Dependencies:
  - require_doctor()               → doctor only
  - require_admin()                → admin only
  - require_receptionist()         → receptionist only  (agent chat)
  - require_any_staff()            → doctor | admin | receptionist
  - require_doctor_or_admin()      → doctor | admin
  - require_receptionist_or_admin()→ receptionist | admin (legacy, kept for shared routes)
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.core.config import get_settings
from backend.db.mongodb.connection import get_db
from backend.models.patient import TokenData, UserRoleEnum
from backend.services.auth.auth_service import AuthService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> TokenData:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        auth_service = AuthService(db)
        token_data = auth_service.decode_token(token)
        return token_data
    except JWTError:
        raise credentials_exception


async def require_doctor(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    if current_user.role != UserRoleEnum.DOCTOR.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted to doctors only",
        )
    return current_user


async def require_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    if current_user.role != UserRoleEnum.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted to admins only",
        )
    return current_user


async def require_any_staff(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Any authenticated staff: doctor, admin, or receptionist."""
    allowed_roles = {
        UserRoleEnum.DOCTOR.value,
        UserRoleEnum.ADMIN.value,
        UserRoleEnum.RECEPTIONIST.value,
    }
    if current_user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff access required",
        )
    return current_user


async def require_doctor_or_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Routes accessible by both doctors and admins (not receptionist)."""
    allowed = {UserRoleEnum.DOCTOR.value, UserRoleEnum.ADMIN.value}
    if current_user.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Doctor or admin access required",
        )
    return current_user


async def require_receptionist(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """
    Receptionist only.
    Used for: /agents/chat — scheduling, follow-ups, notifications
    are receptionist responsibilities. Admins use system tools instead.
    """
    if current_user.role != UserRoleEnum.RECEPTIONIST.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted to receptionists only",
        )
    return current_user


async def require_receptionist_or_doctor(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """
    Receptionist or Doctor.
    Used for: /agents/chat
      - Receptionist → all 5 agents (RECEPTIONIST, RAG, SCHEDULING, NOTIFICATION, CALENDAR)
      - Doctor       → RAG + CALENDAR only (enforced in supervisor route_to_agent)
    Admins are excluded — they manage the system, not patients.
    """
    allowed = {UserRoleEnum.RECEPTIONIST.value, UserRoleEnum.DOCTOR.value}
    if current_user.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Receptionist or doctor access required",
        )
    return current_user


async def require_receptionist_or_doctor_or_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """
    All three staff roles.
    Used for: /agents/chat
      - Receptionist → all 5 agents
      - Doctor       → RAG + CALENDAR (own patients only)
      - Admin        → CALENDAR only (all doctors, unscoped)
    """
    allowed = {
        UserRoleEnum.RECEPTIONIST.value,
        UserRoleEnum.DOCTOR.value,
        UserRoleEnum.ADMIN.value,
    }
    if current_user.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff access required",
        )
    return current_user


async def require_receptionist_or_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """
    Routes accessible by receptionists and admins only.
    Used for: Phase 3 agents/chat endpoint — the receptionist agent
    is the primary entry point for front-desk workflows.
    Doctors use RAG directly; this gate keeps agent traffic to
    intake-facing roles.
    """
    allowed = {UserRoleEnum.RECEPTIONIST.value, UserRoleEnum.ADMIN.value}
    if current_user.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Receptionist or admin access required",
        )
    return current_user