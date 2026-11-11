"""The signed-in user's own profile."""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.accounts.profile import current_class_id, select_class, set_practice_language
from app.db import get_db
from app.engagement.interface import get_language_engagement_stats, list_unlocked_achievements
from app.errors import AppError, ErrorCode, error_responses
from app.game.history import list_history
from app.models.accounts import User
from app.models.enums import (
    Difficulty,
    Language,
    SessionEndReason,
    SessionState,
    UserRole,
)

me_router = APIRouter(prefix="/me", tags=["me"])


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
        # A null current_class_id is a change, so inspect which fields were sent.
        if "current_class_id" not in self.model_fields_set and self.preferred_language is None:
            raise ValueError("Send current_class_id, preferred_language or both")
        return self


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


class RecentBadgeResponse(BaseModel):
    """A recently unlocked achievement."""

    achievement_key: str
    icon_key: str
    language: Language | None
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


# a bigger page would overflow the database OFFSET (500); nobody has 10,000 pages of games
MAX_PAGE = 10_000


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
        "so it is listed. Paged with page (1 to 10,000) and page_size (default 20, at most "
        "100); a page past the end has no items. The details of a game come from its summary. "
        "Admins don't play, so for them it is 403 FORBIDDEN_ROLE."
    ),
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
)
def list_my_games(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    language: Language | None = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1,
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
