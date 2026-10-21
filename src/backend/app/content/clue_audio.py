"""5.3 Upload clue audio (A20): store a clue recording and choose which recording is primary.

The primary recording is the one new games draw (AudioService). An upload becomes primary when
the clue has none yet; otherwise it replaces the primary only when asked. Recordings are never
deleted here: an open game's snapshot may still point at the old one, so replacing a recording
only moves `is_primary`.

The file is stored before the transaction commits. It is deleted again if its row cannot be
saved here, but a transaction rolled back later (by the caller) leaves an unused file behind:
the spec has no cleanup job yet.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.audio_files import check_audio
from app.content.locations import get_location, get_or_create_clue
from app.models.content import AudioAsset
from app.models.enums import AudioSourceType, Difficulty, Language
from app.services.storage import get_storage_service

UPLOAD_PROVIDER = "teacher"  # audio_assets.provider for recordings uploaded by staff


def add_clue_recording(
    location_id: UUID,
    difficulty: Difficulty,
    language: Language,
    data: bytes,
    uploaded_by: UUID,
    db: Session,
    make_primary: bool = False,
) -> AudioAsset:
    """Store the recording `data` for the clue, creating the clue if needed.

    It becomes the primary recording if the clue has none yet or `make_primary` is set.
    AppError: NOT_FOUND for an unknown location (checked before the file is), and the audio
    checks' FILE_TOO_LARGE, UNSUPPORTED_MEDIA_TYPE or AUDIO_TOO_LONG. Flushes, never commits.
    """
    get_location(location_id, db)
    checked = check_audio(data)
    # locked, so two uploads to the clue at once cannot both find no primary and take it
    clue = get_or_create_clue(location_id, difficulty, language, db, lock=True)
    current = db.scalars(
        select(AudioAsset).where(AudioAsset.clue_id == clue.id, AudioAsset.is_primary)
    ).one_or_none()
    if make_primary and current is not None:
        current.is_primary = False
        db.flush()  # before the new primary: the one-primary index is checked row by row
    storage = get_storage_service()
    key = storage.save(checked.data, checked.suffix)
    asset = AudioAsset(
        clue_id=clue.id,
        file_path=key,
        source_type=AudioSourceType.UPLOADED,
        provider=UPLOAD_PROVIDER,
        is_primary=make_primary or current is None,
        duration_seconds=checked.duration_seconds,
        file_size_kb=checked.size_kb,
        uploaded_by=uploaded_by,
    )
    db.add(asset)
    try:
        db.flush()
    except Exception:
        storage.delete(key)  # no row points at it
        raise
    return asset
