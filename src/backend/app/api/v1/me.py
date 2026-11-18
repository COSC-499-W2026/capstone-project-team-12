"""The signed-in user's own profile."""

from datetime import UTC, date, datetime
from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.accounts.profile import current_class_id, select_class, set_practice_language
from app.db import get_db
from app.engagement.interface import (
    app_calendar_date,
    challenge_expiry,
    ensure_daily_challenges,
    get_active_class_ids,
    get_language_engagement_stats,
    list_unlocked_achievements,
    visible_streak,
)
from app.errors import AppError, ErrorCode, error_responses
from app.game.history import list_history
from app.models.accounts import User
from app.models.engagement import UserStats
from app.models.enums import (
    DailyChallengeType,
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


class DailyChallengeResponse(BaseModel):
    """One daily challenge and its current progress."""

    id: UUID
    challenge_date: date
    slot: int = Field(ge=1, le=3)
    challenge_type: DailyChallengeType
    params: dict[str, object]
    description_key: str
    reward_points: int = Field(ge=0)
    progress_current: int = Field(ge=0)
    progress_required: int = Field(gt=0)
    completed: bool
    completed_at: datetime | None


class DailyChallengesResponse(BaseModel):
    """The three challenges assigned to the signed-in user for today."""

    challenge_date: date
    expires_at: datetime = Field(description="Next local midnight, expressed in UTC.")
    challenges: list[DailyChallengeResponse] = Field(min_length=3, max_length=3)


class EngagementStatsResponse(BaseModel):
    """The signed-in user's cumulative statistics for one language."""

    daily_challenges: DailyChallengesResponse
    streak_broken: bool = Field(description="True once when a previously active streak expires.")
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
    current_streak_days: int = Field(
        ge=0,
        description="Current global streak across languages, using Vancouver calendar dates.",
    )
    best_streak_days: int = Field(
        ge=0,
        description="Longest global streak achieved across languages.",
    )


@me_router.get(
    "/engagement",
    response_model=EngagementStatsResponse,
    operation_id="getMyEngagement",
    summary="Get my engagement statistics",
    description=(
        "Return the authenticated user's cumulative points, level, "
        "points needed for the next level, recently unlocked badges, and global streak "
        "statistics for the requested target language. Points are tracked separately "
        "for each language, while streaks and global badges are shared across languages. "
        "Includes today’s challenges, their expiry, and a one-time streak expiration flag. "
        "Students and staff can play and earn points. Streaks use the America/Vancouver calendar."
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

    now = datetime.now(UTC)
    daily_challenges = _daily_challenges_response(user.id, db, now)
    stats = get_language_engagement_stats(user.id, language, db)
    global_stats = db.get(UserStats, user.id, populate_existing=True)
    current_streak = visible_streak(global_stats, today=daily_challenges.challenge_date)
    streak_broken = False
    if (
        global_stats is not None
        and global_stats.last_active_date is not None
        and global_stats.current_streak_days > 0
        and current_streak == 0
        and global_stats.last_active_date != global_stats.reported_break_date
    ):
        global_stats.reported_break_date = global_stats.last_active_date
        streak_broken = True

    response = EngagementStatsResponse(
        daily_challenges=daily_challenges,
        streak_broken=streak_broken,
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
        current_streak_days=current_streak,
        best_streak_days=global_stats.best_streak_days if global_stats else 0,
    )
    db.commit()
    return response


@me_router.get(
    "/daily-challenges",
    response_model=DailyChallengesResponse,
    operation_id="getMyDailyChallenges",
    summary="Get my daily challenges",
    description=(
        "Return exactly three daily challenges for the authenticated user. "
        "Challenges follow the America/Vancouver calendar and reset at local midnight. "
        "The first request of each local day generates the challenges. Subsequent requests "
        "return the existing challenges and preserve their progress."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    ),
)
def get_my_daily_challenges(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DailyChallengesResponse:
    """Return today's challenges, generating them if necessary."""
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")

    response = _daily_challenges_response(user.id, db, datetime.now(UTC))
    db.commit()
    return response


def _daily_challenges_response(
    user_id: UUID, db: Session, now: datetime
) -> DailyChallengesResponse:
    today = app_calendar_date(now)
    challenges = ensure_daily_challenges(user_id, db, now=now)

    return DailyChallengesResponse(
        challenge_date=today,
        expires_at=challenge_expiry(today),
        challenges=[
            DailyChallengeResponse(
                id=challenge.id,
                challenge_date=challenge.challenge_date,
                slot=challenge.slot,
                challenge_type=DailyChallengeType(challenge.challenge_type),
                params=challenge.params,
                description_key=challenge.description_key,
                reward_points=challenge.reward_points,
                progress_current=challenge.progress_current,
                progress_required=challenge.progress_required,
                completed=challenge.completed_at is not None,
                completed_at=challenge.completed_at,
            )
            for challenge in challenges
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
