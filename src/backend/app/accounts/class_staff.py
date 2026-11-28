"""Class staff: the owner and teaching assistants of each class, and the invitations owners send
to make staff and student accounts assistants.

Classes and enrolments belong to engagement, so they are read and locked only through its
interface. A change for one invitee locks that user, then the class, as joining and archiving
do, so it can't interleave with another change for the same invitee or with archiving the class.

Nothing here commits: the route commits each change, and a refused request rolls back with
nothing written.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.interface import is_actively_enrolled, lock_open_class
from app.errors import AppError, ErrorCode
from app.models.accounts import INVITATION_TTL, ClassStaff, ClassStaffInvitation, User
from app.models.enums import EmailKind, InvitationStatus, UserRole
from app.services.email import EmailService
from app.services.locks import lock_user


def class_role(user_id: UUID, class_id: UUID, db: Session) -> str | None:
    """The user's staff role in the class, or None if they aren't staff there."""
    return db.scalar(
        select(ClassStaff.role).where(
            ClassStaff.user_id == user_id, ClassStaff.class_id == class_id
        )
    )


def invite_assistant(
    class_id: UUID,
    inviter_id: UUID,
    email: str,
    db: Session,
) -> ClassStaffInvitation:
    """Invite the staff or student account that uses `email` to assist the class.

    Queues the invitation email. Inviting the same account again while its invitation is
    pending returns that invitation and queues no second email.

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
    invitee = db.scalar(select(User).where(User.email == email.strip().lower()))
    if invitee is None:
        raise AppError(ErrorCode.USER_NOT_FOUND, "No staff or student account uses this email")

    if invitee.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admin accounts can't assist a class")

    lock_user(invitee.id, db)
    class_name = lock_open_class(class_id, db)

    if class_role(invitee.id, class_id, db) is not None:
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

    open_invitation = _open_invitation(class_id, invitee.id, db)
    if open_invitation is not None:
        return open_invitation

    invitation = ClassStaffInvitation(
        class_id=class_id,
        invitee_id=invitee.id,
        email=invitee.email,
        invited_by=inviter_id,
        # from now: the send-emails job sends the email within minutes of it being queued
        expires_at=datetime.now(UTC) + INVITATION_TTL,
    )
    db.add(invitation)
    db.flush()

    EmailService.queue_email(
        invitee, EmailKind.TA_INVITATION, db, params={"class_name": class_name}
    )
    return invitation


def _open_invitation(class_id: UUID, invitee_id: UUID, db: Session) -> ClassStaffInvitation | None:
    """The account's pending invitation to the class, if any."""
    return db.scalar(
        select(ClassStaffInvitation)
        .where(
            ClassStaffInvitation.class_id == class_id,
            ClassStaffInvitation.invitee_id == invitee_id,
            ClassStaffInvitation.status == InvitationStatus.PENDING,
        )
        .execution_options(populate_existing=True)
    )
