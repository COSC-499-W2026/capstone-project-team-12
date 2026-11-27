"""POST /games/{id}/verification/photo: Stage 2, the sign photo (A10; DFD 3.0 and process 2.6).

The router calls both components in turn, so neither imports the other: game locks and changes
the game (`game.photo`), verification checks and reads the photo and keeps its attempt record
(`verification.images`, `verify`, `attempts`). Two transactions, with OCR between them and no
database lock held while it runs (spec Stage 2, "Retries and concurrency"):

1. With an Idempotency-Key, first: the game is locked in any state and the file is read and
   checked; a key that already has a result returns that result's stored response. Then the
   current location is locked and checked, the file is read and checked (if not yet), the photo
   is re-encoded and saved, and the next attempt is reserved as `processing`. Committed.
2. OCR reads the photo (outside any transaction).
3. The location is locked again, the result is written to its attempt and counted by the game,
   and the response is stored for replays. Committed.

Anything that goes wrong after the reservation (the location left Stage 2 or the game ended
during OCR, the run took longer than the timeout, a server error) releases the attempt in a new
transaction: it isn't counted, and the same key can be sent again.

Proposed, not in the spec (A10 plan Q2, team to confirm): a location that left Stage 2 during OCR
(shuffled, A9) answers 409 STATE_CHANGED, as shuffle does; a run past the timeout answers 503
OCR_BUSY with Retry-After, since the server was too slow and the student may retry. With a key,
the file is checked (413, 415) before the key is looked up, because the lookup needs the file's
SHA-256; a refused file can't be the one first sent with the key, since that one was accepted.
Without a key, the game's state is checked first, as the spec orders it.

Still to come: the OCR queue (at most 2 reads at once, 503 OCR_BUSY when 10 wait) and fresh audio
links in a replayed response (plan Q4; a replay returns the stored body as it is). Known costs
until then: (1) the photo is re-encoded while the user and game are locked (about a second for a
large PNG), so that user's refresh or login waits; (2) a feedback phrase or clue recording
removed mid-game makes the audio lookup 404 after OCR, which drops the result and releases the
attempt, so the location can't be finished until the recording is back.
"""

from collections.abc import Collection
from dataclasses import dataclass
from hashlib import sha256
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.api.rate_limit import photo_rate_limit
from app.api.v1.game_actions import play
from app.api.v1.game_responses import FeedbackResponse, GameSummaryResponse, summary_response
from app.db import get_db
from app.errors import AppError, ErrorCode, error_responses
from app.game.current_location import Stage
from app.game.photo import (
    PhotoProgress,
    apply_photo_result,
    lock_photo_game,
    lock_photo_location,
    lock_photographed_location,
)
from app.game.progress import game_changed
from app.game.summary import GameSummary, get_summary
from app.models.accounts import User
from app.models.enums import FeedbackCode, LocationStatus, PhotoResult, SessionState
from app.models.game import GameSession
from app.models.verification import IDEMPOTENCY_KEY_PATTERN
from app.services.audio import get_audio_service
from app.verification.attempts import (
    PendingAttempt,
    discard,
    find_replay,
    record_result,
    reserve_attempt,
    save_response,
)
from app.verification.images import PreparedPhoto, prepare_photo, read_photo
from app.verification.matching import Decision, SignReading
from app.verification.verify import read_sign

# Retry-After of the 503 for a run past the timeout (A10 plan Q12, proposed)
OCR_RETRY_AFTER_SECONDS = 5

verification_router = APIRouter(prefix="/games", tags=["verification"])


class LocationResultResponse(BaseModel):
    status: LocationStatus  # completed or failed
    points_awarded: int


class EngagementResponse(BaseModel):
    """The student's points and progress in the game's language after the location finished."""

    total_points: int
    level: int
    level_up: bool
    streak_days: int
    challenges_completed: list[str]
    achievements_unlocked: list[str]


class NextLocationResponse(BaseModel):
    position: int
    stage: Stage
    clue_audio_url: str


class PhotoResponse(BaseModel):
    """A photo's result. Never the room number, the accepted answers or the OCR text."""

    result: PhotoResult
    attempt: int = Field(description="This photo's attempt number at the location.")
    attempts_remaining: int
    feedback: list[FeedbackResponse] = Field(description="The phrases to play, in order.")
    location_result: LocationResultResponse | None
    engagement: EngagementResponse | None
    next: NextLocationResponse | None = Field(description="The location the game moved on to.")
    summary: GameSummaryResponse | None = Field(
        description="The game summary (as GET /games/{id}/summary) once this photo completed "
        "the game; else null."
    )


@dataclass(frozen=True)
class ReservedUpload:
    """A new upload after the first transaction: its attempt and what OCR needs."""

    pending: PendingAttempt
    location_id: UUID
    accepted_answers: Collection[str]
    photo: PreparedPhoto


@verification_router.post(
    "/{game_id}/verification/photo",
    operation_id="uploadPhoto",
    response_model=PhotoResponse,
    dependencies=[Depends(photo_rate_limit)],
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        HTTPStatus.SERVICE_UNAVAILABLE,
    ),
)
def upload_photo(
    game_id: UUID,
    photo: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            pattern=IDEMPOTENCY_KEY_PATTERN,
            description="Optional; 1-128 visible ASCII characters, new for each photo. Send the "
            "same key again when retrying the same photo after a network error.",
        ),
    ] = None,
) -> PhotoResponse | JSONResponse:
    """Send a photo of the room sign for the current location, in Stage 2.

    `photo`: a JPEG or PNG of at most 5 MB (by default), recognised by its first bytes (not its
    name or type). It is turned upright, shrunk to at most 1600 px and read by OCR; the server
    then compares the sign with the room. `result` is `match` (the sign was read clearly),
    `unclear` (nearly, or not clearly enough: take another photo) or `no_match`.

    Every result uses one of the location's attempts (6 by default). A match completes the
    location (100 points); the last attempt without one fails it (0 points, VERIFY_EXHAUSTED).
    Either way the response has `location_result`, `engagement` and `next`, the new current
    location; after the last location the game is completed, `next` is null, `summary` is the
    game summary (the same body as GET /games/{id}/summary) and `feedback` ends with
    SESSION_COMPLETE. `summary` is null until then. A refused file (413, 415), a refused
    request (409, 503) or a server error uses no attempt.

    `Idempotency-Key`: sending a key again with the same file returns the first response
    unchanged and uses no attempt, also once the location or the game has finished; its audio
    links may have expired by then (fetch current-location for a new clue link). Without a key
    every upload is a new attempt.

    409 SESSION_NOT_ACTIVE for a paused game (resume first), a game that has ended, or one idle
    for the expiry limit (24 hours by default), which is expired; also when the game ended while
    the photo was being read. 409 INVALID_STAGE unless the current location is in Stage 2
    (confirm arrival first). 409 ATTEMPTS_EXHAUSTED if no attempt is left. 409
    UPLOAD_IN_PROGRESS (with Retry-After) while this key, or another photo of the location, is
    still being read. 409 STATE_CHANGED if the location was shuffled while the photo was being
    read. 503 OCR_BUSY (with Retry-After) if reading the photo took too long. 422
    IDEMPOTENCY_KEY_REUSED for a key already sent with another file, VALIDATION_ERROR for a
    malformed key. 413 FILE_TOO_LARGE over the size limit. 415 UNSUPPORTED_MEDIA_TYPE for a
    file that isn't a readable JPEG or PNG, or has more than 25 megapixels (by default). 404
    NOT_FOUND for another student's game. 403 FORBIDDEN_ROLE for an admin, ACCOUNT_DISABLED
    for a disabled account. 429 RATE_LIMITED over 10 uploads a minute (by default), refused
    files included.
    """
    reserved = play(lambda: _reserve(user.id, game_id, photo, idempotency_key, db), user, db)
    if isinstance(reserved, JSONResponse):
        return reserved
    try:
        reading = read_sign(reserved.photo, reserved.accepted_answers)
        return play(lambda: _apply(user.id, game_id, reserved, reading, db), user, db)
    except Exception:
        _release(reserved.pending, db)
        raise


def _reserve(
    student_id: UUID, game_id: UUID, photo: UploadFile, key: str | None, db: Session
) -> ReservedUpload | JSONResponse:
    """The first transaction: the replay of `key`, or the reserved attempt for a new upload."""
    data = None
    if key is not None:
        # the key is looked up before any state check (spec), and the lookup needs the file
        lock_photo_game(student_id, game_id, db)
        data = read_photo(photo.file)
        replayed = find_replay(game_id, key, sha256(data).hexdigest(), db)
        if replayed is not None:
            # stored with the result; only the purge job (not built yet) would clear it
            if replayed.response_json is None:  # pragma: no cover
                raise RuntimeError("A finished photo attempt has no stored response")
            return JSONResponse(replayed.response_json)
    location = lock_photo_location(student_id, game_id, db)
    if data is None:
        data = read_photo(photo.file)
    prepared = prepare_photo(data)
    pending = reserve_attempt(location, key, sha256(data).hexdigest(), prepared, db)
    return ReservedUpload(pending, location.id, list(location.accepted_answers_snapshot), prepared)


def _apply(
    student_id: UUID, game_id: UUID, reserved: ReservedUpload, reading: SignReading, db: Session
) -> PhotoResponse:
    """The second transaction: the result written to its attempt, counted, and stored."""
    target = lock_photographed_location(student_id, game_id, reserved.location_id, db)
    if target is None:
        # shuffled (or otherwise left Stage 2) during OCR: the result is dropped (A9)
        raise game_changed(db.get_one(GameSession, game_id))
    attempt = record_result(reserved.pending, reading, db)
    if attempt is None:
        # past the timeout: play rolls this transaction back, so `_release` releases the attempt
        # again and commits it (and any later read releases a run this old anyway)
        raise AppError(
            ErrorCode.OCR_BUSY,
            "Reading the photo took too long. Try again in a moment.",
            headers={"Retry-After": str(OCR_RETRY_AFTER_SECONDS)},
        )
    matched = reading.decision.result == PhotoResult.MATCH
    progress = apply_photo_result(target, matched, db)
    summary = None
    if progress.game_state == SessionState.COMPLETED:
        # the last location is finished, so the game is over: its summary, in this transaction
        summary = get_summary(student_id, game_id, db)
    response = _response(reading.decision, progress, summary, db)
    save_response(attempt, response.model_dump(mode="json"), db)
    return response


def _release(pending: PendingAttempt, db: Session) -> None:
    """Release an attempt whose upload got no result, in a new transaction, so it isn't
    counted. What the failed step wrote is rolled back first."""
    db.rollback()
    discard(pending, db)
    db.commit()


def _response(
    decision: Decision, progress: PhotoProgress, summary: GameSummary | None, db: Session
) -> PhotoResponse:
    finished = progress.status != LocationStatus.VERIFYING
    following = progress.next_location
    return PhotoResponse(
        result=decision.result,
        attempt=progress.attempts_used,
        attempts_remaining=progress.attempts_left,
        feedback=[
            FeedbackResponse(
                code=code,
                audio_url=get_audio_service().feedback_audio_url(code, progress.language, db),
            )
            for code in _feedback_codes(decision, progress)
        ],
        location_result=(
            LocationResultResponse(status=progress.status, points_awarded=progress.points_awarded)
            if finished
            else None
        ),
        engagement=(
            None
            if progress.engagement is None
            else EngagementResponse.model_validate(progress.engagement, from_attributes=True)
        ),
        next=(
            None
            if following is None
            else NextLocationResponse.model_validate(following, from_attributes=True)
        ),
        summary=None if summary is None else summary_response(summary),
    )


def _feedback_codes(decision: Decision, progress: PhotoProgress) -> list[FeedbackCode]:
    """The photo's phrase (VERIFY_EXHAUSTED alone when the last attempt failed the location),
    then SESSION_COMPLETE if that finished the game."""
    if progress.status == LocationStatus.FAILED:
        codes = [FeedbackCode.VERIFY_EXHAUSTED]
    else:
        codes = [decision.feedback_code]
    if progress.game_state == SessionState.COMPLETED:
        codes.append(FeedbackCode.SESSION_COMPLETE)
    return codes
