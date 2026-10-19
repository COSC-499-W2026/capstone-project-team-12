"""AudioService: which recording a clue uses, and signed URLs for clue and feedback audio.

Media URLs (Interface card #13, agreed with game). The recording is chosen when a location is
drawn: content's `location_snapshot` calls `clue_audio_asset_id` and the ID goes into the
session location. During play, game only asks for a signed URL for that ID (or for a feedback
code and language), so replacing a recording never changes a game in progress. The URLs are
short-lived and signed by StorageService (app.services.storage).

The implementation is chosen by the AUDIO_SERVICE_TYPE setting: `get_audio_service` is the
spec's AudioServiceFactory and builds it once. Only `uploaded` exists; `hybrid` and
`ai_generated` are Future Work. Any other value fails settings validation, which runs when the
app is imported (app.db reads the settings), so a bad value stops the app before it starts.

A language is playable only when all its feedback phrases exist (`has_all_feedback_phrases`).
Content counts a language without them as having no eligible locations, so every game starts
with all its phrases in place. `feedback_audio_url` can then only fail if a phrase is removed
while a game is in progress.

UploadedAudioService is a stub: the audio tables do not exist yet, so it ignores `db`. Every clue
has a recording, with a fixed ID per clue, and every feedback phrase exists. The stub stores
nothing: IDs and storage keys are worked out from the secret key, so they survive a restart, and
an ID that `clue_audio_asset_id` did not hand out raises `MediaNotFoundError`. The real version
will read `audio_assets` and `feedback_audio` through `db`; the signatures will not change.
Every method only reads: it never commits, rolls back or explicitly flushes the caller's session.
"""

import hashlib
import hmac
from collections.abc import Callable
from functools import lru_cache
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import AudioServiceType, get_settings
from app.models.enums import FeedbackCode, Language

# Raised when no recording exists; re-exported because callers of AudioService catch it here.
from app.services.storage import MediaNotFoundError as MediaNotFoundError
from app.services.storage import get_storage_service


class AudioService(Protocol):
    """What every AUDIO_SERVICE_TYPE implementation provides."""

    def clue_audio_asset_id(self, clue_id: UUID, db: Session) -> UUID:
        """ID of the clue's primary recording, for the snapshot; `MediaNotFoundError` if none."""
        ...

    def clue_audio_url(self, audio_asset_id: UUID, db: Session) -> str:
        """Signed, short-lived URL of a snapshot's clue recording; `MediaNotFoundError` if none."""
        ...

    def feedback_audio_url(self, code: FeedbackCode, language: Language, db: Session) -> str:
        """Signed, short-lived URL of the phrase `code` in `language`; `MediaNotFoundError` if
        none, e.g. a phrase removed during a game (see the module docstring)."""
        ...

    def has_all_feedback_phrases(self, language: Language, db: Session) -> bool:
        """True when every `FeedbackCode` phrase exists in `language`."""
        ...


def _stub_digest(*parts: object) -> bytes:
    # Keyed with the secret key, so neither an ID nor a storage key leads back to its clue or
    # phrase.
    message = "/".join(("stub-audio", *map(str, parts))).encode()
    return hmac.new(get_settings().secret_key.encode(), message, hashlib.sha256).digest()


def _stub_tag(body: bytes) -> bytes:
    return _stub_digest("tag", body.hex())[:8]


def _stub_key(*parts: object) -> str:
    # Shaped like the keys StorageService.save makes: a random version 4 UUID plus ".mp3".
    return f"{UUID(bytes=_stub_digest('key', *parts)[:16], version=4)}.mp3"


class UploadedAudioService:
    """`uploaded`: a clue uses its primary uploaded recording (a stub, see the module docstring).

    Not a subclass of `AudioService`, so a missing method fails instead of inheriting the
    Protocol's empty body; mypy checks the match through `_IMPLEMENTATIONS`.
    """

    def clue_audio_asset_id(self, clue_id: UUID, db: Session) -> UUID:
        # The first half identifies the recording; the second half shows the stub handed it out.
        body = _stub_digest("asset", clue_id)[:8]
        return UUID(bytes=body + _stub_tag(body))

    def clue_audio_url(self, audio_asset_id: UUID, db: Session) -> str:
        body, tag = audio_asset_id.bytes[:8], audio_asset_id.bytes[8:]
        if not hmac.compare_digest(tag, _stub_tag(body)):
            raise MediaNotFoundError
        return get_storage_service().signed_url(_stub_key("clue", audio_asset_id))

    def feedback_audio_url(self, code: FeedbackCode, language: Language, db: Session) -> str:
        return get_storage_service().signed_url(_stub_key("feedback", code, language))

    def has_all_feedback_phrases(self, language: Language, db: Session) -> bool:
        # The real version compares the language's `feedback_audio` rows with `FeedbackCode`.
        return True


_IMPLEMENTATIONS: dict[AudioServiceType, Callable[[], AudioService]] = {
    "uploaded": UploadedAudioService,
}


@lru_cache
def get_audio_service() -> AudioService:
    """The AudioServiceFactory: the implementation AUDIO_SERVICE_TYPE names, built once."""
    return _IMPLEMENTATIONS[get_settings().audio_service_type]()
