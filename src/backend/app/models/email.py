"""Email outbox (D2) and hashed link tokens; the send job creates a token only when it sends."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.columns import created_at, in_check, uuid_pk
from app.models.enums import EmailKind, EmailStatus

# priority 1 is always sent before priority 2 (spec, Email delivery)
HIGH_PRIORITY_KINDS = (
    EmailKind.RESET_PASSWORD,
    EmailKind.SET_PASSWORD,
    EmailKind.CHANGE_EMAIL,
    EmailKind.EMAIL_CHANGED_NOTICE,
)
LOW_PRIORITY_KINDS = (EmailKind.VERIFY_EMAIL, EmailKind.TA_INVITATION)

# email_tokens.purpose. The notice and the TA invitation are mail only.
TOKEN_KINDS = (
    EmailKind.VERIFY_EMAIL,
    EmailKind.RESET_PASSWORD,
    EmailKind.SET_PASSWORD,
    EmailKind.CHANGE_EMAIL,
)

MAX_EMAIL_ATTEMPTS = 5

# pending rows, priority 1 first then oldest; "due" (next_attempt_at <= now()) stays in the query
PENDING_DUE_INDEX = "ix_email_outbox_pending_priority_created"
RECENT_INDEX = "ix_email_outbox_to_email_template_created"
# a second reset request while one is still pending adds no message (spec, Email delivery)
ONE_PENDING_RESET_INDEX = "uq_email_outbox_one_pending_reset"
# EmailService.queue_email uses the same condition for ON CONFLICT. It is written as
# literals: with bound parameters Postgres can't match ON CONFLICT to this partial index
# once a prepared statement switches to a generic plan.
ONE_PENDING_RESET_WHERE = (
    f"template = '{EmailKind.RESET_PASSWORD}' AND status = '{EmailStatus.PENDING}'"
)


def _values(kinds: tuple[EmailKind, ...]) -> str:
    return ", ".join(f"'{kind}'" for kind in kinds)


def _priority_matches_template() -> str:
    # an unknown template fails the template check; this one only ties priority to a known kind
    high = _values(HIGH_PRIORITY_KINDS)
    low = _values(LOW_PRIORITY_KINDS)
    known = _values(tuple(EmailKind))
    return (
        f"(template IN ({high}) AND priority = 1) OR (template IN ({low}) AND priority = 2)"
        f" OR template NOT IN ({known})"
    )


def priority_for(kind: EmailKind) -> int:
    """1 for password and email-change mail, 2 for verification and TA invitations."""
    kind = EmailKind(kind)
    if kind in HIGH_PRIORITY_KINDS:
        return 1
    return 2


class EmailToken(Base):
    """A single-use link, stored as a SHA-256 hex hash. The raw token is never saved."""

    __tablename__ = "email_tokens"
    __table_args__ = (
        in_check("purpose", TOKEN_KINDS, name="purpose"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256"),
        CheckConstraint(
            f"(purpose = '{EmailKind.CHANGE_EMAIL}') = (new_email IS NOT NULL)",
            name="new_email_matches_purpose",
        ),
        CheckConstraint(
            "new_email IS NULL OR new_email = lower(new_email)", name="new_email_lowercase"
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    # invalidate looks up the unused links of one user
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str]
    new_email: Mapped[str | None]  # only for change_email
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]  # from send time: reset 60 min, the other links 7 days
    used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()


class EmailOutbox(Base):
    """One message waiting for the send-emails job. No token is stored on this row."""

    __tablename__ = "email_outbox"
    __table_args__ = (
        in_check("template", EmailKind, name="template"),
        in_check("status", EmailStatus, name="status"),
        # same split as priority_for, so a reset can't be stored as priority 2
        CheckConstraint(_priority_matches_template(), name="priority_matches_template"),
        CheckConstraint(f"attempts BETWEEN 0 AND {MAX_EMAIL_ATTEMPTS}", name="attempts"),
        # sent_at is set exactly when the message has gone out
        CheckConstraint(
            f"(status = '{EmailStatus.SENT}') = (sent_at IS NOT NULL)",
            name="sent_at_matches_status",
        ),
        CheckConstraint("to_email = lower(to_email)", name="to_email_lowercase"),
        CheckConstraint("jsonb_typeof(params) = 'object'", name="params_object"),
        CheckConstraint(
            "NOT (params ?| ARRAY['token', 'token_hash'])", name="params_have_no_token"
        ),
        Index(
            PENDING_DUE_INDEX,
            "priority",
            "created_at",
            postgresql_where=text(f"status = '{EmailStatus.PENDING}'"),
        ),
        # the 3-per-hour limit counts recent rows for one address and kind
        Index(RECENT_INDEX, "to_email", "template", "created_at"),
        Index(
            ONE_PENDING_RESET_INDEX,
            "user_id",
            unique=True,
            postgresql_where=text(ONE_PENDING_RESET_WHERE),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    to_email: Mapped[str]
    template: Mapped[str]  # the email kind (spec name: template)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    priority: Mapped[int]
    status: Mapped[str] = mapped_column(
        default=EmailStatus.PENDING, server_default=EmailStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_at: Mapped[datetime] = created_at()
    sent_at: Mapped[datetime | None]
