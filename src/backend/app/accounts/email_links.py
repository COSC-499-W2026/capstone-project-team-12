"""Email verification and the forgot-password request (A3).

The answers never say whether an address has an account: a request that can't be served does
nothing and looks the same to the caller. That is an unknown or inactive address, a resend for
an address that is already verified, or a reset for one that isn't (reset links only go to
verified addresses). The 3-per-hour
limit is applied the same way, by quietly not queueing, since a 429 only for addresses that
exist would give that away. The verification email sent at sign-up counts toward it too.
Nothing here commits; the route does.
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
    """Queue a reset email for an active, verified account.

    A pending one is not doubled (queue_email). An unverified user has to verify first.
    """
    user = _active_user(email, db)
    if user is not None and user.email_verified_at is not None:
        _queue_within_limit(user, EmailKind.RESET_PASSWORD, db)


def _active_user(email: str, db: Session) -> User | None:
    """The active account for the address, locked until the route commits.

    The lock makes requests for one address run one at a time, so several sent at once
    can't all count fewer than 3 and each queue a mail. It is FOR NO KEY UPDATE, the lock
    create_token takes, so the outbox insert (which only needs FOR KEY SHARE) isn't blocked.
    """
    address = email.strip().lower()  # stored lowercase
    query = (
        select(User)
        .where(User.email == address)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)  # re-read after waiting for the lock
    )
    user = db.scalars(query).one_or_none()
    return user if user is not None and user.is_active else None


def _queue_within_limit(user: User, kind: EmailKind, db: Session) -> None:
    if EmailService.count_recent(user.email, kind, _HOUR, db) < REQUESTS_PER_HOUR:
        EmailService.queue_email(user, kind, db)
