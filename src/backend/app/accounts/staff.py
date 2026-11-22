"""Staff (teacher) accounts as admins manage them through the admin API: creating, listing,
deactivating and reactivating.

To create one, the admin gives the address, the username and whether the teacher has content
rights; the teacher then chooses a password from the emailed set-password link (valid 3 days),
so the account has no password until then. The list holds both staff and admin accounts.
Only staff accounts can be deactivated or reactivated here; admin and student accounts are
refused.

Nothing here commits: the route commits the account, its email and its audit entry together,
and a refused request rolls back with nothing written. Reading the list writes nothing.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.accounts.register import insert_user
from app.accounts.tokens import revoke_refresh_tokens
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.enums import AuditAction, EmailKind, UserRole
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


def deactivate_staff(
    admin_id: UUID, user_id: UUID, db: Session, *, ip_address: str | None = None
) -> User:
    """Deactivate a staff account and revoke all its refresh tokens.

    The account can't sign in or refresh until it is reactivated, and its access tokens are
    refused from the next request on (get_current_user checks is_active), so it is signed out
    everywhere. Content rights and class ownership are kept for when it is reactivated.
    Deactivating an account that is already deactivated still revokes any token it has left.

    404 USER_NOT_FOUND for an unknown id; 403 FORBIDDEN_ROLE for an admin or student account.
    Records user_deactivated, as SUCCESS, DENIED for the 403 or FAILURE for the 404 (see
    _set_staff_active).
    """
    user = _set_staff_active(admin_id, user_id, False, db, ip_address=ip_address)
    revoke_refresh_tokens(user.id, db)
    return user


def reactivate_staff(
    admin_id: UUID, user_id: UUID, db: Session, *, ip_address: str | None = None
) -> User:
    """Let a deactivated staff account sign in again.

    The refresh tokens revoked when it was deactivated stay revoked: the teacher signs in
    again. Reactivating an active account changes nothing. 404 USER_NOT_FOUND and 403
    FORBIDDEN_ROLE as for deactivate_staff; records user_reactivated the same way.
    """
    return _set_staff_active(admin_id, user_id, True, db, ip_address=ip_address)


def _set_staff_active(
    admin_id: UUID, user_id: UUID, active: bool, db: Session, *, ip_address: str | None
) -> User:
    """Set is_active on a staff account, holding its row lock until the route commits.

    The lock is FOR NO KEY UPDATE, the one a password reset takes. A login or a refresh holds
    FOR SHARE on the user while it checks is_active and adds a refresh token, so each waits
    for the other: a sign-in that started first has its token revoked by the deactivation,
    and one that comes after sees the account is deactivated.

    The entry names the account as the target, with its address, username and role at the
    time, and whether it was active before (a repeat changes nothing). A refused change is
    recorded with the error code: DENIED for the role, FAILURE for an unknown id.
    """
    action = AuditAction.REACTIVATE_USER if active else AuditAction.DEACTIVATE_USER
    user = lock_user(user_id, db)
    if user is None:
        error = AppError(ErrorCode.USER_NOT_FOUND, USER_NOT_FOUND_MESSAGE)
        audit.record(
            admin_id,
            action,
            db,
            result=AuditResult.FAILURE,
            target_id=user_id,
            details={"error": error.code.value},
            ip_address=ip_address,
        )
        raise error
    details: dict[str, Any] = {
        "email": user.email,
        "username": user.username,
        "role": user.role,
        "was_active": user.is_active,
    }
    if user.role != UserRole.STAFF:
        # admin accounts are managed with the command-line tool; students are not staff
        error = AppError(ErrorCode.FORBIDDEN_ROLE, ONLY_STAFF_MESSAGE)
        audit.record(
            admin_id,
            action,
            db,
            result=AuditResult.DENIED,
            target_id=user.id,
            details=details | {"error": error.code.value},
            ip_address=ip_address,
        )
        raise error
    user.is_active = active
    db.flush()
    audit.record(admin_id, action, db, target_id=user.id, details=details, ip_address=ip_address)
    return user
