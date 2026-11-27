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
while a game is in progress. The rule lives only here: content's coverage report lists each
language's `missing_feedback_codes`, so the report and the draw cannot disagree.

UploadedAudioService reads clue recordings from `audio_assets` and feedback phrases from
`feedback_audio` through `db`; a URL signs the row's own storage key, which is random and never
derived from the clue. Every method only reads: it never commits, rolls back or explicitly
flushes the caller's session.
"""

from collections.abc import Callable
from functools import lru_cache
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AudioServiceType, get_settings
from app.models.content import AudioAsset, FeedbackAudio
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

    def missing_feedback_codes(self, db: Session) -> dict[Language, list[FeedbackCode]]:
        """For every language, the `FeedbackCode` phrases it lacks, in `FeedbackCode` order."""
        ...

    def has_all_feedback_phrases(self, language: Language, db: Session) -> bool:
        """True when every `FeedbackCode` phrase exists in `language`."""
        ...


class UploadedAudioService:
    """`uploaded`: a clue uses its primary uploaded recording, and a feedback phrase its uploaded
    file.

    Not a subclass of `AudioService`, so a missing method fails instead of inheriting the
    Protocol's empty body; mypy checks the match through `_IMPLEMENTATIONS`.
    """

    def clue_audio_asset_id(self, clue_id: UUID, db: Session) -> UUID:
        asset_id = db.scalar(
            select(AudioAsset.id).where(AudioAsset.clue_id == clue_id, AudioAsset.is_primary)
        )
        if asset_id is None:
            raise MediaNotFoundError
        return asset_id

    def clue_audio_url(self, audio_asset_id: UUID, db: Session) -> str:
        # primary or not: a game keeps the recording its snapshot names after the primary moves
        file_path = db.scalar(select(AudioAsset.file_path).where(AudioAsset.id == audio_asset_id))
        if file_path is None:
            raise MediaNotFoundError
        return get_storage_service().signed_url(file_path)

    def feedback_audio_url(self, code: FeedbackCode, language: Language, db: Session) -> str:
        file_path = db.scalar(
            select(FeedbackAudio.file_path).where(
                FeedbackAudio.code == code, FeedbackAudio.language == language
            )
        )
        if file_path is None:
            raise MediaNotFoundError
        return get_storage_service().signed_url(file_path)

    def missing_feedback_codes(self, db: Session) -> dict[Language, list[FeedbackCode]]:
        # one query for every language, so a report shows them all as of the same read
        rows = db.execute(select(FeedbackAudio.language, FeedbackAudio.code))
        recorded = {(language, code) for language, code in rows}
        return {
            language: [code for code in FeedbackCode if (language, code) not in recorded]
            for language in Language
        }

    def has_all_feedback_phrases(self, language: Language, db: Session) -> bool:
        return not self.missing_feedback_codes(db)[language]


_IMPLEMENTATIONS: dict[AudioServiceType, Callable[[], AudioService]] = {
    "uploaded": UploadedAudioService,
}


@lru_cache
def get_audio_service() -> AudioService:
    """The AudioServiceFactory: the implementation AUDIO_SERVICE_TYPE names, built once."""
    return _IMPLEMENTATIONS[get_settings().audio_service_type]()
