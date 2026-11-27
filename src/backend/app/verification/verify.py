"""Reads a sign photo and checks it against a location's accepted answers (DFD 3.4 and 3.5).

The photo pipeline of the spec (Game Flow > Stage 2: Verification): the upload is read and checked
(`images.read_photo`), re-encoded and enhanced (`images.prepare_photo`), read by OCR
(`ocr.read_text`) and judged by the decision table (`matching.decide`). `read_sign` is the last two
steps; the route runs it after reserving the attempt and committing, so no lock is held during OCR.

Second pass (proposed, not in the spec, team to confirm; see `ocr`): when the first read is not a
MATCH but found a candidate, the CLAHE copy is read too and the better decision is kept, so a
photo costs one attempt however many reads it takes. A first read with no candidate at all is not
read again: on the 82 campus photos, every photo the second read turned into a MATCH already had
a candidate in the first read, and a second read doubles the OCR time.

OCR takes seconds on CPU: call `read_sign` from a plain `def` route (FastAPI's thread pool), never
from the event loop.
"""

from collections.abc import Collection, Sequence
from time import perf_counter

from app.models.enums import PhotoResult
from app.verification.images import PreparedPhoto
from app.verification.matching import MatchThresholds, SignReading, TextBox, better, decide
from app.verification.ocr import local_contrast, read_text

MS_PER_SECOND = 1000


def read_sign(photo: PreparedPhoto, accepted_answers: Collection[str]) -> SignReading:
    """Read the sign in a re-encoded photo by OCR and judge it against `accepted_answers`
    (normalised, "SCI333")."""
    started = perf_counter()
    thresholds = MatchThresholds.from_settings()
    reads = [read_text(photo.enhanced)]
    decision = decide(reads[0], accepted_answers, thresholds)
    if decision.result != PhotoResult.MATCH and decision.candidate is not None:
        reads.append(read_text(local_contrast(photo.enhanced)))
        second = decide(reads[1], accepted_answers, thresholds)
        decision = better(decision, second, accepted_answers)
    elapsed_ms = round((perf_counter() - started) * MS_PER_SECOND)
    return SignReading(decision, _raw_text(reads), elapsed_ms)


def _raw_text(reads: Sequence[Sequence[TextBox]]) -> str:
    # OCR's character set has no line break (`ocr.ALLOWLIST`), so each line is exactly one read
    return "\n".join(" ".join(box.text for box in boxes) for boxes in reads)
