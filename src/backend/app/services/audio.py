"""AudioService: which recording a clue uses, and signed URLs for clue and feedback audio.

Media URLs (Interface card #13, agreed with game). The recording is chosen when a location is
drawn: content's `location_snapshot` calls `clue_audio_asset_id` and the ID goes into the
session location. During play, game only asks for a signed URL for that ID (or for a feedback
code and language), so replacing a recording never changes a game in progress. The URLs are
short-lived and signed by StorageService (app.services.storage).

These are stubs: the audio tables do not exist yet, so they ignore `db`. Every clue has a
recording, with a fixed ID per clue, and every feedback phrase exists. The stub stores nothing:
IDs and storage keys are worked out from the secret key, so they survive a restart, and an ID
that `clue_audio_asset_id` did not hand out raises `MediaNotFoundError`. The real versions will
read `audio_assets` and `feedback_audio` through `db` and raise `MediaNotFoundError` when no clip
exists; the signatures will not change. Every function here only reads: it never commits, rolls
back or explicitly flushes the caller's session.
"""

import hashlib
import hmac
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.models.enums import FeedbackCode, Language
from app.services.storage import get_storage_service


class MediaNotFoundError(AppError):
    """No recording exists for the clue, the audio asset, or the feedback code and language.

    The client gets 404 NOT_FOUND.
    """

    def __init__(self) -> None:
        super().__init__(ErrorCode.NOT_FOUND, "Recording not found")


def _stub_digest(*parts: object) -> bytes:
    # Keyed with the secret key, so neither an ID nor a storage key leads back to its clue or
    # phrase.
    message = "/".join(("stub-audio", *map(str, parts))).encode()
    return hmac.new(get_settings().secret_key.encode(), message, hashlib.sha256).digest()


def _stub_tag(body: bytes) -> bytes:
    return _stub_digest("tag", body.hex())[:8]


def _stub_key(*parts: object) -> str:
    # Shaped like the real keys, which are random version 4 UUIDs.
    return str(UUID(bytes=_stub_digest("key", *parts)[:16], version=4))


def clue_audio_asset_id(clue_id: UUID, db: Session) -> UUID:
    """ID of the clue's primary recording, for the snapshot; `MediaNotFoundError` if none.

    The stub gives every clue a recording, so it never raises.
    """
    # The first half identifies the recording; the second half shows the stub handed it out.
    body = _stub_digest("asset", clue_id)[:8]
    return UUID(bytes=body + _stub_tag(body))


def clue_audio_url(audio_asset_id: UUID, db: Session) -> str:
    """Signed, short-lived URL of a snapshot's clue recording; `MediaNotFoundError` if none."""
    body, tag = audio_asset_id.bytes[:8], audio_asset_id.bytes[8:]
    if not hmac.compare_digest(tag, _stub_tag(body)):
        raise MediaNotFoundError
    return get_storage_service().signed_url(_stub_key("clue", audio_asset_id))


def feedback_audio_url(code: FeedbackCode, language: Language, db: Session) -> str:
    """Signed, short-lived URL of the phrase `code` in `language`; `MediaNotFoundError` if none.

    The stub has every phrase, so it never raises; the real version raises when a language's
    phrases have not been uploaded yet.
    """
    return get_storage_service().signed_url(_stub_key("feedback", code, language))
