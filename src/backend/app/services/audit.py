"""Record the changes admin accounts make, and read them back.

Only changes that succeed are recorded. Nothing here commits: the caller owns the transaction,
so an entry is saved together with the change it describes, and a change that is rolled back
leaves no entry.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.enums import AuditAction

MAX_PAGE_SIZE = 200


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

    For a class transfer, `target_user_id` is the new owner. An action that isn't one of the
    enum values raises ValueError.
    """
    values = {
        "actor_id": actor_id,
        "action": AuditAction(action),
        "target_user_id": target_user_id,
        "target_class_id": target_class_id,
        "details": details or {},
        # the clock, not now() (the start of the transaction), so two entries made in
        # one transaction are listed in the order they were recorded
        "created_at": func.clock_timestamp(),
    }
    return db.scalars(insert(AuditEvent).values(**values).returning(AuditEvent)).one()


def list_events(
    db: Session,
    *,
    actor_id: UUID | None = None,
    action: AuditAction | None = None,
    target_user_id: UUID | None = None,
    target_class_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AuditEvent]:
    """Entries newest first, matching every filter given.

    `since` is inclusive and `until` exclusive. `limit` must be 1 to MAX_PAGE_SIZE and
    `offset` at least 0, or ValueError is raised.
    """
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if offset < 0:
        raise ValueError("offset must not be negative")
    query = select(AuditEvent)
    if actor_id is not None:
        query = query.where(AuditEvent.actor_id == actor_id)
    if action is not None:
        query = query.where(AuditEvent.action == AuditAction(action))
    if target_user_id is not None:
        query = query.where(AuditEvent.target_user_id == target_user_id)
    if target_class_id is not None:
        query = query.where(AuditEvent.target_class_id == target_class_id)
    if since is not None:
        query = query.where(AuditEvent.created_at >= since)
    if until is not None:
        query = query.where(AuditEvent.created_at < until)
    query = (
        query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(query))
