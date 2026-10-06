from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest

from app.content.interface import FeedbackCode, Language, clue_audio_url, feedback_audio_url
from app.services.storage import MEDIA_URL_PREFIX, MediaUrlError, get_storage_service


def verify_url(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    key = parts.path.removeprefix(f"{MEDIA_URL_PREFIX}/")
    get_storage_service().verify(key, int(query["expires"][0]), query["sig"][0])
    return key


def test_there_are_thirteen_codes_and_six_languages() -> None:
    assert len(FeedbackCode) == 13
    assert [language.value for language in Language] == ["ja", "zh", "fr", "de", "ko", "es"]


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("code", list(FeedbackCode))
def test_feedback_audio_url_is_signed_and_reveals_nothing(
    code: FeedbackCode, language: Language
) -> None:
    url = feedback_audio_url(code, language)
    key = verify_url(url)
    assert str(UUID(key)) == key
    assert code.value.lower() not in url.lower()


def test_feedback_audio_urls_differ_per_code_and_language() -> None:
    keys = {
        verify_url(feedback_audio_url(code, language))
        for code in FeedbackCode
        for language in Language
    }
    assert len(keys) == len(FeedbackCode) * len(Language)


def test_clue_audio_url_is_signed_for_the_asset() -> None:
    asset_id = uuid4()
    assert verify_url(clue_audio_url(asset_id)) == str(asset_id)


def test_changed_feedback_url_is_rejected() -> None:
    url = feedback_audio_url(FeedbackCode.NAV_START, Language.JA)
    with pytest.raises(MediaUrlError):
        verify_url(url.replace("sig=", "sig=0"))
