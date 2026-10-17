"""Email verification, the forgot-password request and the password reset (A3).

A request that can't be served gets the same answer and does nothing: an unknown or inactive
address, a resend for an address that is already verified or still has a verify mail waiting,
or a reset for one that isn't verified (reset links only go to verified addresses). The
3-per-hour limit works the same way, by quietly not queueing, since a 429 only for addresses
that exist would give them away. It counts queued mails, the verification email sent at
sign-up included, not requests. Response times still differ a little between real and
unknown addresses. Nothing here commits; the route does.
"""

from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.accounts.password import hash_password, validate_password_security
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.email import EmailOutbox
from app.models.enums import EmailKind, EmailStatus
from app.services.email import INVALID_LINK, EmailService

# spec, Rate limits: 3 per hour per address; until requests are counted, queued mails are
MAILS_PER_HOUR = 3
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


def reset_password(token: str, new_password: str, db: Session) -> None:
    """Set a new password with a link from the reset email.

    The password is checked first, so a weak one (422) doesn't use the link up. A bad link, or
    one for an account that is deactivated or unverified, is INVALID_TOKEN (400) and stays unused.
    The link is looked at twice: unlocked before the hash, so unknown tokens don't pay for
    bcrypt, and again when it is consumed, which is the check that counts.
    """
    validate_password_security(new_password)
    # a made-up or stale token is refused before the slow hash, so it costs no CPU
    if not EmailService.is_link_live(token, EmailKind.RESET_PASSWORD, db):
        raise AppError(ErrorCode.INVALID_TOKEN, INVALID_LINK)
    password_hash = hash_password(new_password)  # slow (bcrypt), so before the user is locked
    with db.begin_nested():  # a refused link is rolled back, so it isn't used up
        used = EmailService.consume_token(token, EmailKind.RESET_PASSWORD, db)  # locks the user
        user = db.get_one(User, used.user_id, populate_existing=True)
        if not user.is_active or user.email_verified_at is None:
            raise AppError(ErrorCode.INVALID_TOKEN, INVALID_LINK)
    user.password_hash = password_hash
    # a reset mail still waiting would send another link and keep the one-pending slot taken.
    # SKIP LOCKED: the send job locks the row before the user, so waiting here could deadlock;
    # a row it is sending right now just goes out
    waiting = select(EmailOutbox.id).where(
        EmailOutbox.user_id == user.id,
        EmailOutbox.template == EmailKind.RESET_PASSWORD,
        EmailOutbox.status == EmailStatus.PENDING,
    )
    stale = EmailOutbox.id.in_(waiting.with_for_update(skip_locked=True))
    db.execute(update(EmailOutbox).where(stale).values(status=EmailStatus.FAILED))
    # an unused set-password link (3 days) could overwrite the new password
    EmailService.invalidate_user_tokens(user.id, EmailKind.SET_PASSWORD, db)
    # spec: resetting also revokes all refresh tokens. They come with A2 (revoke_refresh_tokens);
    # whichever of A2 and this merges second adds the call here.
    db.flush()


def request_verification_email(email: str, db: Session) -> None:
    """Queue a verification email for an unverified, active account, unless one is waiting."""
    user = _active_user(email, db)
    if user is None or user.email_verified_at is not None:
        return
    pending = select(EmailOutbox.id).where(
        EmailOutbox.user_id == user.id,
        EmailOutbox.template == EmailKind.VERIFY_EMAIL,
        EmailOutbox.status == EmailStatus.PENDING,
        EmailOutbox.to_email == user.email,  # one to an old address will be skipped
    )
    # a pending mail already gets the newest link; the user lock keeps this race-free
    if db.scalar(pending.limit(1)) is None:
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
    if EmailService.count_recent(user.email, kind, _HOUR, db) < MAILS_PER_HOUR:
        EmailService.queue_email(user, kind, db)
