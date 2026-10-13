from collections.abc import Callable
from http import HTTPStatus
from itertools import product
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.enums import FeedbackCode, Language
from app.services.audio import (
    MediaNotFoundError,
    clue_audio_asset_id,
    clue_audio_url,
    feedback_audio_url,
)
from app.services.storage import get_storage_service

ALL_PHRASES = list(product(FeedbackCode, Language))

SplitUrl = Callable[[str], tuple[str, int, str]]


def verified_key(url: str, split_media_url: SplitUrl) -> str:
    key, expires, signature = split_media_url(url)
    get_storage_service().verify(key, expires, signature)
    return key


def is_random_uuid(key: str) -> bool:
    """A version 4 UUID plus ".mp3", so the key cannot be worked out from the clue or phrase."""
    stem = key.removesuffix(".mp3")
    parsed = UUID(stem)
    return key.endswith(".mp3") and str(parsed) == stem and parsed.version == 4


def test_every_feedback_phrase_has_its_own_signed_url_that_reveals_nothing(
    db: Session, split_media_url: SplitUrl
) -> None:
    keys = [
        verified_key(feedback_audio_url(code, language, db), split_media_url)
        for code, language in ALL_PHRASES
    ]
    assert len(set(keys)) == len(ALL_PHRASES)
    assert all(is_random_uuid(key) for key in keys)


def test_a_clue_keeps_its_recording_and_each_clue_has_its_own(db: Session) -> None:
    clue_ids = [uuid4() for _ in range(3)]
    asset_ids = [clue_audio_asset_id(clue_id, db) for clue_id in clue_ids]
    assert asset_ids == [clue_audio_asset_id(clue_id, db) for clue_id in clue_ids]
    assert len(set(asset_ids)) == len(clue_ids)


def test_every_chosen_recording_has_its_own_signed_url_that_reveals_nothing(
    db: Session, split_media_url: SplitUrl
) -> None:
    asset_ids = [clue_audio_asset_id(uuid4(), db) for _ in range(3)]
    keys = [verified_key(clue_audio_url(asset_id, db), split_media_url) for asset_id in asset_ids]
    assert len(set(keys)) == len(asset_ids)
    assert all(is_random_uuid(key) for key in keys)


def test_a_recording_always_has_the_same_key(db: Session, split_media_url: SplitUrl) -> None:
    """The stub keeps no state, so a snapshot made before a restart can still be played."""
    asset_id = clue_audio_asset_id(uuid4(), db)
    urls = [clue_audio_url(asset_id, db), clue_audio_url(asset_id, db)]
    assert len({verified_key(url, split_media_url) for url in urls}) == 1


def test_clue_audio_url_of_an_unknown_recording_is_not_found(db: Session) -> None:
    with pytest.raises(MediaNotFoundError) as refused:
        clue_audio_url(uuid4(), db)
    assert refused.value.status == HTTPStatus.NOT_FOUND


def test_audio_lookups_never_commit_flush_or_roll_back_the_callers_session(
    db: Session, transaction_calls: list[str]
) -> None:
    clue_audio_url(clue_audio_asset_id(uuid4(), db), db)
    for code, language in ALL_PHRASES:
        feedback_audio_url(code, language, db)
    assert transaction_calls == []
