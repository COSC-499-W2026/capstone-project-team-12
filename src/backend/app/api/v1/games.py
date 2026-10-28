from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Self, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.db import get_db
from app.errors import AppError, ErrorCode, error_responses
from app.game.control import pause_game, quit_game, reset_game, resume_game
from app.game.start import start_game
from app.game.status import find_current_game, get_game
from app.models.accounts import User
from app.models.enums import (
    Difficulty,
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
