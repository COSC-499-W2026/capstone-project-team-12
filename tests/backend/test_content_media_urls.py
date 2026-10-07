from collections.abc import Callable
from itertools import product
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.content.interface import (
    Difficulty,
    FeedbackCode,
    Language,
    MediaNotFoundError,
    clue_audio_url,
    eligible_location_ids,
    feedback_audio_url,
    location_snapshot,
)
from app.services.storage import get_storage_service

ALL_PHRASES = list(product(FeedbackCode, Language))
ALL_MODES = list(product(Language, Difficulty))

SplitUrl = Callable[[str], tuple[str, int, str]]


def verified_key(url: str, split_media_url: SplitUrl) -> str:
    key, expires, signature = split_media_url(url)
    get_storage_service().verify(key, expires, signature)
    return key


def snapshot_audio_asset_id(db: Session) -> UUID:
    location_id = next(iter(eligible_location_ids(db, Language.JA, Difficulty.BASIC)))
    return location_snapshot(db, location_id, Language.JA, Difficulty.BASIC).audio_asset_id


def test_every_feedback_phrase_has_its_own_signed_url_that_reveals_nothing(
    db: Session, split_media_url: SplitUrl
) -> None:
    keys = [
        verified_key(feedback_audio_url(db, code, language), split_media_url)
        for code, language in ALL_PHRASES
    ]
    assert len(set(keys)) == len(ALL_PHRASES)
    assert all(str(UUID(key)) == key for key in keys)


def test_every_snapshot_recording_has_its_own_signed_url(
    db: Session, split_media_url: SplitUrl
) -> None:
    location_id = next(iter(eligible_location_ids(db, Language.JA, Difficulty.BASIC)))
    asset_ids = [location_snapshot(db, location_id, *mode).audio_asset_id for mode in ALL_MODES]
    keys = [verified_key(clue_audio_url(db, asset_id), split_media_url) for asset_id in asset_ids]
    assert len(set(keys)) == len(asset_ids)
    assert all(str(UUID(key)) == key for key in keys)


def test_clue_audio_url_of_an_unknown_recording_is_refused(db: Session) -> None:
    with pytest.raises(MediaNotFoundError):
        clue_audio_url(db, uuid4())


def test_media_lookups_never_commit_flush_or_roll_back_the_callers_session(
    db: Session, transaction_calls: list[str]
) -> None:
    for code, language in ALL_PHRASES:
        feedback_audio_url(db, code, language)
    clue_audio_url(db, snapshot_audio_asset_id(db))
    assert transaction_calls == []
