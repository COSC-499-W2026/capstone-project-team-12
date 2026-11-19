"""Record administrative actions taken by admin accounts, and read them back.

Nothing here commits: the caller owns the transaction, so an entry is saved only if the
action it describes is. To keep a FAILURE or DENIED entry when the action is rolled back,
record it after the rollback and commit it.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.enums import AuditAction, AuditResult

MAX_PAGE_SIZE = 200


def record(
    actor_id: UUID,
    action: AuditAction,
    db: Session,
    *,
    result: AuditResult = AuditResult.SUCCESS,
    target_id: UUID | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    trace_id: str | None = None,
) -> AuditEvent:
    """Add one entry and return it, with its id and created_at already set.

    An action or result that isn't one of the enum values raises ValueError.
    """
    values = {
        "actor_id": actor_id,
        "action": AuditAction(action),
        "result": AuditResult(result),
        "target_id": target_id,
        "details": details or {},
        "ip_address": ip_address,
        "trace_id": trace_id,
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
    result: AuditResult | None = None,
    target_id: UUID | None = None,
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
    if result is not None:
        query = query.where(AuditEvent.result == AuditResult(result))
    if target_id is not None:
        query = query.where(AuditEvent.target_id == target_id)
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
