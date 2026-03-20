"""
backend/api/routes/auth.py

Authentication endpoints.

POST /api/auth/login   — returns JWT token
POST /api/auth/register — create new user (admin only)

FastAPI's OAuth2PasswordRequestForm expects:
  Content-Type: application/x-www-form-urlencoded
  Body: username=...&password=...

Note: the field is called `username` by OAuth2 spec even though
we store/check by email. The form username value IS the email.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.db.mongodb.connection import get_db
from backend.models.patient import UserCreate, Token, UserResponse, TokenData
from backend.services.auth.auth_service import AuthService
from backend.api.middleware.auth_middleware import require_admin

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Authenticate and return JWT token.

    Uses OAuth2PasswordRequestForm so it works with:
    - FastAPI's built-in /docs "Authorize" button
    - Standard OAuth2 clients
    - curl: -d "username=email&password=pass"

    The `username` field in the form is the doctor's email address.
    """
    auth_service = AuthService(db)
    try:
        token = await auth_service.login(
            email=form_data.username,   # OAuth2 spec calls it username; we use email
            password=form_data.password,
        )
        return token
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    data: UserCreate,
    current_user: TokenData = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Create a new user account (doctor, receptionist, or admin).

    ACCESS: Admin only.

    Role descriptions:
    - doctor       → clinical staff; manages visits, RAG, pre-visit briefs;
                     read-only calendar access
    - receptionist → operational staff; manages doctor calendars, books
                     appointments, sends notifications, handles agent outputs,
                     registers patients; NO clinical data access
    - admin        → full system access including user management and
                     embedding pipeline

    Only admins can create accounts — prevents privilege escalation where
    a doctor creates a second account with a higher role.
    """
    auth_service = AuthService(db)
    try:
        user = await auth_service.create_user(data)
        return UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role.value,
            specialization=user.specialization,
            is_active=user.is_active,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
