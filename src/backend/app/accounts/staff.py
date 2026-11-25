"""Staff (teacher) accounts created by an admin through the admin API.

The admin gives the address, the username and whether the teacher has content rights; the
teacher then chooses a password from the emailed set-password link (valid 3 days), so the
account has no password until then.

Nothing here commits: the route commits the account, its email and its audit entry together,
and a refused request rolls back with nothing written.
"""

from uuid import UUID

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
