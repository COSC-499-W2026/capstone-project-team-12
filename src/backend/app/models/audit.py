"""Audit log of the changes admin accounts make (services.audit writes it)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.columns import created_at, in_check, uuid_pk
from app.models.enums import AuditAction


class AuditEvent(Base):
    """One change an admin made: who did what, to which account and class, and when."""

    __tablename__ = "admin_audit_log"
    __table_args__ = (
        in_check("action", AuditAction, name="action"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details_object"),
        # the log is read newest first
        Index("ix_admin_audit_log_created_at", "created_at"),
    )

    id: Mapped[UUID] = uuid_pk()
    created_at: Mapped[datetime] = created_at()
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str]
    # the account acted on; for a class transfer, the new owner
    target_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # set only when the action is on a class
    target_class_id: Mapped[UUID | None] = mapped_column(ForeignKey("classes.id"), index=True)
    # anything else worth keeping, e.g. the changed fields or the previous owner
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
