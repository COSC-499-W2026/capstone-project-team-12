"""The OCR queue (A10; spec Stage 2, step 6: at most 2 OCR jobs at once, 503 OCR_BUSY with
Retry-After if 10 are already waiting). Unit tests with real threads and no OCR model: a "read"
is a function that waits for the test. Anything a bug could block runs in a daemon thread with a
timeout, so a broken queue fails the test instead of hanging it.
"""

import threading
from contextlib import ExitStack
from queue import Empty, SimpleQueue

import pytest

from app.errors import AppError, ErrorCode
from app.verification.ocr_queue import (
    MAX_RUNNING_READS,
    MAX_WAITING_READS,
    OcrQueue,
    get_ocr_queue,
)

WAIT = 5.0  # seconds a thread that should get somewhere is given
BRIEF = 0.2  # seconds a read that should be waiting is given to start anyway


def read_once(queue: OcrQueue) -> str:
    """An upload's read through `queue`, in a daemon thread; raises Empty unless it is read
    within WAIT seconds (a stuck thread is left behind)."""
    result: SimpleQueue[str] = SimpleQueue()

    def upload() -> None:
        with queue.place() as place:
            result.put(place.read(lambda: "read", WAIT))

    threading.Thread(target=upload, daemon=True).start()
    return result.get(timeout=WAIT)


def refused(queue: OcrQueue) -> AppError:
    with pytest.raises(AppError) as refusal, queue.place():
        pass
    return refusal.value


def test_the_process_has_one_queue_with_the_spec_limits() -> None:
    assert get_ocr_queue() is get_ocr_queue()
    assert (MAX_RUNNING_READS, MAX_WAITING_READS) == (2, 10)


def test_two_photos_are_read_at_once_and_a_third_waits_for_a_slot() -> None:
    queue = OcrQueue()
    started: SimpleQueue[int] = SimpleQueue()
    done = threading.Semaphore(0)  # each release lets one read finish

    def upload(number: int) -> None:
        with queue.place() as place:
            place.read(lambda: (started.put(number), done.acquire()), WAIT)

    uploads = [threading.Thread(target=upload, args=(n,), daemon=True) for n in range(3)]
    for thread in uploads:
        thread.start()

    first_two = {started.get(timeout=WAIT), started.get(timeout=WAIT)}
    with pytest.raises(Empty):
        started.get(timeout=BRIEF)  # the third waits while two are read
    done.release()
    third = started.get(timeout=WAIT)  # and is read once one of them has finished
    done.release()
    done.release()
    for thread in uploads:
        thread.join(WAIT)
    assert first_two | {third} == {0, 1, 2}
    assert not any(thread.is_alive() for thread in uploads)


def test_with_two_photos_read_and_ten_waiting_the_next_upload_is_busy() -> None:
    queue = OcrQueue()
    reading = threading.Barrier(MAX_RUNNING_READS + 1)
    finish = threading.Event()

    def upload() -> None:
        with queue.place() as place:
            place.read(lambda: (reading.wait(WAIT), finish.wait(WAIT)), WAIT)

    uploads = [threading.Thread(target=upload, daemon=True) for _ in range(MAX_RUNNING_READS)]
    for thread in uploads:
        thread.start()
    reading.wait(WAIT)  # both are being read
    with ExitStack() as waiting:
        for _ in range(MAX_WAITING_READS):
            waiting.enter_context(queue.place())  # the 10th still gets a place

        busy = refused(queue)

    finish.set()
    for thread in uploads:
        thread.join(WAIT)
    assert (busy.code, busy.headers) == (ErrorCode.OCR_BUSY, {"Retry-After": "5"})


def test_an_upload_that_gets_no_slot_in_time_gives_up_without_reading() -> None:
    queue = OcrQueue(running=1, waiting=1)
    outcome: SimpleQueue[object] = SimpleQueue()

    def wait_for_the_slot() -> None:
        with queue.place() as waiting:
            try:
                outcome.put(waiting.read(lambda: "read", BRIEF))
            except AppError as busy:
                outcome.put(busy)

    def read_while_another_waits() -> object:
        threading.Thread(target=wait_for_the_slot, daemon=True).start()
        return outcome.get(timeout=WAIT)

    with queue.place() as reading:
        busy = reading.read(read_while_another_waits, WAIT)

    assert isinstance(busy, AppError)
    assert (busy.code, busy.headers) == (ErrorCode.OCR_BUSY, {"Retry-After": "5"})
    assert read_once(queue) == "read"  # the slot is free, given back once by the read that had it


def test_a_place_and_its_slot_are_given_back_when_the_read_fails() -> None:
    queue = OcrQueue(running=1, waiting=0)

    def fail() -> None:
        raise RuntimeError("OCR failed")

    with pytest.raises(RuntimeError, match="OCR failed"), queue.place() as place:
        place.read(fail, WAIT)

    assert read_once(queue) == "read"


def test_a_place_is_given_back_when_the_upload_fails_before_its_read() -> None:
    queue = OcrQueue(running=1, waiting=0)

    with pytest.raises(RuntimeError, match="re-encoding failed"), queue.place():
        raise RuntimeError("re-encoding failed")

    assert read_once(queue) == "read"


def test_a_busy_upload_gives_back_no_place_it_never_had() -> None:
    queue = OcrQueue(running=1, waiting=0)

    with queue.place():
        for _ in range(3):
            assert refused(queue).code == ErrorCode.OCR_BUSY

    with queue.place():  # the one place is free again, and there is still only one
        assert refused(queue).code == ErrorCode.OCR_BUSY
