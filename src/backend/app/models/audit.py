"""Audit log of administrative actions taken by admin accounts (services.audit writes it)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.columns import created_at, in_check, uuid_pk
from app.models.enums import AuditAction, AuditResult


class AuditEvent(Base):
    """One administrative action: who did what, to which record, and how it ended."""

    __tablename__ = "audit_log"
    __table_args__ = (
        in_check("action", AuditAction, name="action"),
        in_check("result", AuditResult, name="result"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details_object"),
        # the log is read newest first
        Index("ix_audit_log_created_at", "created_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    created_at: Mapped[datetime] = created_at()
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str]
    result: Mapped[str]
    # the user or class acted on; no foreign key, since it can be either
    target_id: Mapped[UUID | None] = mapped_column(index=True)
    # anything else worth keeping, e.g. the changed fields or the old and new owner
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    ip_address: Mapped[str | None]
    trace_id: Mapped[str | None]
