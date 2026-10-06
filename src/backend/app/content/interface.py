"""Public interface of app.content: the only module other components may import.

Media URLs (Interface card #13): signed links to clue and feedback audio, for the game
responses (`current-location`, `navigation/ping`, `verification/photo`). These are stubs: they
return a correctly signed link for any valid input and do not read the database yet. The real
versions will look up `audio_assets.file_path` and `feedback_audio.file_path` and raise
`MediaNotFoundError` when no clip exists (the stubs never raise it). The signatures will not
change. The lookup stays in this component and signs through StorageService; if the team later
adds the spec's AudioService (chosen by AUDIO_SERVICE_TYPE) in app/services, these functions
delegate to it.

`Language` and `FeedbackCode` are defined in app.models.enums and re-exported here.

Planned (Interface card #12): eligible locations and the content snapshot of a location. Agree
on each signature with its users before adding it here.
"""

from uuid import NAMESPACE_URL, UUID, uuid5

from app.models.enums import FeedbackCode, Language
from app.services.storage import get_storage_service

__all__ = [
    "FeedbackCode",
    "Language",
    "MediaNotFoundError",
    "clue_audio_url",
    "feedback_audio_url",
]


class MediaNotFoundError(Exception):
    """No clip exists for the requested feedback code and language, or clue audio asset."""


def feedback_audio_url(code: FeedbackCode, language: Language) -> str:
    """Signed, short-lived URL of the spoken phrase for `code` in `language`."""
    key = uuid5(NAMESPACE_URL, f"feedback/{code}/{language}")
    return get_storage_service().signed_url(str(key))


def clue_audio_url(audio_asset_id: UUID) -> str:
    """Signed, short-lived URL of the clue recording `audio_asset_id` (the session snapshot)."""
    return get_storage_service().signed_url(str(audio_asset_id))
