"""The clue audio upload and coverage routes under /api/v1/content (A20) over HTTP.

The rules are tested in test_audio_files.py, test_clue_audio.py and test_content_coverage.py.
These check the acceptance cases end to end, who may call each route, the status codes, the
bodies and that an upload is committed.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.schemas import ClueRecordingOut, CoverageReport
from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.content import AudioAsset, Clue, FeedbackAudio, Location
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, Difficulty, FeedbackCode, Language, UserRole

COVERAGE_URL = "/api/v1/content/coverage"
ONE_MB = 1024 * 1024  # MAX_MEDIA_FILE_MB counts MiB, as in test_audio_files.py
# files the upload refuses, made from make_audio; each is sent named clue.mp3 as audio/mpeg, so
# neither the name nor the declared type is trusted
REJECTED = [
    pytest.param(
        lambda make_audio: make_audio(get_settings().max_audio_seconds + 1),
        422,
        ErrorCode.AUDIO_TOO_LONG,
        id="too long",
    ),
    pytest.param(
        lambda make_audio: bytes(get_settings().max_media_file_mb * ONE_MB + 1),
        413,
        ErrorCode.FILE_TOO_LARGE,
        id="too large",
    ),
    pytest.param(
        lambda make_audio: b"SCI333", 415, ErrorCode.UNSUPPORTED_MEDIA_TYPE, id="not audio"
    ),
]


def audio_url(location_id: UUID) -> str:
    """The upload route of the location's French clue at difficulty 2."""
    return f"/api/v1/content/locations/{location_id}/clues/2/fr/audio"


@pytest.fixture
def manager(make_user: Callable[..., User]) -> User:
    return make_user(role=UserRole.STAFF, can_manage_content=True)


@pytest.fixture
def upload(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]], manager: User
) -> Callable[..., Response]:
    """Upload `data` to audio_url(location_id) as `user` (the content manager by default), with
    any other form fields, e.g. make_primary="true"."""

    def send(location_id: UUID, data: bytes, user: User | None = None, **form: str) -> Response:
        return client.post(
            audio_url(location_id),
            files={"file": ("clue.mp3", data, "audio/mpeg")},
            data=form,
            headers=auth_headers(user or manager),
        )

    return send


@pytest.fixture
def assistant(
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
) -> User:
    """A student who is a class's TA: still a student account, so refused like any student."""
    user = make_user()
    make_class_staff(make_class(), user, role=ClassStaffRole.ASSISTANT)
    return user


@pytest.fixture
def every_french_phrase(db: Session, make_feedback_audio: Callable[..., FeedbackAudio]) -> None:
    """French has every feedback phrase, whatever the database already holds (the test's
    transaction is rolled back)."""
    db.execute(delete(FeedbackAudio).where(FeedbackAudio.language == Language.FR))
    for code in FeedbackCode:
        make_feedback_audio(code, Language.FR)


def created(response: Response) -> dict[str, Any]:
    """The body of a 201 response; fails with the server's error otherwise."""
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def playable(response: Response, location: Location) -> list[tuple[Language, Difficulty]]:
    """The location's playable combinations in a coverage response."""
    report = CoverageReport.model_validate(response.json())
    item = next(item for item in report.locations if item.id == location.id)
    return [(c.language, c.difficulty) for c in item.combinations if c.playable]


def assert_nothing_stored(db: Session, location: Location, media_dir: Path) -> None:
    """No clue was created for the location and no file was saved."""
    clues = select(func.count(Clue.id)).where(Clue.location_id == location.id)
    assert db.scalar(clues) == 0
    assert list(media_dir.iterdir()) == []


def test_uploaded_mp3_makes_the_location_playable_in_the_coverage_report(
    upload: Callable[..., Response],
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    manager: User,
    make_user: Callable[..., User],
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    every_french_phrase: None,
    stored_media_dir: Path,
    db: Session,
) -> None:
    location = make_location()
    teacher = make_user(role=UserRole.STAFF)  # any staff account may read the report
    assert playable(client.get(COVERAGE_URL, headers=auth_headers(teacher)), location) == []

    body = created(upload(location.id, make_audio(3)))

    assert set(body) == set(ClueRecordingOut.model_fields)  # no storage key
    assert body["is_primary"] is True  # the clue's first recording
    db.rollback()  # the route committed: this only undoes what was not
    recording = db.get(AudioAsset, body["id"])
    assert recording is not None and recording.uploaded_by == manager.id
    report = client.get(COVERAGE_URL, headers=auth_headers(teacher))
    assert report.status_code == 200, report.text
    assert playable(report, location) == [(Language.FR, Difficulty.ADVANCING)]


def test_a_later_recording_becomes_primary_only_when_asked(
    upload: Callable[..., Response],
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    stored_media_dir: Path,
    db: Session,
) -> None:
    location = make_location()

    first, second, third = (
        created(upload(location.id, make_audio(1), **form))
        for form in ({}, {}, {"make_primary": "true"})
    )

    # as each was stored: the clue's first recording, then one not asked to replace it
    assert [r["is_primary"] for r in (first, second, third)] == [True, False, True]
    assert first["clue_id"] == second["clue_id"] == third["clue_id"]
    db.expire_all()
    # the first gave way to the third and is kept, with its file
    assert db.get_one(AudioAsset, first["id"]).is_primary is False
    assert len(list(stored_media_dir.iterdir())) == 3


@pytest.mark.parametrize(("make_file", "status", "code"), REJECTED)
def test_rejected_file_stores_nothing(
    upload: Callable[..., Response],
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    assert_error: Callable[..., None],
    stored_media_dir: Path,
    db: Session,
    make_file: Callable[[Callable[..., bytes]], bytes],
    status: int,
    code: ErrorCode,
) -> None:
    location = make_location()

    response = upload(location.id, make_file(make_audio))

    assert_error(response, status, code)
    assert_nothing_stored(db, location, stored_media_dir)


def test_only_content_managers_can_upload(
    upload: Callable[..., Response],
    assistant: User,
    make_user: Callable[..., User],
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    assert_error: Callable[..., None],
    stored_media_dir: Path,
    db: Session,
) -> None:
    location = make_location()
    data = make_audio(1)
    teacher = make_user(role=UserRole.STAFF)  # a staff account without content rights

    for user in (teacher, assistant, make_user(role=UserRole.ADMIN)):
        response = upload(location.id, data, user=user)

        assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)
    assert_nothing_stored(db, location, stored_media_dir)


def test_upload_to_an_unknown_location_is_404(
    upload: Callable[..., Response],
    make_audio: Callable[..., bytes],
    assert_error: Callable[..., None],
    stored_media_dir: Path,
) -> None:
    response = upload(uuid4(), make_audio(1))

    assert_error(response, 404, ErrorCode.NOT_FOUND)
    assert list(stored_media_dir.iterdir()) == []


def test_upload_without_a_file_is_422_on_file(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    manager: User,
    make_location: Callable[..., Location],
    assert_error: Callable[..., None],
) -> None:
    response = client.post(
        audio_url(make_location().id), data={"make_primary": "true"}, headers=auth_headers(manager)
    )

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    assert [(d["in"], d["field"]) for d in response.json()["error"]["details"]] == [
        ("body", "file")
    ]


def test_tas_and_admins_cannot_read_coverage(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    assistant: User,
    make_user: Callable[..., User],
    make_location: Callable[..., Location],
    assert_error: Callable[..., None],
) -> None:
    location = make_location()

    for user in (assistant, make_user(role=UserRole.ADMIN)):
        response = client.get(COVERAGE_URL, headers=auth_headers(user))

        assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)
        assert str(location.room_number) not in response.text


@pytest.mark.parametrize(("method", "url"), [("POST", audio_url(uuid4())), ("GET", COVERAGE_URL)])
def test_every_route_needs_a_token(
    client: TestClient, assert_error: Callable[..., None], method: str, url: str
) -> None:
    assert_error(client.request(method, url), 401, ErrorCode.NOT_AUTHENTICATED)
