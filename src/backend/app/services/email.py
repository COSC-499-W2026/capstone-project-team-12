"""Queue outgoing mail and keep link tokens hashed.

The outbox row never holds a token. The send-emails job creates the token when
it sends, so the 60 minutes on a reset link start when the mail actually leaves.
Nothing here commits: the caller owns the transaction.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.email import EmailOutbox, EmailToken, priority_for
from app.models.enums import EmailKind, EmailStatus

# counted from create_token, which the send job calls at send time (spec, email_tokens)
TOKEN_TTL = {
    EmailKind.VERIFY_EMAIL: timedelta(days=7),
    EmailKind.RESET_PASSWORD: timedelta(minutes=60),
    EmailKind.SET_PASSWORD: timedelta(days=7),
    EmailKind.CHANGE_EMAIL: timedelta(days=7),
}

_INVALID_LINK = "Verification or reset link is invalid, used or expired"


class EmailService:
    @staticmethod
    def queue_email(db: Session, user: User, kind: EmailKind) -> EmailOutbox:
        """Add one pending row for the user's address, at the priority of `kind`.

        A reset while one is already pending adds nothing and returns the pending
        row (spec: a second reset request adds no message), so the route answers
        the same way. Does not create a token. Before committing, the caller can
        point `to_email` at another address or set `params`; params must not hold
        a token.
        """
        kind = EmailKind(kind)
        values = {
            "user_id": user.id,
            "to_email": user.email.strip().lower(),
            "template": kind,
            "priority": priority_for(kind),
            "status": EmailStatus.PENDING,
            "attempts": 0,
            "next_attempt_at": datetime.now(UTC),
        }
        pending_reset = (EmailOutbox.template == EmailKind.RESET_PASSWORD) & (
            EmailOutbox.status == EmailStatus.PENDING
        )
        # two tries: the pending reset we collided with may be sent before we read it
        for _ in range(2):
            row = db.scalars(
                insert(EmailOutbox)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[EmailOutbox.user_id], index_where=pending_reset
                )
                .returning(EmailOutbox),
                execution_options={"populate_existing": True},
            ).one_or_none()
            if row is None:
                row = db.scalars(
                    select(EmailOutbox).where(EmailOutbox.user_id == user.id, pending_reset)
                ).one_or_none()
            if row is not None:
                return row
        raise RuntimeError("could not queue the reset email")  # pragma: no cover (needs two races)

    @staticmethod
    def count_recent(db: Session, email: str, kind: EmailKind, window: timedelta) -> int:
        """How many outbox rows of this kind were queued for the address inside `window`.

        Forgot-password and resend-verification will use this for the 3-per-hour
        limit. A mail that already went out still counts.
        """
        since = datetime.now(UTC) - window
        count = db.scalar(
            select(func.count())
            .select_from(EmailOutbox)
            .where(
                EmailOutbox.to_email == email.lower(),
                EmailOutbox.template == EmailKind(kind),
                EmailOutbox.created_at >= since,
            )
        )
        return int(count or 0)

    @staticmethod
    def generate_token() -> str:
        """A new random link token. Only hash_token() of it is stored."""
        return secrets.token_urlsafe(32)

    @staticmethod
    def hash_token(raw: str) -> str:
        """SHA-256 hex of the raw token. This is what email_tokens.token_hash stores."""
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def create_token(
        db: Session, user: User, kind: EmailKind, *, new_email: str | None = None
    ) -> str:
        """Store a hashed token and return the raw token once.

        Reset lasts 60 minutes. Verify, set-password and change-email last 7 days.
        change_email needs new_email; the other kinds must not pass one.
        """
        kind = EmailKind(kind)
        if kind not in TOKEN_TTL:
            raise ValueError(f"{kind} is not a link token")
        stored_email = _new_email_for(kind, new_email)
        raw = EmailService.generate_token()
        db.add(
            EmailToken(
                user_id=user.id,
                purpose=kind,
                new_email=stored_email,
                token_hash=EmailService.hash_token(raw),
                expires_at=datetime.now(UTC) + TOKEN_TTL[kind],
            )
        )
        db.flush()
        return raw

    @staticmethod
    def consume_token(db: Session, raw: str, kind: EmailKind) -> EmailToken:
        """Mark the token used and return its row.

        Unknown, expired, already used, or a different kind raises AppError
        INVALID_TOKEN (400). A failed check does not mark the row used.
        """
        token = db.scalars(
            update(EmailToken)
            .where(
                EmailToken.token_hash == EmailService.hash_token(raw),
                EmailToken.purpose == EmailKind(kind),
                EmailToken.used_at.is_(None),
                EmailToken.expires_at > func.now(),
            )
            .values(used_at=func.now())
            .returning(EmailToken)
        ).one_or_none()
        if token is None:
            raise AppError(ErrorCode.INVALID_TOKEN, _INVALID_LINK)
        return token

    @staticmethod
    def invalidate_user_tokens(db: Session, user_id: UUID, kind: EmailKind) -> None:
        """Mark this user's unused tokens of `kind` as used, so older links stop working."""
        kind = EmailKind(kind)
        if kind not in TOKEN_TTL:
            raise ValueError(f"{kind} is not a link token")
        db.execute(
            update(EmailToken)
            .where(
                EmailToken.user_id == user_id,
                EmailToken.purpose == EmailKind(kind),
                EmailToken.used_at.is_(None),
            )
            .values(used_at=func.now())
        )

    @staticmethod
    def revoke_all_sessions(user_id: UUID) -> None:
        """No-op until refresh tokens exist.

        TODO: A2 (#69) — password reset should revoke every refresh token for this user.
        refresh_tokens is not in the schema yet, so this does nothing.
        """


def _new_email_for(kind: EmailKind, new_email: str | None) -> str | None:
    if kind == EmailKind.CHANGE_EMAIL:
        if new_email is None or not new_email.strip():
            raise ValueError("change_email needs a new_email")
        # format is checked by the route (Pydantic EmailStr); here only normalise
        return new_email.strip().lower()
    if new_email is not None:
        raise ValueError("new_email is only for change_email")
    return None
