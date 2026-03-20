"""
backend/utils/audit.py

Audit log utility — writes to `audit_logs` MongoDB collection.
Called from patient/visit CRUD and user management routes.

Schema per document:
{
    "timestamp":     <UTC datetime>,
    "actor_id":      "USR..." or "PT..." (user performing action),
    "actor_role":    "doctor" | "receptionist" | "admin",
    "actor_name":    str,
    "action":        "create_patient" | "update_patient" | "delete_patient"
                   | "add_visit"     | "update_visit"   | "delete_visit"
                   | "create_user"   | "update_user"
                   | "trigger_embed" | "login",
    "resource_type": "patient" | "visit" | "user" | "system",
    "resource_id":   str,
    "details":       dict  (field diffs, counts, extra context)
}
"""

from datetime import datetime, timezone
import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = structlog.get_logger(__name__)


async def log_audit(
    db: AsyncIOMotorDatabase,
    actor_id: str,
    actor_role: str,
    actor_name: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict | None = None,
) -> None:
    """
    Fire-and-forget audit log writer.
    Errors are logged but never raised — audit failure must NOT break CRUD.
    """
    try:
        doc = {
            "timestamp": datetime.now(timezone.utc),
            "actor_id": actor_id,
            "actor_role": actor_role,
            "actor_name": actor_name,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details or {},
        }
        await db["audit_logs"].insert_one(doc)
    except Exception as e:
        logger.error("audit_log_write_failed", action=action, resource_id=resource_id, error=str(e))
