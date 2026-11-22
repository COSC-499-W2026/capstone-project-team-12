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

from app.accounts.interface import (
    create_teacher,
    get_current_user,
    list_staff_accounts,
    require_admin,
)
from app.accounts.staff import set_staff_active
from app.api.pagination import Page, PageParams, page_params
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


class StaffAccountPage(Page[StaffAccount]):
    """A page of staff and admin accounts (docs/api-conventions.md, Pagination)."""


@admin_router.get(
    "/staff",
    operation_id="listStaffAccounts",
    summary="List staff and admin accounts",
    # NOT_AUTHENTICATED; FORBIDDEN_ROLE, ACCOUNT_DISABLED
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
)
def get_staff_accounts(
    db: Annotated[Session, Depends(get_db)],
    params: Annotated[PageParams, Depends(page_params)],
    email: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=254,
            description="Only addresses that contain this text, in any case.",
        ),
    ] = None,
) -> StaffAccountPage:
    """Every staff and admin account with its role, content rights, status and last login,
    deactivated ones too, by username in any case. Students are not listed. `email` searches
    the addresses. Paged with page and page_size (default 20, at most 100); a page past the
    end has no items.
    """
    staff = list_staff_accounts(params.offset, params.page_size, db, email=email)
    items = [StaffAccount.model_validate(user) for user in staff.items]
    return StaffAccountPage.of(items, params, staff.total)


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


@admin_router.post(
    "/users/{user_id}/deactivate",
    operation_id="deactivateUser",
    summary="Deactivate a staff account",
    # NOT_AUTHENTICATED; FORBIDDEN_ROLE, ACCOUNT_DISABLED; USER_NOT_FOUND
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND),
)
def deactivate_user(
    user_id: UUID,
    request: Request,
    admin: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StaffAccount:
    """Stop a staff account from signing in and revoke all its refresh tokens, so it is signed
    out on every device; its access tokens get 403 ACCOUNT_DISABLED. Content rights and classes
    are kept. Returns the account.

    403 FORBIDDEN_ROLE for an admin or student account, 404 USER_NOT_FOUND for an unknown id.
    Each attempt is recorded in the audit log as user_deactivated, a refused one with its code.
    """
    return _set_active(user_id, False, request, admin, db)


@admin_router.post(
    "/users/{user_id}/reactivate",
    operation_id="reactivateUser",
    summary="Reactivate a staff account",
    # NOT_AUTHENTICATED; FORBIDDEN_ROLE, ACCOUNT_DISABLED; USER_NOT_FOUND
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND),
)
def reactivate_user(
    user_id: UUID,
    request: Request,
    admin: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StaffAccount:
    """Let a deactivated staff account sign in again; sessions ended by the deactivation stay
    ended. Returns the account. Errors and audit as for deactivating (user_reactivated).
    """
    return _set_active(user_id, True, request, admin, db)


def _set_active(
    user_id: UUID, active: bool, request: Request, admin: User, db: Session
) -> StaffAccount:
    ip_address = request.client.host if request.client else None
    try:
        user = set_staff_active(admin.id, user_id, active, db, ip_address=ip_address)
    except AppError:
        db.commit()  # keeps the DENIED or FAILURE entry; nothing else was written
        raise
    account = StaffAccount.model_validate(user)
    db.commit()
    return account
