"""The signed-in user's own profile."""

from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user, revoke_all_refresh_tokens
from app.accounts.password import check_password_hash, hash_password, validate_password_security
from app.accounts.profile import current_class_id, select_class, set_practice_language
from app.accounts.tokens import create_refresh_token
from app.api.v1.email_links import StatusResponse
from app.db import get_db
from app.errors import AppError, ErrorCode, error_responses
from app.models.accounts import User
from app.models.enums import EmailKind, Language, UserRole
from app.services.email import EmailService
from app.services.locks import lock_user

me_router = APIRouter(prefix="/me", tags=["me"])

_BCRYPT_MAX_BYTES = 72


class ProfileResponse(BaseModel):
    """The user's profile and the settings of their app."""

    id: UUID
    username: str
    role: UserRole
    current_class_id: UUID | None = Field(
        description="The class the app follows. Null means Practice (or no active class)."
    )
    preferred_language: Language | None = Field(
        description="The language of Practice games. Null until the student has chosen one."
    )


class UpdateProfileRequest(BaseModel):
    """The settings to change. Send at least one; the others stay as they are."""

    current_class_id: UUID | None = Field(
        default=None,
        description="A class the student is actively enrolled in, or null to switch to Practice.",
    )
    preferred_language: Language | None = Field(
        default=None,
        description="The language of Practice games. Leave it out to keep it as it is.",
    )

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        # a null current_class_id is a change (Practice), so look at what was sent, not the value
        if "current_class_id" not in self.model_fields_set and self.preferred_language is None:
            raise ValueError("Send current_class_id, preferred_language or both")
        return self


class ChangePasswordRequest(BaseModel):
    """The password the user has now and the one they want."""

    current_password: str
    new_password: str = Field(max_length=200)  # the rules are checked by the service (422)

    @field_validator("current_password")
    @classmethod
    def _fits_bcrypt(cls, value: str) -> str:
        # bcrypt raises on anything longer, which would be a 500
        if len(value.encode("utf-8")) > _BCRYPT_MAX_BYTES:
            raise ValueError(f"must be at most {_BCRYPT_MAX_BYTES} bytes")
        return value


def _profile(user: User, db: Session) -> ProfileResponse:
    return ProfileResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        current_class_id=current_class_id(user, db),
        preferred_language=user.preferred_language,
    )


@me_router.patch(
    "/profile",
    response_model=ProfileResponse,
    operation_id="updateProfile",
    summary="Select the current class or set the Practice language",
    description=(
        "Change the class the app follows, the language of Practice games, or both. "
        "A null current_class_id switches to Practice. "
        "A class the student is not actively enrolled in is 404 NOT_FOUND, whether or not it "
        "exists, except that a student whose enrolment ended because the class was archived gets "
        "409 CLASS_ARCHIVED. While the student has an open (active or paused) game, nothing can "
        "be changed: 409 SESSION_ALREADY_OPEN, with the game's id in the error details. "
        "Admins don't play, so for them it is 403 FORBIDDEN_ROLE. "
        "Nothing is saved if either change is refused."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
    ),
)
def update_profile(
    request: UpdateProfileRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProfileResponse:
    """Apply the changes in one transaction."""
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")
    if "current_class_id" in request.model_fields_set:
        select_class(user, request.current_class_id, db)
    if request.preferred_language is not None:
        set_practice_language(user, request.preferred_language, db)
    response = _profile(user, db)
    db.commit()
    return response


@me_router.post(
    "/password",
    operation_id="changePassword",
    summary="Change the signed-in user's password",
    description=(
        "Needs the current password: a wrong one is 401 INVALID_CREDENTIALS. A new password that "
        "breaks the password rules or is the same as the current one is 422 VALIDATION_ERROR. "
        "Nothing is saved if the request is refused. On success, unused reset and set-password "
        "links stop working and every refresh token is revoked, so other devices are signed out "
        "when their access token expires. This device gets a new refresh_token cookie."
    ),
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
)
def change_password(
    request: ChangePasswordRequest,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StatusResponse:
    """Replace the password, then sign out every session but this one."""
    validate_password_security(request.new_password)
    # raises on a wrong password, but returns False if it finds no row, so the result counts
    if not check_password_hash(user.email, request.current_password, db):
        raise AppError(ErrorCode.INVALID_CREDENTIALS)
    if request.new_password == request.current_password:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "The new password must be different from your current password.",
        )
    password_hash = hash_password(request.new_password)  # slow (bcrypt), so before the lock
    lock_user(user.id, db)  # email_tokens are only touched with the user locked
    user.password_hash = password_hash
    # a link sent before this change could otherwise overwrite the new password
    EmailService.invalidate_user_tokens(user.id, EmailKind.RESET_PASSWORD, db)
    EmailService.invalidate_user_tokens(user.id, EmailKind.SET_PASSWORD, db)
    revoke_all_refresh_tokens(user.id, db)  # commits, so all of the above is saved together
    response.set_cookie("refresh_token", create_refresh_token(user.id, db))
    return StatusResponse()
