"""Teaching assistant invitations, owned by the accounts component.

The class owner invites assistants by email and removes them; the invited user lists their
invitations and accepts or declines them. Classes and enrolments belong to engagement, so they
are read only through its interface. Every change for one invitee locks that user first, so
two of them never interleave. The caller owns the transaction; nothing here commits.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.engagement.interface import get_class_name, is_actively_enrolled, is_class_archived
from app.errors import AppError, ErrorCode
from app.models.accounts import INVITATION_TTL, ClassStaff, ClassStaffInvitation, User
from app.models.enums import ClassStaffRole, EmailKind, InvitationStatus, UserRole
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
        # the send-emails job restarts the 7 days when it sends the email
        expires_at=now + INVITATION_TTL,
    )
    db.add(invitation)
    db.flush()

    EmailService.queue_email(
        invitee,
        EmailKind.TA_INVITATION,
        db,
        params={"class_name": class_name, "invitation_id": str(invitation.id)},
    )
    return invitation


def remove_assistant(class_id: UUID, email: str, db: Session) -> bool:
    """Remove the assistant who uses `email` from the class, or cancel their open invitation.

    Return whether anything changed. Repeating the request, or naming the owner or an account
    with no part in the class, changes nothing: the owner is only ever transferred, never
    removed.

    The caller checks that the user owns the class and owns the transaction; this function
    never commits.

    Raises:
        NOT_FOUND: Class doesn't exist.
        CLASS_ARCHIVED: Class is archived.
    """
    _require_open_class(class_id, db)

    user_id = db.scalar(select(User.id).where(User.email == email.strip().lower()))
    if user_id is None:
        return False

    # an accept at the same moment finishes first, so its assistant is removed here too
    lock_user(user_id, db)

    removed = db.scalars(
        delete(ClassStaff)
        .where(
            ClassStaff.class_id == class_id,
            ClassStaff.user_id == user_id,
            ClassStaff.role == ClassStaffRole.ASSISTANT,
        )
        .returning(ClassStaff.user_id)
    ).all()
    cancelled = db.scalars(
        update(ClassStaffInvitation)
        .where(
            ClassStaffInvitation.class_id == class_id,
            ClassStaffInvitation.invitee_id == user_id,
            ClassStaffInvitation.status == InvitationStatus.PENDING,
        )
        .values(status=InvitationStatus.CANCELLED)
        .returning(ClassStaffInvitation.id)
    ).all()
    return bool(removed or cancelled)


def accept_invitation(user_id: UUID, invitation_id: UUID, db: Session) -> UUID:
    """Make the user an assistant of the class they were invited to and return its id.

    TA_SAME_CLASS_CONFLICT is checked again here, since the user may have joined the class as
    a student after being invited; the invitation then stays open. Accepting an invitation
    that was already accepted changes nothing, so it can't re-add an assistant the owner has
    since removed.

    Locks the user first, as joining a class does, so a join and an accept at the same moment
    can't both pass their check. The caller checks that the user is signed in and owns the
    transaction; this function never commits.

    Raises:
        NOT_FOUND: No such invitation for this user, or it was declined, cancelled or has
            expired.
        CLASS_ARCHIVED: Class is archived.
        TA_SAME_CLASS_CONFLICT: The user is enrolled in the class as a student.
    """
    lock_user(user_id, db)
    invitation = _users_invitation(user_id, invitation_id, db)

    class_id = invitation.class_id
    _require_open_class(class_id, db)

    if invitation.status == InvitationStatus.ACCEPTED:
        return class_id

    now = datetime.now(UTC)
    if not _is_open(invitation, now):
        raise AppError(ErrorCode.NOT_FOUND, _NOT_FOUND)

    if is_actively_enrolled(user_id, class_id, db):
        raise AppError(ErrorCode.TA_SAME_CLASS_CONFLICT)

    invitation.status = InvitationStatus.ACCEPTED
    invitation.responded_at = now
    # an admin may have transferred the class to the user meanwhile; they stay its owner
    db.execute(
        insert(ClassStaff)
        .values(class_id=class_id, user_id=user_id, role=ClassStaffRole.ASSISTANT)
        .on_conflict_do_nothing()
    )
    db.flush()
    return class_id


def decline_invitation(user_id: UUID, invitation_id: UUID, db: Session) -> UUID:
    """Decline the user's invitation to assist a class and return the class's id.

    Declining an invitation that was already declined changes nothing. Declining doesn't
    change the class, so an invitation to an archived class can be declined too.

    Locks the user first, as accepting does, so an accept and a decline of the same invitation
    can't both pass. The caller checks that the user is signed in and owns the transaction;
    this function never commits.

    Raises:
        NOT_FOUND: No such invitation for this user, or it was accepted, cancelled or has
            expired.
    """
    lock_user(user_id, db)
    invitation = _users_invitation(user_id, invitation_id, db)

    if invitation.status == InvitationStatus.DECLINED:
        return invitation.class_id

    now = datetime.now(UTC)
    if not _is_open(invitation, now):
        raise AppError(ErrorCode.NOT_FOUND, _NOT_FOUND)

    invitation.status = InvitationStatus.DECLINED
    invitation.responded_at = now
    db.flush()
    return invitation.class_id


@dataclass(frozen=True)
class PendingInvitation:
    """An invitation to assist a class that the user can still answer."""

    id: UUID
    class_id: UUID
    class_name: str
    expires_at: datetime


def list_invitations(user_id: UUID, db: Session) -> list[PendingInvitation]:
    """Return the invitations the user can still answer, newest first.

    Expired invitations and invitations to archived classes are left out, since they can no
    longer be accepted. This operation is read-only and does not commit or flush.
    """
    invitations = db.scalars(
        select(ClassStaffInvitation)
        .where(
            ClassStaffInvitation.invitee_id == user_id,
            ClassStaffInvitation.status == InvitationStatus.PENDING,
            ClassStaffInvitation.expires_at > datetime.now(UTC),
        )
        .order_by(ClassStaffInvitation.created_at.desc(), ClassStaffInvitation.id)
    ).all()

    pending = []
    for invitation in invitations:
        class_name = get_class_name(invitation.class_id, db)
        if class_name is not None and not is_class_archived(invitation.class_id, db):
            pending.append(
                PendingInvitation(
                    invitation.id, invitation.class_id, class_name, invitation.expires_at
                )
            )
    return pending


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


_NOT_FOUND = "Invitation not found"


def _users_invitation(user_id: UUID, invitation_id: UUID, db: Session) -> ClassStaffInvitation:
    """The user's invitation, read again under the user's lock so an owner's cancel that
    committed meanwhile is seen."""
    invitation = db.scalar(
        select(ClassStaffInvitation)
        .where(
            ClassStaffInvitation.id == invitation_id,
            ClassStaffInvitation.invitee_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if invitation is None:
        raise AppError(ErrorCode.NOT_FOUND, _NOT_FOUND)
    return invitation


def _is_open(invitation: ClassStaffInvitation, now: datetime) -> bool:
    """Whether the invitee can still answer the invitation."""
    return invitation.status == InvitationStatus.PENDING and invitation.expires_at > now
