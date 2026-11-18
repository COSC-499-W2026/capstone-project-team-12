"""Unit tests for the photo pipeline (app/verification/verify.py): the checks, the OCR reads and
the second pass. A fake `read_text` returns fixed text boxes, so no model is loaded."""

import io
from collections.abc import Callable, Sequence

import pytest
from PIL import Image

from app.errors import AppError, ErrorCode
from app.models.enums import PhotoResult
from app.services.uploads import BYTES_PER_MB
from app.verification import verify
from app.verification.matching import Candidate, TextBox
from app.verification.verify import verify_photo

ANSWERS = ["SCI333"]
Reads = Callable[..., list[Image.Image]]


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> Reads:
    """Makes OCR return these boxes, one list per read, in order; returns the images it read."""

    def install(*results: Sequence[tuple[str, float]]) -> list[Image.Image]:
        pending = [[TextBox(text, confidence) for text, confidence in boxes] for boxes in results]
        seen: list[Image.Image] = []

        def read_text(image: Image.Image) -> list[TextBox]:
            seen.append(image)
            return pending.pop(0)

        monkeypatch.setattr(verify, "read_text", read_text)
        return seen

    return install


def photo(file_format: str = "JPEG", size: tuple[int, int] = (64, 48)) -> io.BytesIO:
    data = io.BytesIO()
    Image.new("RGB", size, "white").save(data, file_format)
    data.seek(0)
    return data


def test_a_match_on_the_first_read_is_read_once(reads: Reads) -> None:
    seen = reads([("SCI 333", 0.9)])
    decision = verify_photo(photo(), ANSWERS)
    assert (decision.result, decision.candidate) == (PhotoResult.MATCH, Candidate("SCI333", 0.9))
    assert len(seen) == 1


def test_ocr_reads_the_enhanced_photo(reads: Reads) -> None:
    seen = reads([("SCI 333", 0.9)])
    verify_photo(photo("PNG", (3200, 1800)), ANSWERS)
    assert (seen[0].mode, seen[0].size) == ("L", (1600, 900))


@pytest.mark.parametrize(
    ("second", "expected"),
    [
        ([("SCI 333", 0.8)], Candidate("SCI333", 0.8)),  # the second read matches
        ([("LIB 312", 0.9)], Candidate("SCI334", 0.7)),  # the first read was better
    ],
)
def test_a_read_with_a_candidate_but_no_match_is_read_again(
    reads: Reads, second: list[tuple[str, float]], expected: Candidate
) -> None:
    seen = reads([("SCI 334", 0.7)], second)
    assert verify_photo(photo(), ANSWERS).candidate == expected
    assert len(seen) == 2
    assert seen[1].mode == "L" and seen[1] is not seen[0]  # the CLAHE copy


@pytest.mark.parametrize("first", [[], [("EXIT", 0.9)]])
def test_a_read_with_no_candidate_is_not_read_again(
    reads: Reads, first: list[tuple[str, float]]
) -> None:
    seen = reads(first)
    assert verify_photo(photo(), ANSWERS).result == PhotoResult.NO_MATCH
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("upload", "code"),
    [
        (photo("GIF"), ErrorCode.UNSUPPORTED_MEDIA_TYPE),
        (io.BytesIO(b"\xff\xd8\xff" + bytes(5 * BYTES_PER_MB)), ErrorCode.FILE_TOO_LARGE),
    ],
)
def test_a_refused_file_is_never_read(reads: Reads, upload: io.BytesIO, code: ErrorCode) -> None:
    seen = reads()
    with pytest.raises(AppError) as error:
        verify_photo(upload, ANSWERS)
    assert error.value.code == code
    assert seen == []
