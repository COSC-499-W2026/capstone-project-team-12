"""The signed-in user's own profile."""

from datetime import UTC, date, datetime
from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.accounts.email_links import cancel_waiting_reset_mail
from app.accounts.interface import get_current_user, revoke_all_refresh_tokens
from app.accounts.password import hash_password, validate_password_security, verified_password_hash
from app.accounts.profile import current_class_id, select_class, set_practice_language
from app.accounts.tokens import create_refresh_token, set_refresh_cookie
from app.api.v1.email_links import StatusResponse
from app.db import get_db
from app.engagement.interface import (
    app_calendar_date,
    challenge_expiry,
    ensure_daily_challenges,
    get_active_class_ids,
    get_and_report_streak,
    get_language_engagement_stats,
    leave_class,
    list_unlocked_achievements,
    validate_code_and_enrol,
)
from app.errors import AppError, ErrorCode, error_responses
from app.game.history import list_history
from app.game.interface import end_open_sessions
from app.models.accounts import User
from app.models.enums import (
    DailyChallengeType,
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
me_classes_router = APIRouter(prefix="/me/classes", tags=["classes"])


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
    set_refresh_cookie(response, refresh_token)
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
        description="Current global streak across languages, using the app's local calendar.",
    )
    best_streak_days: int = Field(
        ge=0,
        description="Longest global streak achieved across languages.",
    )

    streak_broken: bool = Field(description="True once when a previously active streak expires.")
    daily_challenges: DailyChallengesResponse


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
        "Students and staff can play and earn points. Streaks follow the app's local calendar."
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
    current_streak, best_streak, streak_broken = get_and_report_streak(
        user.id, db, today=daily_challenges.challenge_date
    )

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
        best_streak_days=best_streak,
    )
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


class JoinClassRequest(BaseModel):
    """Access code provided by a student joining a classroom."""

    access_code: str = Field(
        min_length=1,
        max_length=32,
        description="Eight-character classroom access code. Spaces and hyphens are accepted.",
        examples=["K7PM-3QXD"],
    )


class JoinClassResponse(BaseModel):
    """Classroom selected after successful enrolment."""

    class_id: UUID
    language: Language


@me_classes_router.post(
    "/join",
    response_model=JoinClassResponse,
    operation_id="joinClass",
    summary="Join a classroom",
    description=(
        "Enrol the authenticated student using a classroom access code. "
        "A student may have only one active classroom per language. "
        "Students who voluntarily left may rejoin, but students removed "
        "by a teacher cannot. Teachers cannot join as students, and "
        "teaching assistants cannot join classes they assist. "
        "Successful enrolment also makes it the student's current class. "
        "Repeating a successful request with the original access code "
        "returns success without changing the current class or consuming another use, "
        "even if the code has expired or reached its usage limit. A deactivated code "
        "returns 400 INVALID_ACCESS_CODE, including the original code. A different valid code "
        "for the same class returns 409 ALREADY_IN_CLASS_FOR_LANGUAGE."
    ),
    responses=error_responses(
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.CONFLICT,
    ),
)
def join_class(
    request: JoinClassRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> JoinClassResponse:
    """Join a classroom and select it in one transaction."""
    joined = validate_code_and_enrol(user.id, request.access_code, db)
    db.commit()
    return JoinClassResponse(class_id=joined.class_id, language=joined.language)


@me_classes_router.post(
    "/{class_id}/leave",
    status_code=HTTPStatus.NO_CONTENT,
    operation_id="leaveClass",
    summary="Leave a classroom",
    description=(
        "Leave an actively enrolled classroom. "
        "The student may rejoin later using a valid access code. "
        "If this was the student's current class, another active class "
        "is selected when available; otherwise, the selection is cleared. "
        "Leaving ends any open (active or paused) class game sessions."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
    ),
)
def leave_class_endpoint(
    class_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Leave a classroom and close its open games in one transaction."""
    try:
        leave_class(user.id, class_id, db)
        end_open_sessions(class_id, db, student_id=user.id)
        db.commit()
    except Exception:
        db.rollback()
        raise
