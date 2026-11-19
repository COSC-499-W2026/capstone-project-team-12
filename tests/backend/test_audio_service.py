from collections.abc import Callable
from http import HTTPStatus
from itertools import product
from typing import get_args
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import AudioServiceType, Settings
from app.models.content import FeedbackAudio
from app.models.enums import Difficulty, FeedbackCode, Language
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
    """A version 4 UUID plus ".mp3", so the key cannot be worked out from the clue."""
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


def test_a_feedback_phrase_url_is_a_signed_link_to_that_phrase_s_file(
    db: Session,
    audio: AudioService,
    make_feedback_audio: Callable[..., FeedbackAudio],
    split_media_url: SplitUrl,
) -> None:
    # the same code in two languages, and two codes in one language
    phrases = [
        make_feedback_audio(FeedbackCode.NAV_ARRIVED, Language.KO),
        make_feedback_audio(FeedbackCode.NAV_ARRIVED, Language.FR),
        make_feedback_audio(FeedbackCode.VERIFY_START, Language.KO),
    ]
    urls = [audio.feedback_audio_url(phrase.code, phrase.language, db) for phrase in phrases]
    assert [verified_key(url, split_media_url) for url in urls] == [
        phrase.file_path for phrase in phrases
    ]


@pytest.mark.parametrize("language", Language)
def test_a_language_with_every_phrase_has_them_all(
    db: Session,
    audio: AudioService,
    feedback_phrases: Callable[[Language], None],
    language: Language,
) -> None:
    feedback_phrases(language)
    assert audio.has_all_feedback_phrases(language, db)


def test_a_language_without_one_phrase_lacks_it(
    db: Session, audio: AudioService, feedback_phrases: Callable[[Language], None]
) -> None:
    missing = FeedbackCode.NAV_GPS_WEAK
    feedback_phrases(Language.KO)
    feedback_phrases(Language.JA)
    db.execute(
        delete(FeedbackAudio).where(
            FeedbackAudio.code == missing, FeedbackAudio.language == Language.KO
        )
    )

    # the same phrase in another language does not count
    assert audio.missing_feedback_codes(db)[Language.KO] == [missing]
    assert audio.missing_feedback_codes(db)[Language.JA] == []
    assert not audio.has_all_feedback_phrases(Language.KO, db)
    with pytest.raises(MediaNotFoundError) as refused:
        audio.feedback_audio_url(missing, Language.KO, db)
    assert refused.value.status == HTTPStatus.NOT_FOUND
    assert audio.has_all_feedback_phrases(Language.JA, db)


def test_committed_phrases_are_refused_for_a_language_only_db_can_see(
    feedback_phrases: Callable[[Language], None],
    committed_location_ids: Callable[..., list[UUID]],
) -> None:
    feedback_phrases(Language.ES)  # uncommitted, in the `db` fixture's transaction
    with pytest.raises(pytest.fail.Exception, match="phrases that only it can see"):
        committed_location_ids(Language.ES, Difficulty.BASIC)
    committed_location_ids(Language.DE, Difficulty.BASIC)  # another language is fine


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
    db: Session,
    audio: AudioService,
    feedback_phrases: Callable[[Language], None],
    transaction_calls: list[str],
) -> None:
    for language in Language:
        feedback_phrases(language)
    transaction_calls.clear()  # adding the phrases flushed

    audio.clue_audio_url(audio.clue_audio_asset_id(uuid4(), db), db)
    for code, language in ALL_PHRASES:
        audio.feedback_audio_url(code, language, db)
    for language in Language:
        audio.has_all_feedback_phrases(language, db)
    audio.missing_feedback_codes(db)
    assert transaction_calls == []
