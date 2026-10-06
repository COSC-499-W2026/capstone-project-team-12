from collections.abc import Callable
from uuid import UUID, uuid4

from app.content.interface import FeedbackCode, Language, clue_audio_url, feedback_audio_url
from app.services.storage import get_storage_service

SplitUrl = Callable[[str], tuple[str, int, str]]


def verified_key(url: str, split_media_url: SplitUrl) -> str:
    key, expires, signature = split_media_url(url)
    get_storage_service().verify(key, expires, signature)
    return key


def test_every_feedback_clip_has_its_own_signed_url_that_reveals_nothing(
    split_media_url: SplitUrl,
) -> None:
    keys = [
        verified_key(feedback_audio_url(code, language), split_media_url)
        for code in FeedbackCode
        for language in Language
    ]
    assert len(set(keys)) == len(FeedbackCode) * len(Language)
    assert all(str(UUID(key)) == key for key in keys)


def test_clue_audio_url_is_signed_for_the_asset(split_media_url: SplitUrl) -> None:
    asset_id = uuid4()
    assert verified_key(clue_audio_url(asset_id), split_media_url) == str(asset_id)
