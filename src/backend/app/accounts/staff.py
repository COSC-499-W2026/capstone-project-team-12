"""Teaching assistant invitations, owned by the accounts component.

The class owner invites assistants by email. Classes and enrolments belong to engagement, so
they are read only through its interface. Every change for one invitee locks that user first,
so two of them never interleave. The caller owns the transaction; nothing here commits.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.interface import get_class_name, is_actively_enrolled, is_class_archived
from app.errors import AppError, ErrorCode
from app.models.accounts import INVITATION_TTL, ClassStaff, ClassStaffInvitation, User
from app.models.enums import EmailKind, InvitationStatus, UserRole
from app.services.email import EmailService
from app.services.locks import lock_user


def invite_assistant(
    class_id: UUID,
    inviter: User,
    email: str,
    db: Session,
) -> ClassStaffInvitation:
    """Invite the staff or student account that uses `email` to assist the class.

    Queues the invitation email. Inviting the same account again while its invitation is
    still open returns that invitation and queues no second email.

    The caller checks that the inviter owns the class and owns the transaction; this
    function never commits.

    Raises:
        NOT_FOUND: Class doesn't exist.
        CLASS_ARCHIVED: Class is archived.
        USER_NOT_FOUND: No staff or student account uses the email.
        FORBIDDEN_ROLE: The email belongs to an admin account.
        VALIDATION_ERROR: The account already owns or assists the class.
        TA_SAME_CLASS_CONFLICT: The account is enrolled in the class as a student.
    """
    class_name = _require_open_class(class_id, db)

    invitee = db.scalar(select(User).where(User.email == email.strip().lower()))
    if invitee is None:
        raise AppError(ErrorCode.USER_NOT_FOUND, "No staff or student account uses this email")

    if invitee.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admin accounts can't assist a class")

    lock_user(invitee.id, db)

    if _is_staff(invitee.id, class_id, db):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "This account is already staff of the class",
            details=[
                {
                    "in": "body",
                    "field": "email",
                    "message": "This account already owns or assists the class",
                }
            ],
        )

    if is_actively_enrolled(invitee.id, class_id, db):
        raise AppError(ErrorCode.TA_SAME_CLASS_CONFLICT)

    now = datetime.now(UTC)
    open_invitation = _open_invitation(class_id, invitee.id, now, db)
    if open_invitation is not None:
        return open_invitation

    invitation = ClassStaffInvitation(
        class_id=class_id,
        invitee_id=invitee.id,
        email=invitee.email,
        invited_by=inviter.id,
        # from now: the send-emails job sends the email within minutes of it being queued
        expires_at=now + INVITATION_TTL,
    )
    db.add(invitation)
    db.flush()

    EmailService.queue_email(
        invitee, EmailKind.TA_INVITATION, db, params={"class_name": class_name}
    )
    return invitation


def _require_open_class(class_id: UUID, db: Session) -> str:
    """Return the class's name, unless it is missing or archived."""
    name = get_class_name(class_id, db)
    if name is None:
        raise AppError(ErrorCode.NOT_FOUND)

    if is_class_archived(class_id, db):
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    return name


def _is_staff(user_id: UUID, class_id: UUID, db: Session) -> bool:
    statement = select(ClassStaff.role).where(
        ClassStaff.class_id == class_id,
        ClassStaff.user_id == user_id,
    )
    return db.scalar(statement) is not None


def _open_invitation(
    class_id: UUID, invitee_id: UUID, now: datetime, db: Session
) -> ClassStaffInvitation | None:
    """The account's open invitation to the class. A pending one that has expired is marked
    expired instead, so a new invitation can take its place."""
    pending = db.scalar(
        select(ClassStaffInvitation)
        .where(
            ClassStaffInvitation.class_id == class_id,
            ClassStaffInvitation.invitee_id == invitee_id,
            ClassStaffInvitation.status == InvitationStatus.PENDING,
        )
        .execution_options(populate_existing=True)
    )
    if pending is None or pending.expires_at > now:
        return pending

    pending.status = InvitationStatus.EXPIRED
    db.flush()  # before the new invitation takes the one-pending slot
    return None
