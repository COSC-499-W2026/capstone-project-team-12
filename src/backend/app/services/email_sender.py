"""The send-emails job: deliver due outbox rows, priority 1 first, one transaction per mail.

The link token is created in the same transaction as the send, so a reset link's 60 minutes
start when that mail leaves, and a failed send rolls the new token back (the user's older link
keeps working). The job is an entry point, so unlike EmailService it commits.

Delivery is at least once: if the commit after a successful send fails, the row is still
pending and is sent again later, and the first mail's link won't work since its token was
never saved.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.email import MAX_EMAIL_ATTEMPTS, PENDING_WHERE, TOKEN_KINDS, EmailOutbox
from app.models.enums import EmailKind, EmailStatus
from app.services.email import EmailService

logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # per run; the job runs every minute
# after the provider's sending limit, the whole queue waits this long instead of asking every minute
QUOTA_PAUSE = timedelta(hours=1)

_PENDING = text(PENDING_WHERE)


# mail that only makes sense at the user's current address (change_email goes to the new one,
# the notice to the old one)
_CURRENT_ADDRESS_KINDS = (EmailKind.VERIFY_EMAIL, EmailKind.RESET_PASSWORD, EmailKind.SET_PASSWORD)


class _SkipError(Exception):
    """The address changed or a newer mail of the kind exists; its link would replace theirs."""


class MailQuotaError(Exception):
    """The provider's sending limit is reached. The run stops and no attempt is counted."""


@dataclass(frozen=True)
class OutgoingMail:
    to_email: str
    kind: EmailKind
    params: Mapping[str, Any]
    # the raw link token, only for kinds with a link; kept out of repr so it never reaches a log
    token: str | None = field(repr=False)


class Mailer(Protocol):
    def send(self, mail: OutgoingMail) -> None:
        """Deliver one mail. Raise MailQuotaError at a sending limit, anything else on failure."""


@dataclass
class SendReport:
    sent: int = 0
    retried: int = 0  # failed, will be tried again later
    failed: int = 0  # failed for the last time
    quota_reached: bool = False


def retry_delay(attempts: int) -> timedelta:
    """Wait 2, 4, 8 then 16 minutes after the 1st to 4th failed attempt."""
    return timedelta(minutes=2**attempts)


def send_due_emails(mailer: Mailer, db: Session, *, limit: int = BATCH_SIZE) -> SendReport:
    """Send up to `limit` due rows and commit after each one."""
    report = SendReport()
    for _ in range(limit):
        row = _lock_next_due(db)
        if row is None:
            break
        try:  # making the token can fail too, and must not leave the row due forever
            # a savepoint: a failure drops the new token but keeps the row locked until recorded
            with db.begin_nested():
                token = _token_for(row, db)
                mailer.send(
                    OutgoingMail(row.to_email, EmailKind(row.template), dict(row.params), token)
                )
        except _SkipError:
            row.status = EmailStatus.FAILED
            db.commit()
            report.failed += 1
            continue
        except MailQuotaError:
            db.rollback()  # unlock the row first, or two runs pausing at once would deadlock
            _pause_queue(db)
            db.commit()
            report.quota_reached = True
            break
        except Exception as error:
            # the class name only: error text can hold the recipient's address (logs keep IDs)
            logger.error("Sending outbox row %s failed: %s", row.id, type(error).__name__)
            _record_failure(row)
            db.commit()
            if row.status == EmailStatus.FAILED:
                report.failed += 1
            else:
                report.retried += 1
            continue
        row.status, row.sent_at = EmailStatus.SENT, func.now()
        db.commit()
        report.sent += 1
    db.rollback()  # ends the read that found nothing due
    return report


def _lock_next_due(db: Session) -> EmailOutbox | None:
    # SKIP LOCKED: a run that overlaps this one sends other rows instead of waiting
    return db.scalars(
        select(EmailOutbox)
        .where(_PENDING, EmailOutbox.next_attempt_at <= func.now())
        .order_by(EmailOutbox.priority, EmailOutbox.next_attempt_at, EmailOutbox.created_at)
        .limit(1)
        .with_for_update(skip_locked=True),
        # the CLI's sessions keep objects after commit; read the row as it is now
        execution_options={"populate_existing": True},
    ).one_or_none()


def _token_for(row: EmailOutbox, db: Session) -> str | None:
    """Make the row's link token, or raise _SkipError if the mail must not go out.

    The user is locked and read once, for both the address check and the token.
    """
    kind = EmailKind(row.template)
    if kind not in TOKEN_KINDS:  # no link and no address check, so no need to lock the user
        return None
    user = db.scalars(
        select(User).where(User.id == row.user_id).with_for_update(key_share=True),
        # the CLI's session keeps objects after commit; read the address as it is now
        execution_options={"populate_existing": True},
    ).one()
    if kind in _CURRENT_ADDRESS_KINDS and user.email != row.to_email:
        raise _SkipError
    if _newer_mail_exists(row, db):
        raise _SkipError  # e.g. a retried change_email to a typo would cancel the right link
    new_email = row.to_email if kind == EmailKind.CHANGE_EMAIL else None  # sent to the new one
    return EmailService.create_token(user, kind, db, new_email=new_email)


def _newer_mail_exists(row: EmailOutbox, db: Session) -> bool:
    newer = select(EmailOutbox.id).where(
        EmailOutbox.user_id == row.user_id,
        EmailOutbox.template == row.template,
        EmailOutbox.status != EmailStatus.FAILED,
        EmailOutbox.created_at > row.created_at,
    )
    return db.scalar(newer.limit(1)) is not None


def _pause_queue(db: Session) -> None:
    """Move every pending row that is due within QUOTA_PAUSE to the end of the pause.

    Attempts are not counted: the mail itself didn't fail.
    """
    db.execute(
        update(EmailOutbox)
        .where(_PENDING, EmailOutbox.next_attempt_at < func.now() + QUOTA_PAUSE)
        .values(next_attempt_at=func.now() + QUOTA_PAUSE)
    )


def _record_failure(row: EmailOutbox) -> None:
    """Count the attempt; after the last one the row is failed, otherwise it waits to retry."""
    row.attempts += 1
    if row.attempts >= MAX_EMAIL_ATTEMPTS:
        row.status = EmailStatus.FAILED
    else:
        row.next_attempt_at = func.now() + retry_delay(row.attempts)
