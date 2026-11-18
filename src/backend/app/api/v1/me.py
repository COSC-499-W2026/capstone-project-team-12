"""The signed-in user's own profile."""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.accounts.email_links import cancel_waiting_reset_mail
from app.accounts.interface import get_current_user, revoke_all_refresh_tokens
from app.accounts.password import hash_password, validate_password_security, verified_password_hash
from app.accounts.profile import current_class_id, select_class, set_practice_language
from app.accounts.tokens import create_refresh_token
from app.api.v1.auth import _set_refresh_cookie
from app.api.v1.email_links import StatusResponse
from app.db import get_db
from app.engagement.interface import (
    get_active_class_ids,
    get_language_engagement_stats,
    list_unlocked_achievements,
)
from app.errors import AppError, ErrorCode, error_responses
from app.game.history import list_history
from app.models.accounts import User
from app.models.enums import (
    Difficulty,
    EmailKind,
    Language,
    SessionEndReason,
    SessionState,
    UserRole,
)
from app.services.email import EmailService
from app.services.locks import lock_user

me_router = APIRouter(prefix="/me", tags=["me"])

_BCRYPT_MAX_BYTES = 72


class ProfileResponse(BaseModel):
    """The user's profile and the settings of their app."""

    id: UUID
    username: str
    email: str
    role: UserRole
    classes: list[UUID] = Field(
        description=(
            "The classes the student is actively enrolled in, newest first by the date they first "
            "joined each one. A class they left and rejoined keeps its original join date. Always "
            "empty for staff and admins: classes a teacher teaches aren't listed here."
        )
    )
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
        # A null current_class_id is a change, so inspect which fields were sent.
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
    # one read, so current_class_id is always one of classes
    active = get_active_class_ids(user.id, db)
    return ProfileResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        classes=active,
        current_class_id=current_class_id(user, db, active),
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
    verified = verified_password_hash(user.email, request.current_password, db)
    if verified is None:
        raise AppError(ErrorCode.INVALID_CREDENTIALS)
    validate_password_security(request.new_password)
    if request.new_password == request.current_password:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "The new password must be different from your current password.",
        )
    password_hash = hash_password(request.new_password)  # slow (bcrypt), so before the lock
    lock_user(user.id, db)  # reloads user; email_tokens are only touched with the user locked
    if user.password_hash != verified:  # changed by another request while bcrypt ran unlocked
        raise AppError(ErrorCode.INVALID_CREDENTIALS)
    user.password_hash = password_hash
    # a link sent before this change could otherwise overwrite the new password
    cancel_waiting_reset_mail(user.id, db)
    EmailService.invalidate_user_tokens(user.id, EmailKind.RESET_PASSWORD, db)
    EmailService.invalidate_user_tokens(user.id, EmailKind.SET_PASSWORD, db)
    revoke_all_refresh_tokens(user.id, db)
    refresh_token, _ = create_refresh_token(user.id, db)
    db.commit()  # one transaction, so nothing is saved unless all of it is
    _set_refresh_cookie(response, refresh_token)
    return StatusResponse()


@me_router.get(
    "/profile",
    response_model=ProfileResponse,
    operation_id="getProfile",
    summary="Get the current user's profile details.",
    description=(
        "The signed-in user's account details, their active classes and the settings of their "
        "app. Staff and admins have no enrolments, so their classes list is empty. Admins get "
        "their profile here, but they don't play, so PATCH /me/profile refuses them with 403 "
        "FORBIDDEN_ROLE."
    ),
    responses=error_responses(HTTPStatus.UNAUTHORIZED),
)
def get_profile(
    user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]
) -> ProfileResponse:
    """Return the signed-in user's profile."""
    return _profile(user, db)


class RecentBadgeResponse(BaseModel):
    """A recently unlocked achievement."""

    achievement_key: str
    icon_key: str
    language: Language | None = Field(description="Null for a global badge.")
    unlocked_at: datetime


class EngagementStatsResponse(BaseModel):
    """The signed-in user's cumulative statistics for one language."""

    language: Language
    total_points: int = Field(
        ge=0,
        description="Cumulative points earned in the selected language.",
    )
    level: int = Field(
        ge=1,
        le=5,
        description="Current language level derived from cumulative points.",
    )
    points_to_next_level: int | None = Field(
        ge=0,
        description="Points needed for the next level; null at level five.",
    )
    recent_badges: list[RecentBadgeResponse] = Field(
        description="Recent unlocks for this language, including global badges."
    )


@me_router.get(
    "/engagement",
    response_model=EngagementStatsResponse,
    operation_id="getMyEngagement",
    summary="Get my engagement statistics",
    description=(
        "Return the authenticated user's cumulative points, level, "
        "points needed for the next level, and recently unlocked badges "
        "for the requested target language. Points are tracked separately "
        "for each language, while global badges are included across languages. "
        "Users without previous activity start at zero points and level one. "
        "Students and staff can play and earn points. "
        "Streaks and daily challenges will be added separately."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    ),
)
def get_my_engagement(
    language: Language,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> EngagementStatsResponse:
    """Read the signed-in user's engagement statistics."""
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")

    stats = get_language_engagement_stats(user.id, language, db)

    return EngagementStatsResponse(
        language=stats.language,
        total_points=stats.total_points,
        level=stats.level,
        points_to_next_level=stats.points_to_next_level,
        recent_badges=[
            RecentBadgeResponse(
                achievement_key=badge.achievement_key,
                icon_key=badge.icon_key,
                language=badge.language,
                unlocked_at=badge.unlocked_at,
            )
            for badge in stats.recent_badges
        ],
    )


class AchievementProgressResponse(BaseModel):
    """Progress toward an achievement with a measurable threshold."""

    current: int
    required: int


class AchievementResponse(BaseModel):
    """An achievement and the user's current unlock state."""

    achievement_key: str
    category: str
    icon_key: str
    language: Language | None
    unlocked: bool
    unlocked_at: datetime | None
    progress: AchievementProgressResponse | None


class AchievementsResponse(BaseModel):
    """Available and previously earned achievements for a language."""

    achievements: list[AchievementResponse]


@me_router.get(
    "/achievements",
    response_model=AchievementsResponse,
    operation_id="getMyAchievements",
    summary="Get my achievements",
    description=(
        "Return active achievements for the requested language, "
        "including global achievements and previously earned inactive "
        "achievements. Each item includes its unlock state and available "
        "progress. Unlocked achievements appear newest first, followed "
        "by locked achievements."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    ),
)
def get_my_achievements(
    language: Language,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AchievementsResponse:
    """Read the signed-in user's achievements."""
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")

    achievements = list_unlocked_achievements(user.id, language, db)

    return AchievementsResponse(
        achievements=[
            AchievementResponse(
                achievement_key=item.achievement_key,
                category=item.category,
                icon_key=item.icon_key,
                language=item.language,
                unlocked=item.unlocked,
                unlocked_at=item.unlocked_at,
                progress=(
                    AchievementProgressResponse(
                        current=item.progress.current,
                        required=item.progress.required,
                    )
                    if item.progress is not None
                    else None
                ),
            )
            for item in achievements
        ]
    )


class GameHistoryItemResponse(BaseModel):
    """One finished game. Never a location's name, room or coordinates."""

    session_id: UUID
    started_at: datetime
    ended_at: datetime = Field(
        description=(
            "When the game was completed, quit or reset. For an expired or class-closed game, "
            "its last action."
        )
    )
    language: Language
    difficulty: Difficulty
    class_id: UUID | None = Field(description="Null for Practice.")
    state: SessionState = Field(description="completed or abandoned.")
    end_reason: SessionEndReason | None = Field(description="Null for a completed game.")
    total_score: int
    locations_completed: int


class GameHistoryResponse(BaseModel):
    """A page of finished games (docs/api-conventions.md, Pagination)."""

    items: list[GameHistoryItemResponse]
    page: int
    page_size: int
    total: int
    has_next_page: bool


@me_router.get(
    "/games",
    response_model=GameHistoryResponse,
    operation_id="listMyGames",
    summary="List my finished games",
    description=(
        "The signed-in user's finished games (completed, or abandoned for any reason: quit, "
        "reset, expired or class closed), newest first, optionally in one language. Open games "
        "are not listed; an open game that has been idle for the expiry limit is expired first, "
        "so it is listed. Paged with page and page_size (default 20, at most 100); a page past "
        "the end has no items. The details of a game come from its summary. "
        "Admins don't play, so for them it is 403 FORBIDDEN_ROLE."
    ),
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
)
def list_my_games(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    language: Language | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> GameHistoryResponse:
    """Read one page of the history; an expiry done while reading is committed."""
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")
    history = list_history(user.id, language, page, page_size, db)
    db.commit()
    return GameHistoryResponse(
        items=[
            GameHistoryItemResponse.model_validate(item, from_attributes=True)
            for item in history.items
        ],
        page=page,
        page_size=page_size,
        total=history.total,
        has_next_page=page * page_size < history.total,
    )
