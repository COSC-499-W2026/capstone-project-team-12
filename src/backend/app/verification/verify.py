"""Checks a sign photo against a location's accepted answers (DFD 3.2 to 3.5).

The photo pipeline of the spec (Game Flow > Stage 2: Verification), without its attempt records:
the upload is read and checked (`images.read_photo`), re-encoded and enhanced
(`images.prepare_photo`), read by OCR (`ocr.read_text`) and judged by the decision table
(`matching.decide`).

Second pass (proposed, not in the spec, team to confirm; see `ocr`): when the first read is not a
MATCH but found a candidate, the CLAHE copy is read too and the better decision is kept, so a
photo costs one attempt however many reads it takes. A first read with no candidate at all is not
read again: on the 82 campus photos, every photo the second read turned into a MATCH already had
a candidate in the first read, and a second read doubles the OCR time.

OCR takes seconds on CPU: call `verify_photo` from a plain `def` route (FastAPI's thread pool),
never from the event loop.
"""

from collections.abc import Collection
from typing import IO

from app.models.enums import PhotoResult
from app.verification.images import prepare_photo, read_photo
from app.verification.matching import Decision, MatchThresholds, better, decide
from app.verification.ocr import local_contrast, read_text


def verify_photo(upload: IO[bytes], accepted_answers: Collection[str]) -> Decision:
    """The decision on the photo in `upload` for a location with `accepted_answers` (normalised,
    "SCI333"). AppError 413 FILE_TOO_LARGE or 415 UNSUPPORTED_MEDIA_TYPE for a file that is not a
    photo the pipeline accepts (see `images`); nothing is read by OCR then."""
    photo = prepare_photo(read_photo(upload))
    thresholds = MatchThresholds.from_settings()
    first = decide(read_text(photo.enhanced), accepted_answers, thresholds)
    if first.result == PhotoResult.MATCH or first.candidate is None:
        return first
    second = decide(read_text(local_contrast(photo.enhanced)), accepted_answers, thresholds)
    return better(first, second, accepted_answers)
