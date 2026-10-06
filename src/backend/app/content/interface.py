"""Public interface of app.content: the only module other components may import.

Media URLs (Interface card #13): signed links to clue and feedback audio, for the game
responses (`current-location`, `navigation/ping`, `verification/photo`). These are stubs: they
return a correctly signed link for any valid input and do not read the database yet. The real
versions will look up `audio_assets.file_path` and `feedback_audio.file_path` and raise
`app.services.storage.MediaNotFoundError` when no clip exists. The signatures will not change.

Planned (Interface card #12): eligible locations and the content snapshot of a location. Agree
on each signature with its users before adding it here.
"""

from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from app.services.storage import get_storage_service


class Language(StrEnum):
    JA = "ja"
    ZH = "zh"
    FR = "fr"
    DE = "de"
    KO = "ko"
    ES = "es"


class FeedbackCode(StrEnum):
    NAV_START = "NAV_START"
    NAV_GETTING_CLOSER = "NAV_GETTING_CLOSER"
    NAV_GETTING_FARTHER = "NAV_GETTING_FARTHER"
    NAV_KEEP_GOING = "NAV_KEEP_GOING"
    NAV_VERY_CLOSE = "NAV_VERY_CLOSE"
    NAV_ARRIVED = "NAV_ARRIVED"
    NAV_GPS_WEAK = "NAV_GPS_WEAK"
    VERIFY_START = "VERIFY_START"
    VERIFY_SUCCESS = "VERIFY_SUCCESS"
    VERIFY_UNCLEAR = "VERIFY_UNCLEAR"
    VERIFY_NO_MATCH = "VERIFY_NO_MATCH"
    VERIFY_EXHAUSTED = "VERIFY_EXHAUSTED"
    SESSION_COMPLETE = "SESSION_COMPLETE"


def feedback_audio_url(code: FeedbackCode, language: Language) -> str:
    """Signed, short-lived URL of the spoken phrase for `code` in `language`."""
    key = uuid5(NAMESPACE_URL, f"feedback/{code}/{language}")
    return get_storage_service().signed_url(str(key))


def clue_audio_url(audio_asset_id: UUID) -> str:
    """Signed, short-lived URL of the clue recording `audio_asset_id` (the session snapshot)."""
    return get_storage_service().signed_url(str(audio_asset_id))
