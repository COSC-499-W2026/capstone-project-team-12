"""Staff (teacher) accounts created by an admin through the admin API.

The admin gives the address, the username and whether the teacher has content rights; the
teacher then chooses a password from the emailed set-password link (valid 3 days), so the
account has no password until then.

Nothing here commits: the route commits the account, its email and its audit entry together,
and a refused request rolls back with nothing written.
"""

from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts.register import email_exists, username_exists
from app.db import violated_constraint
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.enums import AuditAction, EmailKind, UserRole
from app.services import audit
from app.services.email import EmailService

EMAIL_TAKEN_MESSAGE = "An account with this email address already exists."
USERNAME_TAKEN_MESSAGE = "This username is already in use."


def create_teacher(
    admin_id: UUID, email: str, username: str, can_manage_content: bool, db: Session
) -> User:
    """Add an active staff account with no password, queue its set-password email and record
    create_staff in the audit log with the new account as the target.

    The route checks the format of `email` and `username` first; the address is stored
    lowercase. 409 EMAIL_TAKEN if any account has the address (deactivated ones too), 409
    USERNAME_TAKEN if the username is in use in any case, including when another request takes
    either one between the check and the insert.
    """
    email = email.strip().lower()
    # the address first: it is what the admin page shows the conflict against
    if email_exists(email, db):
        raise AppError(ErrorCode.EMAIL_TAKEN, EMAIL_TAKEN_MESSAGE)
    if username_exists(username, db):
        raise AppError(ErrorCode.USERNAME_TAKEN, USERNAME_TAKEN_MESSAGE)
    try:
        user = db.scalars(
            insert(User)
            .values(
                email=email,
                username=username,
                password_hash=None,  # set from the emailed link
                role=UserRole.STAFF,
                can_manage_content=can_manage_content,
            )
            .returning(User)
        ).one()
    except IntegrityError as err:
        # another request took the address or username after the checks above
        constraint = violated_constraint(err)
        if constraint == "uq_users_email":
            raise AppError(ErrorCode.EMAIL_TAKEN, EMAIL_TAKEN_MESSAGE) from err
        if constraint == "uq_users_username_lower":
            raise AppError(ErrorCode.USERNAME_TAKEN, USERNAME_TAKEN_MESSAGE) from err
        raise  # any other constraint is a real error
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
