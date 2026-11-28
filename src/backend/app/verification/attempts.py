"""Photo attempt records: Idempotency-Key replays and reserving an attempt (A10; spec Stage 2:
Verification, "Retries and concurrency" and "Key scope"; DFD 3.0 process 3.1, store D6).

The photo route calls these in its first transaction, with the student's game locked, and
commits before OCR starts, so no database lock is held while the photo is read:

1. `find_replay`, for an upload with an Idempotency-Key, before any status or attempt check:
   a key whose run finished gives back that attempt, so its stored response can be returned
   without using another attempt.
2. `reserve_attempt`, for a new upload: reserves the next attempt as `processing`, so two
   uploads can never take the same attempt, and saves the photo.

Locks: the caller locks the game first, so one game's uploads pass through here one at a time.
Under it, `find_replay` locks the attempt it finds (the key check comes before the location is
locked), and `reserve_attempt` locks the location's running attempt after the caller locked the
location. Any other writer of photo_attempts must take the game lock first too, or lock only
photo_attempts rows, never a session location after them. Nothing here commits.

A run still `processing` after `photo_processing_timeout_seconds` (for example after a restart)
is stale: it becomes `error` the next time it is read here, which frees its attempt number and
key (the unique indexes leave `error` rows out), and the retry inserts a new row. A late result
of that run is dropped: a result is only written to a row that is still `processing` with the
run's job_id.

Proposed, not in the spec (team to confirm):

- A run is stale once it is the timeout old (120 s by default), not only after it.
- For a known key the checks run in this order: stale run (a new upload), another file (422
  IDEMPOTENCY_KEY_REUSED), still processing (409 UPLOAD_IN_PROGRESS), then the replay.
- A key is looked up across all the game's locations, shuffled ones included: the request names
  no location, and after a MATCH the game has already moved on. If two locations used the key
  (the spec scopes a key to one location), the newest attempt is the replay.
- Only one photo per location is read at a time (A10 plan Q3): a second upload while one is
  being read gets 409 UPLOAD_IN_PROGRESS, whatever its key.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.models.enums import PhotoAttemptStatus
from app.models.game import SessionLocation
from app.models.verification import PhotoAttempt
from app.services.storage import get_storage_service
from app.verification.images import PreparedPhoto

# spec, "Retries and concurrency": a key still processing returns 409 with Retry-After: 2
UPLOAD_RETRY_AFTER_SECONDS = 2


@dataclass(frozen=True)
class PendingAttempt:
    """A reserved attempt, for the OCR run that owns it. Still valid after the commit."""

    id: UUID
    # the run writes its result only while the row is still `processing` with this job_id; a
    # released (stale) row is `error`, so its run's late result is dropped
    job_id: UUID
    attempt_no: int


def find_replay(game_id: UUID, key: str, content_sha256: str, db: Session) -> PhotoAttempt | None:
    """The finished attempt this upload repeats, or None if it is a new upload.

    `key` is the Idempotency-Key and `content_sha256` the SHA-256 (hex) of the uploaded file.
    The caller has locked the student's game. The attempt is returned whatever happened to its
    location or game since (completed, failed, shuffled or ended); its stored response is what
    the student gets again, and no attempt is used.

    422 IDEMPOTENCY_KEY_REUSED if the key came with another file; 409 UPLOAD_IN_PROGRESS (with
    Retry-After) while that upload is still being read. A stale run is marked `error` and
    flushed, and the upload goes on as a new one. Doesn't commit.
    """
    attempt = db.scalar(
        select(PhotoAttempt)
        .join(SessionLocation, SessionLocation.id == PhotoAttempt.session_location_id)
        .where(
            SessionLocation.session_id == game_id,
            PhotoAttempt.idempotency_key == key,
            PhotoAttempt.status != PhotoAttemptStatus.ERROR,
        )
        .order_by(PhotoAttempt.created_at.desc())
        .limit(1)
        .with_for_update(of=PhotoAttempt)
        .execution_options(populate_existing=True)
    )
    if attempt is None or _release_if_stale(attempt, db):
        return None
    if attempt.content_sha256 != content_sha256:
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            "This Idempotency-Key was already used for another photo",
        )
    if attempt.status == PhotoAttemptStatus.PROCESSING:
        raise _upload_in_progress()
    return attempt


def reserve_attempt(
    location: SessionLocation,
    key: str | None,
    content_sha256: str,
    photo: PreparedPhoto,
    db: Session,
) -> PendingAttempt:
    """Reserve the location's next attempt for a new photo, as `processing`, and save the photo.

    For a new upload only: the caller has asked `find_replay` about the key, if any, and has
    locked the game and `location` and checked that the location is in Stage 2 with an attempt
    left (game.photo.lock_photo_location). The attempt number is attempts_used + 1, because a
    result is written to its attempt and counted in attempts_used in one transaction, and no
    other upload is still being read. The caller has checked the key's format (1-128 visible
    ASCII characters, the table's CHECK).

    409 UPLOAD_IN_PROGRESS (with Retry-After) while another photo of the location is being read;
    nothing is saved then. A stale run of the location is marked `error` first, whatever its
    key. The row is flushed before the photo is stored, so a row the database refuses leaves no
    file. Flushed; doesn't commit, so the caller commits before OCR starts. A transaction rolled
    back after this leaves the photo file in storage.
    """
    running = db.scalar(
        select(PhotoAttempt)
        .where(
            PhotoAttempt.session_location_id == location.id,
            PhotoAttempt.status == PhotoAttemptStatus.PROCESSING,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if running is not None and not _release_if_stale(running, db):
        raise _upload_in_progress()
    attempt = PhotoAttempt(
        session_location_id=location.id,
        attempt_no=location.attempts_used + 1,
        idempotency_key=key,
        content_sha256=content_sha256,
        job_id=uuid4(),
        has_camera_exif=photo.has_camera_exif,
    )
    db.add(attempt)
    db.flush()
    attempt.photo_path = get_storage_service().save(photo.data, ".jpg")
    db.flush()
    return PendingAttempt(attempt.id, attempt.job_id, attempt.attempt_no)


def _release_if_stale(attempt: PhotoAttempt, db: Session) -> bool:
    """Mark a run that is still processing after the timeout `error`, flushed; True if so.

    Flushed at once because the app's sessions don't autoflush: a later query that reloads the
    row (populate_existing) would otherwise read it back as `processing`.
    """
    timeout = timedelta(seconds=get_settings().photo_processing_timeout_seconds)
    if attempt.status != PhotoAttemptStatus.PROCESSING:
        return False
    if datetime.now(UTC) - attempt.created_at < timeout:
        return False
    attempt.status = PhotoAttemptStatus.ERROR
    db.flush()
    return True


def _upload_in_progress() -> AppError:
    return AppError(
        ErrorCode.UPLOAD_IN_PROGRESS,
        "A photo of this location is still being read. Try again in a moment.",
        headers={"Retry-After": str(UPLOAD_RETRY_AFTER_SECONDS)},
    )
