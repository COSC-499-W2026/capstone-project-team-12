"""Staff (teacher) accounts as admins manage them through the admin API: creating and listing.

To create one, the admin gives the address, the username and whether the teacher has content
rights; the teacher then chooses a password from the emailed set-password link (valid 3 days),
so the account has no password until then. The list holds both staff and admin accounts.

Nothing here commits: the route commits the account, its email and its audit entry together,
and a refused request rolls back with nothing written. Reading the list writes nothing.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.accounts.register import insert_user
from app.models.accounts import User
from app.models.enums import AuditAction, EmailKind, UserRole
from app.services import audit
from app.services.email import EmailService


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
