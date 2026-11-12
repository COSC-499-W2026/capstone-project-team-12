"""Room-number candidates from OCR text and the Stage 2 decision table (DFD 3.4 and 3.5).

Pure functions with no database and no OCR model, so the decision table is tested with fixed
OCR output (spec, Testing & Acceptance). Rules from the spec, Game Flow > Stage 2: Verification:

- Normalise: uppercase, remove spaces and hyphens, keep tokens of 3 letters and 3 or 4 digits.
  The building letters and the number may be read as separate boxes, so neighbouring boxes are
  also joined, in both orders: OCR sometimes lists the number's box before the letters' box.
- Decide on the best candidate: an accepted answer read with at least the match confidence is
  MATCH; an accepted answer from the unclear confidence up, a token one character away from an
  answer from the unclear confidence up, or an answer's number read without its building letters
  is UNCLEAR; anything else is NO_MATCH. The building letters are always needed for MATCH.

Spaces and hyphens separate words, and a token is a whole word or two neighbouring words joined
("ROOM SCI 333" gives SCI333). Searching the text with the separators already removed would take
the last three letters of any word, so "ROOM 333" would read as another building, OOM333.

Real signs may carry a letter after the number ("LIB 314W", "SCI 126A"), which accepted answers
cannot hold (3 letters and 3 or 4 digits), so one letter after the digits is left out: LIB314W is
the candidate LIB314. Rooms that differ only by that letter are then one location (a known limit,
for the team to decide). Digits are never cut off: ADM0267 is not ADM026.

Choices the spec leaves open (proposed, to be confirmed by the team): "one character away" is one
substitution, insertion or deletion (Levenshtein distance 1); a number read without building
letters needs the unclear confidence too, like the other UNCLEAR rows; building letters read
below the unclear confidence count as unreadable, so reading more of a sign never gives a worse
result than reading less; and a number read with other building letters ("LIB" and "333" for
SCI333) is that other building, so NO_MATCH, since the same number can exist in more than one
building.

Candidates hold OCR text: they are stored for statistics, never sent to the student. The text of
boxes and candidates is left out of `repr`, so logs and tracebacks never show a room number.
"""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from app.config import get_settings
from app.models.content import ANSWER_PATTERN
from app.models.enums import FeedbackCode, PhotoResult

BUILDING_LETTERS = 3  # every answer starts with its building code, e.g. "SCI" (spec, Locations)
SEPARATORS = re.compile(r"[ -]")
# An accepted answer's format and at most one letter after it: "SCI333", or LIB314 in "LIB314W".
ROOM_NUMBER = re.compile(rf"({ANSWER_PATTERN.strip('^$')})[A-Z]?")
# 3 or 4 digits ending a word, before at most one letter: "333" in "CI333", "314" in "314W".
NUMBER = re.compile(r"(?<![0-9])([0-9]{3,4})[A-Z]?$")

FEEDBACK = {
    PhotoResult.MATCH: FeedbackCode.VERIFY_SUCCESS,
    PhotoResult.UNCLEAR: FeedbackCode.VERIFY_UNCLEAR,
    PhotoResult.NO_MATCH: FeedbackCode.VERIFY_NO_MATCH,
}
RANK = {PhotoResult.NO_MATCH: 0, PhotoResult.UNCLEAR: 1, PhotoResult.MATCH: 2}


@dataclass(frozen=True)
class TextBox:
    """One piece of text OCR found in the photo, in the reader's order."""

    text: str = field(repr=False)
    confidence: float


@dataclass(frozen=True)
class Candidate:
    """Normalised text that may be the room number: "SCI333", or only "333" without a building."""

    text: str = field(repr=False)
    confidence: float


@dataclass(frozen=True)
class MatchThresholds:
    """The decision table's confidences, read once so a test can pass its own."""

    match: float
    unclear: float

    @classmethod
    def from_settings(cls) -> "MatchThresholds":
        settings = get_settings()
        return cls(match=settings.ocr_match_confidence, unclear=settings.ocr_unclear_confidence)


@dataclass(frozen=True)
class Decision:
    result: PhotoResult
    candidate: Candidate | None  # the best candidate; None when nothing readable was found

    @property
    def feedback_code(self) -> FeedbackCode:
        return FEEDBACK[self.result]


def decide(
    boxes: Sequence[TextBox], accepted_answers: Collection[str], thresholds: MatchThresholds
) -> Decision:
    """Apply the decision table to the OCR boxes of one photo.

    `accepted_answers` are the session location's snapshot, already normalised ("SCI333").
    The best candidate decides: a better result wins, then a higher confidence.
    """
    by_box = _words(boxes)
    words = [word for box in by_box for word in box]
    rooms = _room_numbers(words, by_box)
    answers = frozenset(accepted_answers)
    numbers = {answer[BUILDING_LETTERS:] for answer in answers}
    judged = [(_judge(room, answers, thresholds), room) for room in rooms]
    judged += [
        (PhotoResult.UNCLEAR, number)
        for number in _numbers_without_building(words, rooms, thresholds)
        if number.text in numbers and number.confidence >= thresholds.unclear
    ]
    if not judged:
        return Decision(PhotoResult.NO_MATCH, None)
    result, best = max(judged, key=lambda item: (RANK[item[0]], item[1].confidence))
    return Decision(result, best)


def _words(boxes: Sequence[TextBox]) -> list[list[Candidate]]:
    """The words of each box in reading order, uppercase, with their box's confidence.

    Boxes without a word are left out, so neighbouring boxes are the ones that hold words.
    """
    by_box = [
        [Candidate(word, box.confidence) for word in SEPARATORS.split(box.text.upper()) if word]
        for box in boxes
    ]
    return [words for words in by_box if words]


def _room_numbers(
    words: Sequence[Candidate], by_box: Sequence[Sequence[Candidate]]
) -> list[Candidate]:
    """Room numbers read as one word, or as two neighbouring words joined (lower confidence).

    Words are joined in reading order, and neighbouring boxes also the other way round: the last
    word of the later box before the first word of the earlier one, for a number read before its
    building letters ("1121-1", then "EME"). Only boxes are reversed, because a hyphen splits
    "1121-1" into two words, so the building letters are never the number's neighbouring word.
    """
    pairs = [*pairwise(words), *((later[-1], earlier[0]) for earlier, later in pairwise(by_box))]
    joined = [Candidate(a.text + b.text, min(a.confidence, b.confidence)) for a, b in pairs]
    return [
        Candidate(found.group(1), text.confidence)
        for text in [*words, *joined]
        if (found := ROOM_NUMBER.fullmatch(text.text))
    ]


def _numbers_without_building(
    words: Sequence[Candidate], rooms: Sequence[Candidate], thresholds: MatchThresholds
) -> list[Candidate]:
    """Numbers ending a word that no readable room number holds: building missing or unreadable."""
    with_building = {
        room.text[BUILDING_LETTERS:] for room in rooms if room.confidence >= thresholds.unclear
    }
    return [
        Candidate(match.group(1), word.confidence)
        for word in words
        if (match := NUMBER.search(word.text)) and match.group(1) not in with_building
    ]


def _judge(room: Candidate, answers: frozenset[str], thresholds: MatchThresholds) -> PhotoResult:
    if room.confidence < thresholds.unclear:
        return PhotoResult.NO_MATCH
    if room.text in answers:
        if room.confidence >= thresholds.match:
            return PhotoResult.MATCH
        return PhotoResult.UNCLEAR
    if any(_one_edit_apart(room.text, answer) for answer in answers):
        return PhotoResult.UNCLEAR
    return PhotoResult.NO_MATCH


def _one_edit_apart(first: str, second: str) -> bool:
    """True if exactly one substitution, insertion or deletion turns one string into the other."""
    shorter, longer = sorted((first, second), key=len)
    for index, (a, b) in enumerate(zip(shorter, longer, strict=False)):
        if a != b:  # the rest must agree once this character is replaced or skipped
            rest_of_shorter = index + 1 if len(shorter) == len(longer) else index
            return shorter[rest_of_shorter:] == longer[index + 1 :]
    return len(longer) - len(shorter) == 1  # one extra character at the end
