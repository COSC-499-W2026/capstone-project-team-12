"""Unit tests for OCR normalisation and the Stage 2 decision table (app/verification/matching.py).

Fixed text boxes stand in for EasyOCR output (spec, Testing & Acceptance: "the decision table
(with fixed OCR outputs)"), so no model and no database are needed.
"""

import pytest

from app.config import get_settings
from app.models.enums import FeedbackCode, PhotoResult
from app.verification.matching import Candidate, MatchThresholds, TextBox, decide

SPEC_THRESHOLDS = MatchThresholds(match=0.60, unclear=0.45)  # spec, Configuration
ANSWERS = ["SCI333"]


def boxes(*items: tuple[str, float]) -> list[TextBox]:
    return [TextBox(text, confidence) for text, confidence in items]


def decide_spec(
    *items: tuple[str, float], answers: list[str] = ANSWERS
) -> tuple[PhotoResult, Candidate | None]:
    """The result and best candidate for these boxes, with the spec's thresholds."""
    decision = decide(boxes(*items), answers, SPEC_THRESHOLDS)
    return decision.result, decision.candidate


@pytest.mark.parametrize("text", ["SCI 333", "sci-333", "SCI  -  333", "SCI333", "ROOM SCI 333"])
def test_sign_text_is_normalised_before_matching(text: str) -> None:
    assert decide_spec((text, 0.9)) == (PhotoResult.MATCH, Candidate("SCI333", 0.9))


# One row per rule of the spec's decision table, with values on both sides of 0.60 and 0.45.
@pytest.mark.parametrize(
    ("text", "confidence", "expected", "candidate"),
    [
        ("SCI 333", 0.92, PhotoResult.MATCH, "SCI333"),
        ("SCI 333", 0.60, PhotoResult.MATCH, "SCI333"),  # the match threshold itself is enough
        ("SCI 333", 0.5999, PhotoResult.UNCLEAR, "SCI333"),
        ("SCI 333", 0.45, PhotoResult.UNCLEAR, "SCI333"),
        ("SCI 333", 0.4499, PhotoResult.NO_MATCH, "SCI333"),  # "any other case"
        ("SCI 334", 0.99, PhotoResult.UNCLEAR, "SCI334"),  # one character away is never MATCH
        ("SCX 333", 0.45, PhotoResult.UNCLEAR, "SCX333"),
        ("SCI 334", 0.4499, PhotoResult.NO_MATCH, "SCI334"),
        ("SCI 3333", 0.9, PhotoResult.UNCLEAR, "SCI3333"),  # one character added at the end
        ("SCI 3433", 0.9, PhotoResult.UNCLEAR, "SCI3433"),  # one character added inside
        ("LIB 312", 0.99, PhotoResult.NO_MATCH, "LIB312"),  # a different room
        ("LIB 333", 0.99, PhotoResult.NO_MATCH, "LIB333"),  # the same number in another building
    ],
)
def test_decision_table_for_a_room_number(
    text: str, confidence: float, expected: PhotoResult, candidate: str
) -> None:
    assert decide_spec((text, confidence)) == (expected, Candidate(candidate, confidence))


@pytest.mark.parametrize(
    "items",
    [
        [("333", 0.9)],  # building letters missing
        [("CI 333", 0.9)],  # building letters unreadable
        [("5CI333", 0.9)],
        [("SCII 333", 0.9)],  # four letters are no building code
        [("ROOM 333", 0.9)],  # a word before the number is not a building
        [("ROOM", 0.9), ("333", 0.9)],
        [("SCI", 0.9), ("EXIT", 0.9), ("333", 0.9)],  # only neighbouring boxes are joined
        [("333", 0.45)],
    ],
)
def test_number_without_its_building_letters_is_unclear(items: list[tuple[str, float]]) -> None:
    assert decide_spec(*items) == (PhotoResult.UNCLEAR, Candidate("333", items[-1][1]))


def test_building_letters_read_too_unsure_count_as_unreadable() -> None:
    # The joined SCI333 is below the unclear confidence, so it does not hide the clear number.
    assert decide_spec(("SCI", 0.3), ("333", 0.95)) == (
        PhotoResult.UNCLEAR,
        Candidate("333", 0.95),
    )


def test_number_next_to_other_building_letters_is_no_match() -> None:
    # "LIB" and "333" in neighbouring boxes are read as LIB333, not as a number alone.
    assert decide_spec(("LIB", 0.9), ("333", 0.9))[0] == PhotoResult.NO_MATCH


@pytest.mark.parametrize(
    "items",
    [
        [],
        [("EXIT", 0.99), ("NO SMOKING", 0.95)],
        [("312", 0.9)],  # a number that is not the answer's
        [("333", 0.4499)],  # a number alone needs the unclear confidence too
        [("SCI 33345", 0.9)],  # the digits run on: no room number
        [("SCI 33", 0.9)],
    ],
)
def test_nothing_readable_is_no_match_without_a_candidate(items: list[tuple[str, float]]) -> None:
    assert decide_spec(*items) == (PhotoResult.NO_MATCH, None)


def test_building_letters_and_number_in_neighbouring_boxes_are_joined() -> None:
    result = decide_spec(("SCI", 0.9), ("333", 0.8))
    assert result == (PhotoResult.MATCH, Candidate("SCI333", 0.8))  # the lower confidence


def test_joined_boxes_take_the_lower_confidence() -> None:
    assert decide_spec(("SCI", 0.9), ("333", 0.5))[0] == PhotoResult.UNCLEAR


def test_four_digit_room_number_matches() -> None:
    result = decide_spec(("abc-1234", 0.8), answers=["ABC1234"])
    assert result == (PhotoResult.MATCH, Candidate("ABC1234", 0.8))


def test_a_longer_number_does_not_hold_a_four_digit_answer() -> None:
    assert decide_spec(("91234", 0.9), answers=["ABC1234"]) == (PhotoResult.NO_MATCH, None)


def test_one_digit_missing_from_a_four_digit_room_is_unclear() -> None:
    assert decide_spec(("ABC 123", 0.9), answers=["ABC1234"])[0] == PhotoResult.UNCLEAR


def test_any_accepted_answer_matches() -> None:
    assert decide_spec(("COM 333", 0.9), answers=["SCI333", "COM333"])[0] == PhotoResult.MATCH


def test_an_answer_beats_a_closer_read_one_character_away() -> None:
    result = decide_spec(("SCI 334", 0.99), ("EXIT", 0.9), ("SCI 333", 0.7))
    assert result == (PhotoResult.MATCH, Candidate("SCI333", 0.7))


def test_a_better_result_beats_an_answer_read_too_unsure() -> None:
    result = decide_spec(("SCI 333", 0.3), ("EXIT", 0.9), ("SCI 334", 0.8))
    assert result == (PhotoResult.UNCLEAR, Candidate("SCI334", 0.8))


def test_equal_results_go_to_the_higher_confidence() -> None:
    result = decide_spec(("SCI 334", 0.5), ("EXIT", 0.9), ("SCX 333", 0.8))
    assert result == (PhotoResult.UNCLEAR, Candidate("SCX333", 0.8))


def test_no_match_keeps_the_most_confident_room_number() -> None:
    result = decide_spec(("LIB 312", 0.6), ("EXIT", 0.9), ("COM 101", 0.95))
    assert result == (PhotoResult.NO_MATCH, Candidate("COM101", 0.95))


@pytest.mark.parametrize(
    ("item", "code"),
    [
        (("SCI 333", 0.9), FeedbackCode.VERIFY_SUCCESS),
        (("SCI 333", 0.5), FeedbackCode.VERIFY_UNCLEAR),
        (("LIB 312", 0.9), FeedbackCode.VERIFY_NO_MATCH),
    ],
)
def test_each_result_has_its_feedback_code(item: tuple[str, float], code: FeedbackCode) -> None:
    assert decide(boxes(item), ANSWERS, SPEC_THRESHOLDS).feedback_code == code


def test_repr_never_shows_the_ocr_text() -> None:
    # Rule 1: a log line or traceback that formats one of these must not show the room number.
    read = boxes(("SCI", 0.91), ("333", 0.8))
    decision = decide(read, ANSWERS, SPEC_THRESHOLDS)
    for value in [*read, decision.candidate, decision]:
        assert "SCI" not in repr(value) and "333" not in repr(value)
    assert repr(decision) == (
        "Decision(result=<PhotoResult.MATCH: 'match'>, candidate=Candidate(confidence=0.8))"
    )


def test_thresholds_come_from_the_settings(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    monkeypatch.setenv("OCR_MATCH_CONFIDENCE", "0.9")
    monkeypatch.setenv("OCR_UNCLEAR_CONFIDENCE", "0.85")
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)  # the next caller reads the restored env
    decision = decide(boxes(("SCI 333", 0.87)), ANSWERS, MatchThresholds.from_settings())
    assert decision.result == PhotoResult.UNCLEAR  # MATCH with the spec values
