"""Staff (teacher) accounts created by an admin through the admin API (A27).

The admin gives the address, the username and whether the teacher has content rights; the
teacher then chooses a password from the emailed set-password link (valid 3 days), so the
account has no password until then.

Every attempt is written to the audit log, a refused one as a FAILURE that names the error
code. Nothing here commits: on success the route commits the account, its email and the entry
together; on an AppError only the FAILURE entry is left to commit (see create_teacher).
"""

from typing import Any
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts.register import email_exists, username_exists
from app.db import violated_constraint
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.enums import AuditAction, AuditResult, EmailKind, UserRole
from app.services import audit
from app.services.email import EmailService

EMAIL_TAKEN_MESSAGE = "An account with this email address already exists."
USERNAME_TAKEN_MESSAGE = "This username is already in use."


def create_teacher(
    admin_id: UUID,
    email: str,
    username: str,
    can_manage_content: bool,
    db: Session,
    *,
    ip_address: str | None = None,
) -> User:
    """Add an active staff account with no password and queue its set-password email.

    The route checks the format of `email` and `username` first; the address is stored
    lowercase. 409 EMAIL_TAKEN if any account has the address (deactivated ones too), 409
    USERNAME_TAKEN if the username is in use in any case. Records user_created: SUCCESS with
    the new account as the target, or FAILURE with the error code and no target. On an
    AppError nothing but that FAILURE entry has been written, so the caller can commit it.
    """
    email = email.strip().lower()
    details: dict[str, Any] = {
        "email": email,
        "username": username,
        "role": UserRole.STAFF.value,
        "can_manage_content": can_manage_content,
    }
    try:
        user = _add_staff_user(email, username, can_manage_content, db)
    except AppError as error:
        audit.record(
            admin_id,
            AuditAction.CREATE_USER,
            db,
            result=AuditResult.FAILURE,
            details=details | {"error": error.code.value},
            ip_address=ip_address,
        )
        raise
    EmailService.queue_email(user, EmailKind.SET_PASSWORD, db)  # the token is made when sent
    audit.record(
        admin_id,
        AuditAction.CREATE_USER,
        db,
        target_id=user.id,
        details=details,
        ip_address=ip_address,
    )
    return user


def _add_staff_user(email: str, username: str, can_manage_content: bool, db: Session) -> User:
    # the address first: it is what the admin page shows the conflict against
    if email_exists(email, db):
        raise AppError(ErrorCode.EMAIL_TAKEN, EMAIL_TAKEN_MESSAGE)
    if username_exists(username, db):
        raise AppError(ErrorCode.USERNAME_TAKEN, USERNAME_TAKEN_MESSAGE)
    try:
        # a savepoint: losing a race to the same address or username rolls back only this
        # insert, so the transaction can still record the FAILURE entry
        with db.begin_nested():
            return db.scalars(
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
        constraint = violated_constraint(err)
        if constraint == "uq_users_email":
            raise AppError(ErrorCode.EMAIL_TAKEN, EMAIL_TAKEN_MESSAGE) from err
        if constraint == "uq_users_username_lower":
            raise AppError(ErrorCode.USERNAME_TAKEN, USERNAME_TAKEN_MESSAGE) from err
        raise  # any other constraint is a real error
