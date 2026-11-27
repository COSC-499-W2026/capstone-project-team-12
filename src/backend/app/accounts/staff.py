"""Staff (teacher) accounts as admins manage them through the admin API (creating, listing,
deactivating and reactivating), and the teaching assistants class owners invite.

To create one, the admin gives the address, the username and whether the teacher has content
rights; the teacher then chooses a password from the emailed set-password link (valid 3 days),
so the account has no password until then. The list holds both staff and admin accounts. Only
staff accounts can be deactivated or reactivated; admin and student accounts are refused.

A class owner invites an assistant by email. Classes and enrolments belong to engagement, so
they are read only through its interface. A change for one invitee locks that user, then the
class, as joining and archiving do, so it can't interleave with another change for the same
invitee or with archiving the class.

Nothing here commits: the route commits each change and any audit entry together, and a
refused request rolls back with nothing written. Reading the list writes nothing.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.accounts.register import insert_user
from app.accounts.tokens import revoke_refresh_tokens
from app.engagement.interface import (
    get_class_name,
    is_actively_enrolled,
    is_class_archived,
    lock_class,
)
from app.errors import AppError, ErrorCode
from app.models.accounts import INVITATION_TTL, ClassStaff, ClassStaffInvitation, User
from app.models.enums import AuditAction, EmailKind, InvitationStatus, UserRole
from app.services import audit
from app.services.email import EmailService
from app.services.locks import lock_user

USER_NOT_FOUND_MESSAGE = "No account has this id."
ONLY_STAFF_MESSAGE = "Only staff accounts can be deactivated or reactivated."


def create_teacher(
    admin_id: UUID, email: str, username: str, can_manage_content: bool, db: Session
) -> User:
    """Add an active staff account with no password, queue its set-password email and record
    create_staff in the audit log with the new account as the target.

    The address is stored lowercase. 422 VALIDATION_ERROR if the address or the username is
    badly formed; 409 EMAIL_TAKEN or USERNAME_TAKEN if another account has either one.
    """
    # no password: the teacher sets it from the emailed link
    user = insert_user(
        email, username, None, role=UserRole.STAFF, can_manage_content=can_manage_content, db=db
    )
    EmailService.queue_email(user, EmailKind.SET_PASSWORD, db)  # the token is made when sent
    # the account itself holds the address and username, so the entry only adds the rights
    # it started with
    audit.record(
        admin_id,
        AuditAction.CREATE_STAFF,
        db,
        target_user_id=user.id,
        details={"can_manage_content": can_manage_content},
    )
    return user


@dataclass(frozen=True)
class StaffPage:
    items: tuple[User, ...]
    total: int  # matching accounts on every page


LISTED_ROLES = (UserRole.STAFF, UserRole.ADMIN)


def list_staff_accounts(
    offset: int, limit: int, db: Session, *, email: str | None = None
) -> StaffPage:
    """Up to `limit` staff and admin accounts after the first `offset`, deactivated ones too,
    so they can be reactivated. Students are never listed.

    `email` keeps only the addresses that contain it, in any case; `%` and `_` in it match
    themselves. Ordered by username in any case (the order of uq_users_username_lower), then
    by id, so pages don't overlap. An offset past the end gives no items.
    """
    matching: list[ColumnElement[bool]] = [User.role.in_(LISTED_ROLES)]
    if email is not None:
        # addresses are stored lowercase
        matching.append(User.email.contains(email.strip().lower(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(User).where(*matching)) or 0
    if offset >= total:
        return StaffPage(items=(), total=total)
    users = db.scalars(
        select(User)
        .where(*matching)
        .order_by(func.lower(User.username), User.id)
        .offset(offset)
        .limit(limit)
    )
    return StaffPage(items=tuple(users), total=total)


def set_staff_active(admin_id: UUID, user_id: UUID, active: bool, db: Session) -> User:
    """Deactivate (active=False) or reactivate a staff account and record deactivate or
    reactivate in the audit log with the account as the target. Repeating either is allowed
    and recorded again.

    Deactivating also revokes every refresh token, so the account is signed out everywhere: it
    can't sign in or refresh, and get_current_user refuses its access tokens. The tokens stay
    revoked when it is reactivated. Content rights and classes are kept.

    The user is locked FOR NO KEY UPDATE first, as a password reset does. A login or refresh
    holds FOR SHARE on it while adding a token, so one in progress commits first and its token
    is revoked too; a later one sees the account is deactivated.

    404 USER_NOT_FOUND for an unknown id, 403 FORBIDDEN_ROLE for an admin or student account;
    either records nothing, since nothing changed.
    """
    user = lock_user(user_id, db)
    if user is None:
        raise AppError(ErrorCode.USER_NOT_FOUND, USER_NOT_FOUND_MESSAGE)
    if user.role != UserRole.STAFF:
        # admin accounts are managed with the command-line tool; students are not staff
        raise AppError(ErrorCode.FORBIDDEN_ROLE, ONLY_STAFF_MESSAGE)
    was_active = user.is_active
    user.is_active = active
    db.flush()
    if not active:
        revoke_refresh_tokens(user.id, db)
    # only whether it changed: an address or username here would outlive the account being
    # anonymised
    audit.record(
        admin_id,
        AuditAction.REACTIVATE if active else AuditAction.DEACTIVATE,
        db,
        target_user_id=user.id,
        details={"was_active": was_active},
    )
    return user


def invite_assistant(
    class_id: UUID,
    inviter: User,
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
    _require_open_class(class_id, db)

    invitee = db.scalar(select(User).where(User.email == email.strip().lower()))
    if invitee is None:
        raise AppError(ErrorCode.USER_NOT_FOUND, "No staff or student account uses this email")

    if invitee.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admin accounts can't assist a class")

    lock_user(invitee.id, db)
    # checked again under the lock, which reloads the class: an archive may have committed since
    lock_class(class_id, db)
    class_name = _require_open_class(class_id, db)

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
    open_invitation = _open_invitation(class_id, invitee.id, db)
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
