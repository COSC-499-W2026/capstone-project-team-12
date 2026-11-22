"""Admin API: staff accounts and content rights.

Every route needs an admin account; anyone else gets 403 FORBIDDEN_ROLE, and a deactivated
admin 403 ACCOUNT_DISABLED. Admin accounts are only created with the command-line tool, never
here. Every change is recorded in the admin audit log; a refused request changes nothing, so
it records nothing, and reads are not recorded.
"""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.accounts.interface import create_teacher, get_current_user, require_admin
from app.accounts.staff import list_staff_accounts
from app.db import get_db
from app.errors import error_responses
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


class StaffAccount(BaseModel):
    """A staff (teacher) or admin account as the admin pages show it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    username: str
    role: UserRole
    can_manage_content: bool
    is_active: bool
    last_login_at: datetime | None = Field(description="Null if the account never signed in.")
    created_at: datetime


class StaffAccountPage(BaseModel):
    """A page of staff and admin accounts (docs/api-conventions.md, Pagination)."""

    items: list[StaffAccount]
    page: int
    page_size: int
    total: int
    has_next_page: bool


@admin_router.get(
    "/staff",
    operation_id="listStaffAccounts",
    summary="List staff and admin accounts",
    # NOT_AUTHENTICATED; FORBIDDEN_ROLE, ACCOUNT_DISABLED
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
)
def get_staff_accounts(
    db: Annotated[Session, Depends(get_db)],
    email: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=254,
            description="Only addresses that contain this text, in any case.",
        ),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> StaffAccountPage:
    """Every staff and admin account with its role, content rights, status and last login,
    deactivated ones too, by username in any case. Students are not listed. `email` searches
    the addresses. Paged with page and page_size (default 20, at most 100); a page past the
    end has no items.
    """
    staff = list_staff_accounts(page, page_size, db, email=email)
    return StaffAccountPage(
        items=[StaffAccount.model_validate(user) for user in staff.items],
        page=page,
        page_size=page_size,
        total=staff.total,
        has_next_page=page * page_size < staff.total,
    )


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
    admin: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StaffAccount:
    """Create a teacher account, with content rights if `can_manage_content` is true, and queue
    an email with a link to set its password (valid 3 days). The account can't sign in until
    the password is set.

    409 EMAIL_TAKEN if an account already has the address, 409 USERNAME_TAKEN if the username
    is in use. The new account is recorded in the admin audit log as create_staff.
    """
    user = create_teacher(admin.id, fields.email, fields.username, fields.can_manage_content, db)
    account = StaffAccount.model_validate(user)
    db.commit()
    return account
