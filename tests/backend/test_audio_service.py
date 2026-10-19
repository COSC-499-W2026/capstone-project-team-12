from collections.abc import Callable
from http import HTTPStatus
from itertools import product
from typing import get_args
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import AudioServiceType, Settings
from app.models.enums import FeedbackCode, Language
from app.services import audio as audio_module
from app.services.audio import (
    AudioService,
    MediaNotFoundError,
    UploadedAudioService,
    get_audio_service,
)
from app.services.storage import get_storage_service

ALL_PHRASES = list(product(FeedbackCode, Language))

SplitUrl = Callable[[str], tuple[str, int, str]]


@pytest.fixture
def audio() -> AudioService:
    return get_audio_service()


def verified_key(url: str, split_media_url: SplitUrl) -> str:
    key, expires, signature = split_media_url(url)
    get_storage_service().verify(key, expires, signature)
    return key


def is_random_uuid(key: str) -> bool:
    """A version 4 UUID plus ".mp3", so the key cannot be worked out from the clue or phrase."""
    stem = key.removesuffix(".mp3")
    parsed = UUID(stem)
    return key.endswith(".mp3") and str(parsed) == stem and parsed.version == 4


def test_the_default_audio_service_is_the_uploaded_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDIO_SERVICE_TYPE", raising=False)
    assert Settings(_env_file=None).audio_service_type == "uploaded"
    assert isinstance(get_audio_service(), UploadedAudioService)


@pytest.mark.parametrize("service_type", ["hybrid", "ai_generated", ""])
def test_an_audio_service_type_that_does_not_exist_is_rejected(
    monkeypatch: pytest.MonkeyPatch, service_type: str
) -> None:
    monkeypatch.setenv("AUDIO_SERVICE_TYPE", service_type)
    with pytest.raises(ValidationError, match="audio_service_type"):
        Settings(_env_file=None)


def test_every_audio_service_type_has_an_implementation() -> None:
    """A new AUDIO_SERVICE_TYPE value without one would only fail when the factory runs."""
    assert set(get_args(AudioServiceType)) == set(audio_module._IMPLEMENTATIONS)


def test_get_audio_service_is_cached() -> None:
    assert get_audio_service() is get_audio_service()


def test_every_feedback_phrase_has_its_own_signed_url_that_reveals_nothing(
    db: Session, audio: AudioService, split_media_url: SplitUrl
) -> None:
    keys = [
        verified_key(audio.feedback_audio_url(code, language, db), split_media_url)
        for code, language in ALL_PHRASES
    ]
    assert len(set(keys)) == len(ALL_PHRASES)
    assert all(is_random_uuid(key) for key in keys)


@pytest.mark.parametrize("language", Language)
def test_the_stub_has_every_feedback_phrase_in_every_language(
    db: Session, audio: AudioService, language: Language
) -> None:
    assert audio.has_all_feedback_phrases(language, db)


def test_a_clue_keeps_its_recording_and_each_clue_has_its_own(
    db: Session, audio: AudioService
) -> None:
    clue_ids = [uuid4() for _ in range(3)]
    asset_ids = [audio.clue_audio_asset_id(clue_id, db) for clue_id in clue_ids]
    assert asset_ids == [audio.clue_audio_asset_id(clue_id, db) for clue_id in clue_ids]
    assert len(set(asset_ids)) == len(clue_ids)


def test_every_chosen_recording_has_its_own_signed_url_that_reveals_nothing(
    db: Session, audio: AudioService, split_media_url: SplitUrl
) -> None:
    asset_ids = [audio.clue_audio_asset_id(uuid4(), db) for _ in range(3)]
    urls = [audio.clue_audio_url(asset_id, db) for asset_id in asset_ids]
    keys = [verified_key(url, split_media_url) for url in urls]
    assert len(set(keys)) == len(asset_ids)
    assert all(is_random_uuid(key) for key in keys)


def test_a_recording_always_has_the_same_key(
    db: Session, audio: AudioService, split_media_url: SplitUrl
) -> None:
    """The stub keeps no state, so a snapshot made before a restart can still be played."""
    asset_id = audio.clue_audio_asset_id(uuid4(), db)
    urls = [audio.clue_audio_url(asset_id, db), audio.clue_audio_url(asset_id, db)]
    assert len({verified_key(url, split_media_url) for url in urls}) == 1


def test_clue_audio_url_of_an_unknown_recording_is_not_found(
    db: Session, audio: AudioService
) -> None:
    with pytest.raises(MediaNotFoundError) as refused:
        audio.clue_audio_url(uuid4(), db)
    assert refused.value.status == HTTPStatus.NOT_FOUND


def test_audio_lookups_never_commit_flush_or_roll_back_the_callers_session(
    db: Session, audio: AudioService, transaction_calls: list[str]
) -> None:
    audio.clue_audio_url(audio.clue_audio_asset_id(uuid4(), db), db)
    for code, language in ALL_PHRASES:
        audio.feedback_audio_url(code, language, db)
    for language in Language:
        audio.has_all_feedback_phrases(language, db)
    assert transaction_calls == []
