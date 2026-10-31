from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Self, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, NonNegativeInt, PositiveInt, model_validator
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.db import get_db
from app.errors import AppError, ErrorCode, error_responses
from app.game.arrival import confirm_arrival
from app.game.control import pause_game, quit_game, reset_game, resume_game
from app.game.current_location import Stage, get_current_location
from app.game.navigation import Ping, Trend, process_ping
from app.game.shuffle import shuffle_game
from app.game.start import start_game
from app.game.status import GameStatus, find_current_game, get_game
from app.models.accounts import User
from app.models.enums import (
    ArrivalOverride,
    Difficulty,
    FeedbackCode,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
    UserRole,
)

games_router = APIRouter(prefix="/games", tags=["games"])

T = TypeVar("T")


class StartGameRequest(BaseModel):
    difficulty: Difficulty
    # a class game: the class sets the language
    class_id: UUID | None = None
    # a Practice game
    language: Language | None = None

    @model_validator(mode="after")
    def exactly_one_of_class_or_language(self) -> Self:
        if (self.class_id is None) == (self.language is None):
            raise ValueError("send either class_id or language")
        return self


class StartGameResponse(BaseModel):
    session_id: UUID
    state: SessionState
    position: int
    clue_audio_url: str


@games_router.post(
    "",
    operation_id="startGame",
    status_code=HTTPStatus.CREATED,
    response_model=StartGameResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def start_new_game(
    request: StartGameRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StartGameResponse:
    """Start a class game (`class_id`) or a Practice game (`language`).

    Send exactly one of them. The game gets 5 different locations in the language. Locations the
    student has not completed yet come first. After a quit the same day, the locations that were
    not played carry over to the new game, but not after a reset. Names, rooms and coordinates
    are never sent, only the first clue's audio link.

    409 SESSION_ALREADY_OPEN if the student has an active or paused game (its id is in `details`),
    unless it has been idle for the expiry limit (24 hours by default), then it is expired and the
    new game starts. If the new game then fails (e.g. NOT_ENOUGH_CONTENT), nothing is committed,
    so the expiry is rolled back and the old game stays open until the next start or the expiry
    job.
    409 NOT_ENOUGH_CONTENT if the language has fewer than 5 locations to draw.
    409 CLASS_ARCHIVED for a student whose enrolment ended when the class was archived.
    404 NOT_FOUND for a class the student is not in, or if the first clue has no recording (also
    in a Practice game). 403 FORBIDDEN_ROLE for an admin, ACCOUNT_DISABLED for a disabled account.
    """
    started = start_game(
        user, request.difficulty, db, class_id=request.class_id, language=request.language
    )
    db.commit()
    return StartGameResponse.model_validate(started, from_attributes=True)


class LocationProgressResponse(BaseModel):
    position: int
    status: LocationStatus
    skip_reason: SkipReason | None
    attempts_used: int
    points_awarded: int


class GameStatusResponse(BaseModel):
    """A game's state and how each location went. Never a location's name, room or
    coordinates."""

    session_id: UUID
    state: SessionState
    end_reason: SessionEndReason | None
    class_id: UUID | None
    language: Language
    difficulty: Difficulty
    current_position: int
    shuffles_used: int
    total_score: int
    started_at: datetime
    paused_at: datetime | None
    locations: list[LocationProgressResponse]


# before /{game_id}, so "current" isn't read as an id
@games_router.get(
    "/current",
    operation_id="getCurrentGame",
    response_model=GameStatusResponse,
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND),
)
def current_game(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GameStatusResponse:
    """The student's open (active or paused) game, with the status of each location.

    404 NOT_FOUND if there is none. A game that has been idle for the expiry limit (24 hours by
    default) is expired when it is read, so it gives 404 too. 403 FORBIDDEN_ROLE for an admin,
    ACCOUNT_DISABLED for a disabled account.
    """
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")
    status = find_current_game(user.id, db)
    # commit before the 404, so an expiry done while reading is kept
    db.commit()
    if status is None:
        raise AppError(ErrorCode.NOT_FOUND, "No open game")
    return GameStatusResponse.model_validate(status, from_attributes=True)


@games_router.get(
    "/{game_id}",
    operation_id="getGame",
    response_model=GameStatusResponse,
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND),
)
def game_status(
    game_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GameStatusResponse:
    """One of the student's games, open or finished, with the status of each location.

    404 NOT_FOUND for another student's game, the same as for an id that doesn't exist. An open
    game that has been idle for the expiry limit (24 hours by default) is expired when it is read.
    403 FORBIDDEN_ROLE for an admin, ACCOUNT_DISABLED for a disabled account.
    """
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")
    status = get_game(user.id, game_id, db)
    db.commit()
    return GameStatusResponse.model_validate(status, from_attributes=True)


class CurrentLocationResponse(BaseModel):
    """The current location of a game. Never its name, room number, coordinates or answers."""

    session_id: UUID
    state: SessionState
    position: int
    stage: Stage
    # the clue of this location, for both stages
    clue_audio_url: str
    attempts_used: int
    attempts_left: int
    # Stage 1 only: when "Continue without GPS" is allowed. Compare it with the current time;
    # don't count down from it, since it can move (see the route description)
    gps_override_available_at: datetime | None


@games_router.get(
    "/{game_id}/current-location",
    operation_id="getCurrentLocation",
    response_model=CurrentLocationResponse | GameStatusResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def current_location(
    game_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CurrentLocationResponse | GameStatusResponse:
    """The current location of the student's game: position, stage (`navigation` or
    `verification`), clue audio link and attempts. A paused game can be read too.

    `gps_override_available_at` (Stage 1 only) is when the student may confirm arrival without
    GPS: 3 minutes after the location became current (paused time counts), or straight away once
    the last 3 pings all had poor accuracy, when it is the latest ping's time. It moves with each
    further weak ping and goes back to the 3-minute time after a good one, so only compare it
    with the current time; don't use it as the start of a countdown.

    If a content manager deactivated the location before the student reached it, it is skipped
    (0 points) and the next location comes back. If that was the last one, the game is completed
    and the response is the game status (the same body as GET /games/{id}) instead.
    409 SESSION_NOT_ACTIVE for a game that has ended, or one that has been idle for the expiry
    limit (24 hours by default), which is expired. 409 INVALID_STAGE if the game has no location
    to play at its position (out of step). 404 NOT_FOUND for another student's game, or if the
    clue has no recording. 403 FORBIDDEN_ROLE for an admin, ACCOUNT_DISABLED for a disabled
    account.
    """
    result = _play(lambda: get_current_location(user.id, game_id, db), user, db)
    if isinstance(result, GameStatus):
        return GameStatusResponse.model_validate(result, from_attributes=True)
    return CurrentLocationResponse.model_validate(result, from_attributes=True)


class PingRequest(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    # the browser's accuracy radius in metres
    accuracy_m: float = Field(ge=0, allow_inf_nan=False)


class FeedbackResponse(BaseModel):
    code: FeedbackCode
    audio_url: str


class PingResponse(BaseModel):
    # closer / farther / steady, or null when no trend was evaluated on this ping
    trend: Trend | None
    can_confirm: bool
    # play the feedback only when true
    speak: bool
    feedback: FeedbackResponse
    # when "Continue without GPS" is allowed; compare with the current time, don't count down
    gps_override_available_at: datetime


@games_router.post(
    "/{game_id}/navigation/ping",
    operation_id="sendNavigationPing",
    response_model=PingResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.TOO_MANY_REQUESTS,
    ),
)
def navigation_ping(
    game_id: UUID,
    request: PingRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PingResponse:
    """Send the student's position in Stage 1, about every 5 seconds.

    Returns one feedback code with its audio link. Play it only when `speak` is true: a phrase is
    spoken when a trend is evaluated (about every 25 s), NAV_START on the first good fix,
    NAV_ARRIVED once on entering the radius, and NAV_GPS_WEAK on the first weak fix (accuracy over
    50 m), then at most every 25 s. Other pings return the last spoken code with `speak` false
    and `trend` null. `can_confirm` is true within the radius only when this fix is not weak
    (accuracy ≤ 50 m); a weak fix is never confirmable. A good ping after a weak signal, before
    the next trend, shows NAV_KEEP_GOING (NAV_VERY_CLOSE when near) with `speak` false. No
    direction or target position is ever returned. `gps_override_available_at` is as in GET
    .../current-location: 3 weak pings in a row make it the latest ping's time (already passed),
    and a good ping moves it back to 3 minutes after the location became current.
    409 SESSION_NOT_ACTIVE for a paused game (resume first), a game that has ended, or one idle
    for the expiry limit (24 hours by default), which is expired. 409 INVALID_STAGE unless the
    current location is in Stage 1. 429 RATE_LIMITED for a ping less than 2 s after the last one.
    404 NOT_FOUND for another student's game. 403 FORBIDDEN_ROLE for an admin, ACCOUNT_DISABLED
    for a disabled account.
    """
    ping = Ping(request.lat, request.lng, request.accuracy_m)
    outcome = _play(lambda: process_ping(user.id, game_id, ping, db), user, db)
    return PingResponse(
        trend=outcome.trend,
        can_confirm=outcome.can_confirm,
        speak=outcome.speak,
        feedback=FeedbackResponse(code=outcome.feedback, audio_url=outcome.feedback_audio_url),
        gps_override_available_at=outcome.gps_override_available_at,
    )


class ArrivalRequest(BaseModel):
    # "gps_unavailable": "Continue without GPS", allowed from gps_override_available_at on
    override_reason: ArrivalOverride | None = None
    # the position the app is confirming; a different current position gives 409 STATE_CHANGED
    position: PositiveInt | None = None


class ArrivalResponse(CurrentLocationResponse):
    # VERIFY_START, to play now (the same list shape as the photo response)
    feedback: list[FeedbackResponse]


@games_router.post(
    "/{game_id}/navigation/confirm-arrival",
    operation_id="confirmArrival",
    response_model=ArrivalResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def navigation_confirm_arrival(
    game_id: UUID,
    request: ArrivalRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ArrivalResponse:
    """Confirm arrival and start Stage 2, plus VERIFY_START.

    No override: ping under 30 s and in the radius, else 409 STALE_POSITION or NOT_WITHIN_RADIUS
    (`details` reason `gps_weak` if accuracy is over 50 m). `gps_unavailable` works from
    `gps_override_available_at` (3 min, or 3 weak pings), else 409 GPS_OVERRIDE_NOT_ALLOWED.
    Stage 2 repeats with no writes, even paused. 409 SESSION_NOT_ACTIVE if paused in Stage 1,
    ended or expired; STATE_CHANGED for a stale `position`; INVALID_STAGE otherwise. A shuffle
    keeps the position; the new row has no ping (409 STALE_POSITION). 404 for another student's
    game or no recording. 403 FORBIDDEN_ROLE or ACCOUNT_DISABLED.
    """
    arrival = _play(
        lambda: confirm_arrival(user.id, game_id, request.override_reason, request.position, db),
        user,
        db,
    )
    feedback = FeedbackResponse(code=arrival.feedback, audio_url=arrival.feedback_audio_url)
    location = CurrentLocationResponse.model_validate(arrival.location, from_attributes=True)
    return ArrivalResponse(**location.model_dump(), feedback=[feedback])


class PausedGameResponse(GameStatusResponse):
    # when the game expires if nothing else happens
    expires_at: datetime


@games_router.post(
    "/{game_id}/pause",
    operation_id="pauseGame",
    response_model=PausedGameResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def pause(
    game_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PausedGameResponse:
    """Pause the student's game. The current location keeps its stage and attempts.

    A new pause counts as a game action, so `expires_at` is now plus the expiry limit (24 hours
    by default). A repeat pause changes nothing and returns the original `expires_at`. 409
    SESSION_NOT_ACTIVE for a completed or abandoned game, or one that has been idle for the
    expiry limit, which is expired. 404 NOT_FOUND for another student's game. 403 FORBIDDEN_ROLE
    for an admin, ACCOUNT_DISABLED for a disabled account.
    """
    paused = _play(lambda: pause_game(user.id, game_id, db), user, db)
    return PausedGameResponse(**asdict(paused.status), expires_at=paused.expires_at)


@games_router.post(
    "/{game_id}/resume",
    operation_id="resumeGame",
    response_model=GameStatusResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def resume(
    game_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GameStatusResponse:
    """Resume the student's paused game where it was left.

    Resuming an active game changes nothing and returns it. A game that has been idle for the
    expiry limit (24 hours by default) can't be resumed: it is expired and gives 409
    SESSION_NOT_ACTIVE, the same as a completed or abandoned game. 404 NOT_FOUND for another
    student's game. 403 FORBIDDEN_ROLE for an admin, ACCOUNT_DISABLED for a disabled account.
    """
    status = _play(lambda: resume_game(user.id, game_id, db), user, db)
    return GameStatusResponse.model_validate(status, from_attributes=True)


@games_router.post(
    "/{game_id}/quit",
    operation_id="quitGame",
    response_model=GameStatusResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def quit_the_game(
    game_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GameStatusResponse:
    """End the student's game (abandoned, end reason quit). A paused game can be quit too.

    Points already earned stay. The unplayed locations are skipped and come first in the next
    game started today in the same mode, language and difficulty. Returns the game status for
    now; once GET /games/{id}/summary exists, it returns the summary instead. 409
    SESSION_NOT_ACTIVE for a game that has already ended (so quitting twice is a 409), or one that
    has been idle for the expiry limit (24 hours by default), which is expired. 404 NOT_FOUND for
    another student's game. 403 FORBIDDEN_ROLE for an admin, ACCOUNT_DISABLED for a disabled
    account.
    """
    status = _play(lambda: quit_game(user.id, game_id, db), user, db)
    return GameStatusResponse.model_validate(status, from_attributes=True)


@games_router.post(
    "/{game_id}/reset",
    operation_id="resetGame",
    status_code=HTTPStatus.CREATED,
    response_model=StartGameResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def reset(
    game_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StartGameResponse:
    """End the student's game and start a new one with the same class, language and difficulty.

    A paused game can be reset too. Returns the new game, the same body as POST /games. The new
    locations are a fresh random draw: unplayed locations of a game quit earlier today don't come
    back. Points already earned stay with the old game.
    409 RESET_LIMIT_REACHED after 3 resets today (local time). 409 NOT_ENOUGH_CONTENT if the
    language has too few locations; the current game is left as it is. 409 SESSION_NOT_ACTIVE
    for a game that has already ended, or one that has been idle for the expiry limit (24 hours
    by default), which is expired. 404 NOT_FOUND for another student's game, or if the new first
    clue has no recording. 403 FORBIDDEN_ROLE for an admin, ACCOUNT_DISABLED for a disabled
    account.
    """
    started = _play(lambda: reset_game(user.id, game_id, db), user, db)
    return StartGameResponse.model_validate(started, from_attributes=True)


class ShuffleRequest(BaseModel):
    # the shuffles_used the app last saw, so a retried request can't shuffle twice
    shuffles_used: NonNegativeInt


class ShuffledGameResponse(GameStatusResponse):
    # the clue of the new current location
    clue_audio_url: str


@games_router.post(
    "/{game_id}/shuffle",
    operation_id="shuffleGame",
    response_model=ShuffledGameResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def shuffle(
    game_id: UUID,
    request: ShuffleRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ShuffledGameResponse:
    """Replace the unfinished locations of the student's game (the current one and those still
    to come) with new ones. Finished locations and their points don't change.

    Send the `shuffles_used` from the last game status. Returns the game status plus
    `clue_audio_url`, the clue of the new current location. 409 STATE_CHANGED if `shuffles_used`
    no longer matches, for example a retry of a shuffle that already happened; nothing changes,
    `details` holds the current `shuffles_used`, `state` and `current_position`, and the
    locations come from GET /games/{id}. 409 SHUFFLE_LIMIT_REACHED after 2 shuffles
    in this game. 409 NOT_ENOUGH_CONTENT if there aren't enough new locations; the shuffle isn't
    used up. 409 SESSION_NOT_ACTIVE for a paused game (resume first), a game that has ended, or
    one that has been idle for the expiry limit (24 hours by default), which is expired. 404
    NOT_FOUND for another student's game, or if the new clue has no recording. 403
    FORBIDDEN_ROLE for an admin, ACCOUNT_DISABLED for a disabled account.
    """
    shuffled = _play(lambda: shuffle_game(user.id, game_id, request.shuffles_used, db), user, db)
    return ShuffledGameResponse(**asdict(shuffled.status), clue_audio_url=shuffled.clue_audio_url)


def _play(action: Callable[[], T], user: User, db: Session) -> T:
    """Run a game action and commit, also when it is refused, so an expiry done before the 409 is
    kept."""
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admins don't play")
    try:
        result = action()
    except AppError:
        # actions must raise AppError only before a write, or after the expiry: this commits first
        db.commit()
        raise
    db.commit()
    return result
