"""Admin API (A27): staff accounts and content rights.

Every route needs an admin account; anyone else gets 403 FORBIDDEN_ROLE, and a deactivated
admin 403 ACCOUNT_DISABLED. Admin accounts are only created with the command-line tool, never
here. Every action, including one refused with an error, is written to the audit log.
"""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user, require_admin
from app.accounts.register import email_is_valid, username_is_valid
from app.accounts.staff import create_teacher
from app.db import get_db
from app.errors import AppError, error_responses
from app.models.accounts import User
from app.models.enums import UserRole

admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class CreateStaffRequest(BaseModel):
    """A new teacher account. The teacher sets the password from the emailed link."""

    # an unknown field such as "role" is refused rather than ignored: this only makes teachers
    model_config = ConfigDict(extra="forbid")

    email: str = Field(
        min_length=1,
        max_length=254,
        description="The teacher's address, used to sign in. Stored lowercase.",
        examples=["tanaka@example.com"],
    )
    username: str = Field(
        min_length=3,
        max_length=30,
        description="3 to 30 characters with no space at either end; unique in any case.",
        examples=["Tanaka-sensei"],
    )
    can_manage_content: bool = Field(
        default=False,
        description="Content rights: add and edit locations, clues and audio.",
    )

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        # the same check as sign-up
        value = value.strip().lower()
        if not email_is_valid(value):
            raise ValueError("not a valid email address")
        return value

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        if not username_is_valid(value):
            raise ValueError("must be 3 to 30 characters with no space at either end")
        return value


class StaffAccount(BaseModel):
    """A staff (teacher) account as the admin pages show it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    username: str
    role: UserRole
    can_manage_content: bool
    is_active: bool
    created_at: datetime


@admin_router.post(
    "/staff",
    operation_id="createStaffAccount",
    summary="Create a teacher account",
    status_code=HTTPStatus.CREATED,
    # NOT_AUTHENTICATED; FORBIDDEN_ROLE, ACCOUNT_DISABLED; EMAIL_TAKEN, USERNAME_TAKEN
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.CONFLICT),
)
def post_staff_account(
    fields: CreateStaffRequest,
    request: Request,
    admin: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StaffAccount:
    """Create a teacher account, with content rights if `can_manage_content` is true, and queue
    an email with a link to set its password (valid 3 days). The account can't sign in until
    the password is set.

    409 EMAIL_TAKEN if an account already has the address, 409 USERNAME_TAKEN if the username
    is in use. Each attempt is recorded in the audit log as user_created: a success with the
    new account, or a failure with the error code.
    """
    ip_address = request.client.host if request.client else None
    try:
        user = create_teacher(
            admin.id,
            fields.email,
            fields.username,
            fields.can_manage_content,
            db,
            ip_address=ip_address,
        )
    except AppError:
        db.commit()  # keeps the FAILURE entry; nothing else was written
        raise
    account = StaffAccount.model_validate(user)
    db.commit()
    return account
