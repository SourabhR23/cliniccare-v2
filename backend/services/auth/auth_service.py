"""
backend/services/auth/auth_service.py

AUTHENTICATION FLOW:
  1. Doctor/Admin submits email + password to POST /auth/login
  2. We find user by email, verify bcrypt hash matches
  3. If valid, create JWT token with user_id, email, role
  4. Client stores token, sends it in Authorization: Bearer <token> header
  5. Our middleware decodes token, extracts user info, puts it on request
  6. Route handlers use the user info for RBAC (role-based access control)

JWT (JSON Web Token) STRUCTURE:
  Header.Payload.Signature
  Each part is base64-encoded.

  Payload contains:
  {
    "sub": "doc_001",          # subject = user ID
    "email": "dr@clinic.in",
    "role": "doctor",
    "exp": 1234567890,         # expiration timestamp
    "iat": 1234567800,         # issued at timestamp
  }

  Signature = HMAC-SHA256(Header + "." + Payload, SECRET_KEY)
  If anyone tampers with the payload, signature verification fails.

  SECURITY NOTE:
  JWT payload is BASE64 ENCODED, NOT ENCRYPTED.
  Anyone can decode and read the payload.
  Never put sensitive data (passwords, PII) in JWT payload.

BCRYPT:
  Passwords are NEVER stored as plain text.
  bcrypt(password, salt) → hash
  The salt is random and embedded in the hash — no need to store separately.
  bcrypt is slow by design (cost factor=12 means ~250ms per hash).
  This makes brute force attacks impractical.

  ISSUE: bcrypt's cost factor should be tuned to hardware.
  On Render's free tier CPU, cost=12 takes ~300ms — acceptable for login.
  On faster hardware, increase to 14. On slower, decrease to 10.
"""

from datetime import datetime, timedelta
from typing import Optional
import structlog
from jose import JWTError, jwt
from passlib.context import CryptContext
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.core.config import get_settings
from backend.models.patient import (
    UserCreate, UserDocument, UserResponse, Token, TokenData, UserRoleEnum
)

logger = structlog.get_logger(__name__)

# CryptContext manages the hashing algorithm
# schemes=["bcrypt"]: use bcrypt (best practice)
# deprecated="auto": if we ever add a new scheme, old hashes auto-flagged for rehash
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.collection = db["users"]
        self.settings = get_settings()

    def hash_password(self, password: str) -> str:
        """
        Hash a plain-text password.
        Never called with already-hashed password — bcrypt(bcrypt(pw)) is wrong.

        Time: ~300ms (intentionally slow)
        """
        return pwd_context.hash(password)

    def verify_password(self, plain: str, hashed: str) -> bool:
        """
        Verify plain password against stored hash.
        Returns True if match, False otherwise.

        TIMING ATTACK PROTECTION:
        passlib compares hashes in constant time.
        A naive string comparison would be faster on matching prefixes,
        leaking information about the hash. passlib prevents this.
        """
        return pwd_context.verify(plain, hashed)

    def create_access_token(self, user: UserDocument) -> Token:
        """
        Create JWT access token for authenticated user.

        WHAT GOES IN THE TOKEN:
        - sub (subject): user ID — used to look up user on each request
        - email: for logging and display
        - role: for RBAC without a DB lookup on every request
        - exp: expiration (server rejects expired tokens)

        WHY ROLE IN TOKEN:
        Alternative is to DB-lookup the user on every request to get their role.
        That's 1 extra MongoDB query per API call (~50ms × every request).
        Putting role in token eliminates this — tradeoff: if role changes,
        old tokens still have old role until they expire.
        For a clinic app, role rarely changes — acceptable tradeoff.
        """
        settings = get_settings()
        expire = datetime.utcnow() + timedelta(
            minutes=settings.access_token_expire_minutes
        )

        payload = {
            "sub": user.id,
            "email": user.email,
            "role": user.role.value,
            "exp": expire,
            "iat": datetime.utcnow(),
        }

        token = jwt.encode(
            payload,
            settings.secret_key,
            algorithm=settings.algorithm,
        )

        return Token(
            access_token=token,
            token_type="bearer",
            expires_in=settings.access_token_expire_minutes * 60,
            user=UserResponse(
                id=user.id,
                email=user.email,
                name=user.name,
                role=user.role.value,
                specialization=user.specialization,
                is_active=user.is_active,
            )
        )

    def decode_token(self, token: str) -> TokenData:
        """
        Decode and verify JWT token.
        Raises JWTError if token is invalid, expired, or tampered.

        Called by auth middleware on every protected request.
        Fast (~1ms) — just cryptographic verification, no DB call.
        """
        try:
            payload = jwt.decode(
                token,
                self.settings.secret_key,
                algorithms=[self.settings.algorithm],
            )
            user_id: str = payload.get("sub")
            email: str = payload.get("email")
            role: str = payload.get("role")

            if not user_id or not email or not role:
                raise JWTError("Missing required fields in token")

            return TokenData(user_id=user_id, email=email, role=role)

        except JWTError as e:
            logger.warning("token_decode_failed", error=str(e))
            raise

    async def login(self, email: str, password: str) -> Token:
        """
        Authenticate user and return token.

        SECURITY: We return the same error message whether email doesn't
        exist OR password is wrong. This prevents "email enumeration" —
        an attacker can't tell which emails are registered by trying different
        emails and seeing different error messages.
        """
        user_doc = await self.collection.find_one({"email": email.lower()})

        if not user_doc:
            # Still run password check to prevent timing attacks
            # (if we return immediately on missing user, attacker can tell
            # by the response time that the email doesn't exist)
            self.verify_password(password, "$2b$12$dummy_hash_to_waste_time")
            raise ValueError("Invalid email or password")

        user = UserDocument(**user_doc)

        if not self.verify_password(password, user.hashed_password):
            raise ValueError("Invalid email or password")

        if not user.is_active:
            raise ValueError("Account is deactivated. Contact admin.")

        logger.info("user_login", user_id=user.id, role=user.role)
        return self.create_access_token(user)

    async def create_user(self, data: UserCreate) -> UserDocument:
        """
        Create a new user (admin operation).

        IMPORTANT: User creation should only be callable by admins.
        This is enforced at the route level with RBAC middleware.
        The service itself doesn't check roles — that's the route's job.
        (Separation of concerns)
        """
        import uuid

        existing = await self.collection.find_one({"email": data.email.lower()})
        if existing:
            raise ValueError(f"User with email {data.email} already exists")

        user_db = UserDocument(
            **{"_id": "USR" + uuid.uuid4().hex[:8].upper()},
            email=data.email.lower(),
            hashed_password=self.hash_password(data.password),
            name=data.name,
            role=data.role,
            specialization=data.specialization,
        )

        await self.collection.insert_one(user_db.model_dump(by_alias=True))
        logger.info("user_created", user_id=user_db.id, role=user_db.role)
        return user_db