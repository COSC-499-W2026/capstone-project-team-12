"""Admin API: staff accounts, content rights, and exporting or deleting a user's data.

Every route needs an admin account; anyone else gets 403 FORBIDDEN_ROLE, and a deactivated
admin 403 ACCOUNT_DISABLED. Admin accounts are only created with the command-line tool, never
here. Every change and every export is recorded in the admin audit log; a refused request
records nothing, and other reads are not recorded.
"""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.accounts.anonymise import anonymise_user
from app.accounts.interface import (
    UserExport,
    create_teacher,
    export_user,
    get_current_user,
    list_staff_accounts,
    require_admin,
    set_staff_active,
)
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
    admin: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StaffAccount:
    """Stop a staff account from signing in and revoke all its refresh tokens, so it is signed
    out on every device; its access tokens get 403 ACCOUNT_DISABLED. Content rights and classes
    are kept. Returns the account.

    403 FORBIDDEN_ROLE for an admin or student account, 404 USER_NOT_FOUND for an unknown id.
    The change is recorded in the admin audit log as deactivate.
    """
    return _set_active(user_id, False, admin, db)


@admin_router.post(
    "/users/{user_id}/reactivate",
    operation_id="reactivateUser",
    summary="Reactivate a staff account",
    # NOT_AUTHENTICATED; FORBIDDEN_ROLE, ACCOUNT_DISABLED; USER_NOT_FOUND
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND),
)
def reactivate_user(
    user_id: UUID,
    admin: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StaffAccount:
    """Let a deactivated staff account sign in again; sessions ended by the deactivation stay
    ended. Returns the account. Errors as for deactivating; recorded as reactivate.
    """
    return _set_active(user_id, True, admin, db)


def _set_active(user_id: UUID, active: bool, admin: User, db: Session) -> StaffAccount:
    account = StaffAccount.model_validate(set_staff_active(admin.id, user_id, active, db))
    db.commit()
    return account


@admin_router.get(
    "/users/{user_id}/export",
    operation_id="exportUser",
    summary="Export a user's data as a JSON file",
    # NOT_AUTHENTICATED; FORBIDDEN_ROLE, ACCOUNT_DISABLED; USER_NOT_FOUND
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND),
)
def get_user_export(
    user_id: UUID,
    response: Response,
    admin: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserExport:
    """Download what is stored about an account of any role: the profile, class enrolments, TA
    roles, owned classes, games with their locations, points, badges, the streak, daily
    challenges, and photos not yet purged (none are stored yet, so `photos` is empty). Sent as an
    attachment named user-{id}.json and never cached. The password hash and tokens are never
    included.

    404 USER_NOT_FOUND for an unknown id. The export is recorded in the admin audit log as
    export_user.
    """
    export = export_user(admin.id, user_id, db)
    db.commit()
    response.headers["Content-Disposition"] = f'attachment; filename="user-{user_id}.json"'
    response.headers["Cache-Control"] = "no-store"  # personal data: no copy in a browser cache
    return export


class DeleteUserRequest(BaseModel):
    """The admin confirms a deletion by typing the account's username. It is sent in the body,
    not the query, so the name being deleted doesn't end up in access logs."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=1, max_length=30, description="The account's username, exactly as shown."
    )


@admin_router.delete(
    "/users/{user_id}",
    operation_id="deleteUser",
    summary="Delete an account by anonymising it",
    status_code=HTTPStatus.NO_CONTENT,
    # NOT_AUTHENTICATED; FORBIDDEN_ROLE, ACCOUNT_DISABLED; USER_NOT_FOUND; OWNS_ACTIVE_CLASS
    # (USERNAME_MISMATCH is a 422, which every route lists)
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def delete_user(
    user_id: UUID,
    fields: DeleteUserRequest,
    admin: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Delete a student or staff account, which can't be undone. The account is anonymised: its
    email address and username are replaced, its password, preferences, email links, emails, GPS
    positions and photos removed, and it is signed out everywhere. Its games, points and
    enrolments stay, anonymised.

    403 FORBIDDEN_ROLE for an admin account, 404 USER_NOT_FOUND, 422 USERNAME_MISMATCH unless
    `username` matches exactly, 409 OWNS_ACTIVE_CLASS (class ids in details) until each class it
    owns is transferred or archived. The deletion is recorded in the admin audit log as
    delete_user.
    """
    anonymise_user(admin.id, user_id, fields.username, db)
    db.commit()
