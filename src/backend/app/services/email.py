"""Queue outgoing mail and keep link tokens hashed.

The outbox row never holds a token. The send-emails job creates the token when
it sends, so the 60 minutes on a reset link start when the mail actually leaves.
Nothing here commits: the caller owns the transaction.
"""

import hashlib
import secrets
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.email import ONE_PENDING_RESET_WHERE, EmailOutbox, EmailToken, priority_for
from app.models.enums import EmailKind

# counted from create_token, which the send job calls at send time (spec, email_tokens)
TOKEN_TTL = {
    EmailKind.VERIFY_EMAIL: timedelta(days=7),
    EmailKind.RESET_PASSWORD: timedelta(minutes=60),
    EmailKind.SET_PASSWORD: timedelta(days=7),
    EmailKind.CHANGE_EMAIL: timedelta(days=7),
}

INVALID_LINK = "Verification or reset link is invalid, used or expired"

_PENDING_RESET = text(ONE_PENDING_RESET_WHERE)  # see models/email.py


class EmailService:
    @staticmethod
    def queue_email(
        user: User,
        kind: EmailKind,
        db: Session,
        *,
        to_email: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> EmailOutbox:
        """Add one pending row at the priority of `kind`, to `to_email` or the user's address.

        A reset while one is already pending adds nothing and returns the pending row
        (spec: a second reset request adds no message), so the route answers the same
        way. If the address has changed, that row is re-addressed and its backoff
        reset; its params are kept. params must not hold a token. Does not create one.
        """
        kind = EmailKind(kind)
        to_email = (user.email if to_email is None else to_email).strip().lower()
        if not to_email:
            raise ValueError("to_email is empty")
        if params and not params.keys().isdisjoint({"token", "token_hash"}):
            raise ValueError("params must not hold a token")
        values = {
            "user_id": user.id,
            "to_email": to_email,
            "template": kind,
            "priority": priority_for(kind),
            "params": params or {},
        }
        pending_reset = (EmailOutbox.user_id == user.id, _PENDING_RESET)
        refresh = {"populate_existing": True}
        # two tries: the pending reset we collided with may be sent before we update it
        for _ in range(2):
            row = db.scalars(
                insert(EmailOutbox)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[EmailOutbox.user_id], index_where=_PENDING_RESET
                )
                .returning(EmailOutbox),
                execution_options=refresh,
            ).one_or_none()
            if row is not None:
                return row
            # a new address starts a fresh retry schedule; the status check leaves a row
            # the send job has just sent alone
            row = db.scalars(
                update(EmailOutbox)
                .where(*pending_reset, EmailOutbox.to_email.is_distinct_from(to_email))
                .values(to_email=to_email, attempts=0, next_attempt_at=func.now())
                .returning(EmailOutbox),
                execution_options=refresh,
            ).one_or_none()
            if row is None:  # same address, or already sent
                row = db.scalars(
                    select(EmailOutbox).where(*pending_reset), execution_options=refresh
                ).one_or_none()
            if row is not None:
                return row
        raise RuntimeError("could not queue the reset email")  # pragma: no cover (needs two races)

    @staticmethod
    def count_recent(email: str, kind: EmailKind, window: timedelta, db: Session) -> int:
        """How many outbox rows of this kind were queued for the address inside `window`.

        Forgot-password and resend-verification will use this for the 3-per-hour
        limit. A mail that already went out still counts.
        """
        return db.scalars(
            select(func.count())
            .select_from(EmailOutbox)
            .where(
                EmailOutbox.to_email == email.strip().lower(),
                EmailOutbox.template == EmailKind(kind),
                EmailOutbox.created_at >= func.now() - window,
            )
        ).one()

    @staticmethod
    def hash_token(raw: str) -> str:
        """SHA-256 hex of the raw token. This is what email_tokens.token_hash stores."""
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def create_token(
        user: User, kind: EmailKind, db: Session, *, new_email: str | None = None
    ) -> str:
        """Store a hashed token and return the raw token once.

        Reset lasts 60 minutes. Verify, set-password and change-email last 7 days.
        change_email needs new_email; the other kinds must not pass one. The user's
        older unused links of the same kind stop working, so only the newest one does.
        """
        kind = EmailKind(kind)
        stored_email = _new_email_for(kind, new_email)
        # two requests at once must not both keep a live link
        _lock_user(user.id, db)
        EmailService.invalidate_user_tokens(user.id, kind, db)  # raises if not a link kind
        raw = secrets.token_urlsafe(32)  # only its hash is stored
        db.add(
            EmailToken(
                user_id=user.id,
                purpose=kind,
                new_email=stored_email,
                token_hash=EmailService.hash_token(raw),
                expires_at=func.now() + TOKEN_TTL[kind],
            )
        )
        db.flush()
        return raw

    @staticmethod
    def consume_token(raw: str, kind: EmailKind, db: Session) -> EmailToken:
        """Mark the token used and return its row.

        Unknown, expired, already used, or a different kind raises AppError
        INVALID_TOKEN (400). A failed check does not mark the row used.
        """
        token_hash = EmailService.hash_token(raw)
        owner = db.scalar(select(EmailToken.user_id).where(EmailToken.token_hash == token_hash))
        if owner is not None:
            _lock_user(owner, db)  # same order as create_token: user first, then the token
        token = db.scalars(
            update(EmailToken)
            .where(
                EmailToken.token_hash == token_hash,
                EmailToken.purpose == EmailKind(kind),
                EmailToken.used_at.is_(None),
                EmailToken.expires_at > func.now(),
            )
            .values(used_at=func.now())
            .returning(EmailToken)
        ).one_or_none()
        if token is None:
            raise AppError(ErrorCode.INVALID_TOKEN, INVALID_LINK)
        return token

    @staticmethod
    def invalidate_user_tokens(user_id: UUID, kind: EmailKind, db: Session) -> None:
        """Mark this user's unused tokens of `kind` as used, so older links stop working."""
        kind = EmailKind(kind)
        if kind not in TOKEN_TTL:
            raise ValueError(f"{kind} is not a link token")
        db.execute(
            update(EmailToken)
            .where(
                EmailToken.user_id == user_id,
                EmailToken.purpose == kind,
                EmailToken.used_at.is_(None),
            )
            .values(used_at=func.now())
        )


def _new_email_for(kind: EmailKind, new_email: str | None) -> str | None:
    if kind == EmailKind.CHANGE_EMAIL:
        if new_email is None or not new_email.strip():
            raise ValueError("change_email needs a new_email")
        # format is checked by the route (Pydantic EmailStr); here only normalise
        return new_email.strip().lower()
    if new_email is not None:
        raise ValueError("new_email is only for change_email")
    return None


def _lock_user(user_id: UUID, db: Session) -> None:
    """Lock the user row until the transaction ends, always before touching email_tokens.

    FOR NO KEY UPDATE, so inserts that only reference the user (FOR KEY SHARE) aren't blocked.
    """
    db.execute(select(User.id).where(User.id == user_id).with_for_update(key_share=True))
