"""The OCR queue: at most 2 photos are read at once, and at most 10 more wait (A10; DFD 3.4).

Spec (Game Flow > Stage 2: Verification, step 6): "At most 2 OCR jobs run at once; if 10 are
already waiting, return 503 OCR_BUSY with Retry-After", and a 503 OCR_BUSY is not an attempt.

An upload takes a place in the queue (`OcrQueue.place`) once its photo is re-encoded, before its
attempt is reserved and the photo saved, so an upload refused with OCR_BUSY saves no photo and
uses no attempt. Its place's `read` then waits for one of the reading slots and runs the photo's
OCR in it. The place is given back when the upload leaves the `with` block, also on an error.

The limits live in this process's memory, so they hold only with one API process (the image
runs one Uvicorn worker): `get_ocr_queue` gives the process its one queue, made at import, so
two threads can never each make one.

Proposed, not in the spec (team to confirm):

- A slot is held for the whole of a photo's OCR, both reads when the second read runs (`verify`):
  the spec's "OCR job" is one photo.
- A waiting upload gives up after the caller's `wait_seconds` (the route passes the processing
  timeout, after which its result would be dropped anyway) with OCR_BUSY. A read that never
  ends can't be stopped from here: it keeps its slot until the process restarts.
- Retry-After is 5 seconds (A10 plan Q12), about one read.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from app.errors import AppError, ErrorCode

MAX_RUNNING_READS = 2  # spec: at most 2 OCR jobs run at once
MAX_WAITING_READS = 10  # spec: 503 OCR_BUSY if 10 are already waiting
OCR_RETRY_AFTER_SECONDS = 5  # proposed, see the module docstring
BUSY = "Too many photos are being read. Try again in a moment."

T = TypeVar("T")


class OcrPlace:
    """An upload's place in the queue, held until its OCR has run."""

    def __init__(self, slots: threading.BoundedSemaphore) -> None:
        self._slots = slots

    def read(self, work: Callable[[], T], wait_seconds: float) -> T:
        """Run `work` (the photo's OCR) in a reading slot, waiting until one is free.

        503 OCR_BUSY (with Retry-After) if no slot is free within `wait_seconds`; `work` hasn't
        run then.
        """
        if not self._slots.acquire(timeout=wait_seconds):
            raise ocr_busy(BUSY)
        try:
            return work()
        finally:
            self._slots.release()


class OcrQueue:
    """`running` reading slots and room for `waiting` more uploads, shared by every request."""

    def __init__(self, running: int = MAX_RUNNING_READS, waiting: int = MAX_WAITING_READS) -> None:
        self._slots = threading.BoundedSemaphore(running)
        self._places = running + waiting
        self._taken = 0
        self._lock = threading.Lock()

    @contextmanager
    def place(self) -> Iterator[OcrPlace]:
        """A place for one upload, given back when the block exits.

        503 OCR_BUSY (with Retry-After) when `running` + `waiting` uploads already hold one.
        Never waits itself: only `OcrPlace.read` waits, for a slot.
        """
        with self._lock:
            if self._taken >= self._places:
                raise ocr_busy(BUSY)
            self._taken += 1
        try:
            yield OcrPlace(self._slots)
        finally:
            with self._lock:
                self._taken -= 1


def ocr_busy(message: str) -> AppError:
    """503 OCR_BUSY, with the Retry-After the student's app waits for before sending again."""
    return AppError(
        ErrorCode.OCR_BUSY, message, headers={"Retry-After": str(OCR_RETRY_AFTER_SECONDS)}
    )


_QUEUE = OcrQueue()


def get_ocr_queue() -> OcrQueue:
    """The process's one OCR queue."""
    return _QUEUE
