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
from app.models.content import AudioAsset, Clue, FeedbackAudio
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


def test_a_clue_s_recording_is_its_primary_one(
    db: Session,
    audio: AudioService,
    make_clue: Callable[..., Clue],
    make_audio_asset: Callable[..., AudioAsset],
) -> None:
    clues = [make_clue(), make_clue()]
    primaries = []
    for clue in clues:  # recordings that aren't primary before and after it
        make_audio_asset(clue)
        primaries.append(make_audio_asset(clue, is_primary=True))
        make_audio_asset(clue)
    assert [audio.clue_audio_asset_id(clue.id, db) for clue in clues] == [
        primary.id for primary in primaries
    ]


@pytest.mark.parametrize("recordings", [0, 1], ids=["no recording", "one not primary"])
def test_a_clue_without_a_primary_recording_has_none(
    db: Session,
    audio: AudioService,
    make_clue: Callable[..., Clue],
    make_audio_asset: Callable[..., AudioAsset],
    recordings: int,
) -> None:
    clue = make_clue()
    for _ in range(recordings):
        make_audio_asset(clue)
    with pytest.raises(MediaNotFoundError) as refused:
        audio.clue_audio_asset_id(clue.id, db)
    assert refused.value.status == HTTPStatus.NOT_FOUND


def test_a_recording_url_is_a_signed_link_to_its_file_even_after_the_primary_moves(
    db: Session,
    audio: AudioService,
    make_audio_asset: Callable[..., AudioAsset],
    split_media_url: SplitUrl,
) -> None:
    """A game keeps playing the recording its snapshot names when a new one becomes primary."""
    old = make_audio_asset(is_primary=True)
    old.is_primary = False
    db.flush()  # before the new primary, see AudioAsset
    new = make_audio_asset(clue_id=old.clue_id, is_primary=True)

    urls = [audio.clue_audio_url(recording.id, db) for recording in (old, new)]
    # the key is the row's own random file name, never one derived from the clue
    assert [verified_key(url, split_media_url) for url in urls] == [old.file_path, new.file_path]


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
    make_audio_asset: Callable[..., AudioAsset],
    transaction_calls: list[str],
) -> None:
    clue_id = make_audio_asset(is_primary=True).clue_id
    for language in Language:
        feedback_phrases(language)
    transaction_calls.clear()  # adding the rows flushed

    audio.clue_audio_url(audio.clue_audio_asset_id(clue_id, db), db)
    for code, language in ALL_PHRASES:
        audio.feedback_audio_url(code, language, db)
    for language in Language:
        audio.has_all_feedback_phrases(language, db)
    audio.missing_feedback_codes(db)
    assert transaction_calls == []
