"""The send-emails job: deliver due outbox rows, priority 1 first, one transaction per mail.

The link token is created in the same transaction as the send, so a reset link's 60 minutes
start when that mail leaves, and a failed send rolls the new token back (the user's older link
keeps working). The job is an entry point, so unlike EmailService it commits.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.email import MAX_EMAIL_ATTEMPTS, PENDING_WHERE, TOKEN_KINDS, EmailOutbox
from app.models.enums import EmailKind, EmailStatus
from app.services.email import EmailService

logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # per run; the job runs every minute

_PENDING = text(PENDING_WHERE)


class MailQuotaError(Exception):
    """The provider's sending limit is reached. The run stops and no attempt is counted."""


@dataclass(frozen=True)
class OutgoingMail:
    to_email: str
    kind: EmailKind
    params: Mapping[str, Any]
    token: str | None  # the raw link token, only for kinds with a link


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


def send_due_emails(db: Session, mailer: Mailer, *, limit: int = BATCH_SIZE) -> SendReport:
    """Send up to `limit` due rows and commit after each one."""
    report = SendReport()
    for _ in range(limit):
        row = _lock_next_due(db)
        if row is None:
            break
        row_id = row.id
        mail = OutgoingMail(
            row.to_email, EmailKind(row.template), dict(row.params), _token_for(row, db)
        )
        try:
            mailer.send(mail)
        except MailQuotaError:
            db.rollback()
            report.quota_reached = True
            break
        except Exception:
            logger.exception("Sending outbox row %s failed", row_id)
            db.rollback()  # drops the new token too
            if _record_failure(row_id, db) == EmailStatus.FAILED:
                report.failed += 1
            else:
                report.retried += 1
            db.commit()
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
    kind = EmailKind(row.template)
    if kind not in TOKEN_KINDS:
        return None
    user = db.get_one(User, row.user_id)
    new_email = row.to_email if kind == EmailKind.CHANGE_EMAIL else None  # sent to the new one
    return EmailService.create_token(user, kind, db, new_email=new_email)


def _record_failure(row_id: UUID, db: Session) -> str | None:
    """Count the attempt; after the last one the row is failed, otherwise it waits to retry."""
    row = db.scalars(
        select(EmailOutbox).where(EmailOutbox.id == row_id, _PENDING).with_for_update()
    ).one_or_none()
    if row is None:  # pragma: no cover (changed by someone else since the rollback)
        return None
    row.attempts += 1
    if row.attempts >= MAX_EMAIL_ATTEMPTS:
        row.status = EmailStatus.FAILED
    else:
        row.next_attempt_at = func.now() + retry_delay(row.attempts)
    return row.status
