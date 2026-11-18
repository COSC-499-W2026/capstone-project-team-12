"""POST /games/{id}/verification/photo: Stage 2, the sign photo (A10; DFD 3.0 and process 2.6).

The router calls both components in turn, so neither imports the other: game locks the current
location (`lock_photo_location`), verification reads the photo and decides
(`verify_photo`), and game applies the result (`finish_photo_location`).

A first slice of the spec's pipeline (A10 plan, demo cut): one transaction, so OCR reads the photo
while the user, game and location rows are locked. Still to come with photo_attempts: attempt
records, the Idempotency-Key, committing before OCR (the two-phase flow), the OCR queue (503
OCR_BUSY) and the rate limit. Costs until then: (1) unlimited concurrent reads, each holding a
pooled connection (about 15 at once exhaust the pool); (2) the user row stays locked, so a refresh
or login waits; (3) a missing feedback phrase raises 404 after OCR and rolls the result back.
"""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.api.v1.game_actions import play
from app.api.v1.game_responses import FeedbackResponse, GameSummaryResponse, summary_response
from app.db import get_db
from app.errors import error_responses
from app.game.current_location import Stage
from app.game.photo import PhotoProgress, finish_photo_location, lock_photo_location
from app.game.summary import GameSummary, get_summary
from app.models.accounts import User
from app.models.enums import FeedbackCode, LocationStatus, PhotoResult, SessionState
from app.services.audio import get_audio_service
from app.verification.matching import Decision
from app.verification.verify import verify_photo

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


@verification_router.post(
    "/{game_id}/verification/photo",
    operation_id="uploadPhoto",
    response_model=PhotoResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    ),
)
def upload_photo(
    game_id: UUID,
    photo: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PhotoResponse:
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
    SESSION_COMPLETE. `summary` is null until then. A refused file (413, 415) or a server error
    uses no attempt.

    Not yet supported: the `Idempotency-Key` header, so a retried upload is a new attempt.

    409 SESSION_NOT_ACTIVE for a paused game (resume first), a game that has ended, or one idle
    for the expiry limit (24 hours by default), which is expired. 409 INVALID_STAGE unless the
    current location is in Stage 2 (confirm arrival first). 409 ATTEMPTS_EXHAUSTED if no attempt
    is left. 413 FILE_TOO_LARGE over the size limit. 415 UNSUPPORTED_MEDIA_TYPE for a file that
    isn't a readable JPEG or PNG, or has more than 25 megapixels (by default). 404 NOT_FOUND for
    another student's game. 403 FORBIDDEN_ROLE for an admin, ACCOUNT_DISABLED for a disabled
    account.
    """
    return play(lambda: _check_photo(user.id, game_id, photo, db), user, db)


def _check_photo(student_id: UUID, game_id: UUID, photo: UploadFile, db: Session) -> PhotoResponse:
    location = lock_photo_location(student_id, game_id, db)
    decision = verify_photo(photo.file, location.accepted_answers_snapshot)
    matched = decision.result == PhotoResult.MATCH
    progress = finish_photo_location(student_id, game_id, location.id, matched, db)
    # The game and the location stay locked from the first call to this one, so the location is
    # still in Stage 2: finish returns None only when another request changed it meanwhile.
    if progress is None:  # pragma: no cover (unreachable while the rows are locked)
        raise RuntimeError("The location left Stage 2 while it was locked")
    summary = None
    if progress.game_state == SessionState.COMPLETED:
        # the last location is finished, so the game is over: its summary, in this transaction
        summary = get_summary(student_id, game_id, db)
    return _response(decision, progress, summary, db)


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
