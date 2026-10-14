"""Email verification and the forgot-password request (A3).

The answers never say whether an address has an account: a request for an unknown, inactive
or already verified address does nothing and looks the same to the caller. The 3-per-hour
limit is applied the same way, by quietly not queueing, since a 429 only for addresses that
exist would give that away. Nothing here commits; the route does.
"""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.enums import EmailKind
from app.services.email import EmailService

# spec, Rate limits: forgot password and resend verification, 3 per hour per email address
REQUESTS_PER_HOUR = 3
_HOUR = timedelta(hours=1)


def verify_email(token: str, db: Session) -> None:
    """Mark the address verified with a link from the verification email.

    An invalid, used or expired token raises INVALID_TOKEN (400) from consume_token.
    """
    used = EmailService.consume_token(token, EmailKind.VERIFY_EMAIL, db)
    user = db.get_one(User, used.user_id, populate_existing=True)
    if user.email_verified_at is None:
        user.email_verified_at = func.now()
    db.flush()


def request_verification_email(email: str, db: Session) -> None:
    """Queue a new verification email for an unverified, active account."""
    user = _active_user(email, db)
    if user is None or user.email_verified_at is not None:
        return
    _queue_within_limit(user, EmailKind.VERIFY_EMAIL, db)


def request_password_reset(email: str, db: Session) -> None:
    """Queue a reset email for an active account. A pending one is not doubled (queue_email)."""
    user = _active_user(email, db)
    if user is not None:
        _queue_within_limit(user, EmailKind.RESET_PASSWORD, db)


def _active_user(email: str, db: Session) -> User | None:
    address = email.strip().lower()  # stored lowercase
    user = db.scalars(select(User).where(User.email == address)).one_or_none()
    return user if user is not None and user.is_active else None


def _queue_within_limit(user: User, kind: EmailKind, db: Session) -> None:
    if EmailService.count_recent(user.email, kind, _HOUR, db) < REQUESTS_PER_HOUR:
        EmailService.queue_email(user, kind, db)
