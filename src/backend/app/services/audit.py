"""Record the changes admin accounts make.

Only changes that succeed are recorded. Nothing here commits: the caller owns the transaction,
so an entry is saved together with the change it describes, and a change that is rolled back
leaves no entry.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import func, insert
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.enums import AuditAction


def record(
    actor_id: UUID,
    action: AuditAction,
    db: Session,
    *,
    target_user_id: UUID,
    target_class_id: UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add one entry and return it, with its id and created_at already set.

    For a class transfer, `target_user_id` is the new owner.
    """
    values = {
        "actor_id": actor_id,
        "action": action,
        "target_user_id": target_user_id,
        "target_class_id": target_class_id,
        "details": details or {},
        # the clock, not now() (the start of the transaction), so two entries made in
        # one transaction keep the order they were recorded in
        "created_at": func.clock_timestamp(),
    }
    return db.scalars(insert(AuditEvent).values(**values).returning(AuditEvent)).one()
