"""The audio zip import routes under /api/v1/content/import (A20) over HTTP.

The rules are tested in test_clue_audio_import.py and test_feedback_audio_import.py. These check
the acceptance cases end to end, who may call each route, the status codes, the reports and that
an import is committed.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from test_clue_audio_import import MB, answer, file_name, recordings, zip_of
from test_content_uploads_api import COVERAGE_URL, playable

from app.config import get_settings
from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.content import AudioAsset, FeedbackAudio, Location
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, Difficulty, FeedbackCode, Language, UserRole

CLUE_AUDIO_URL = "/api/v1/content/import/clue-audio"
FEEDBACK_AUDIO_URL = "/api/v1/content/import/feedback-audio"
ZIP_URLS = [CLUE_AUDIO_URL, FEEDBACK_AUDIO_URL]


@pytest.fixture
def manager(make_user: Callable[..., User]) -> User:
    return make_user(role=UserRole.STAFF, can_manage_content=True)


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
def send(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]], manager: User
) -> Callable[..., Response]:
    """Post `data` to a zip import route as `user` (the content manager by default). The file's
    name and type say nothing of its content, as neither is trusted."""

    def post(url: str, data: bytes, user: User | None = None) -> Response:
        return client.post(
            url,
            files={"file": ("upload", data, "application/octet-stream")},
            headers=auth_headers(user or manager),
        )

    return post


@pytest.fixture
def valid_zips(
    make_location: Callable[..., Location], make_audio: Callable[..., bytes]
) -> dict[str, bytes]:
    """A zip of one recording each route accepts, by its URL."""
    return {
        CLUE_AUDIO_URL: zip_of({file_name(make_location()): make_audio(1)}),
        FEEDBACK_AUDIO_URL: zip_of({"NAV_START_fr.mp3": make_audio(1)}),
    }


def report(response: Response) -> dict[str, Any]:
    """The report in a 200 response; fails with the server's error otherwise."""
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def rejections(body: dict[str, Any]) -> list[tuple[str, str]]:
    """(file, code) of each rejected file of a report; every one gives a reason."""
    assert all(rejected["reason"] for rejected in body["rejected"])
    return [(rejected["file"], rejected["code"]) for rejected in body["rejected"]]


def test_zip_imports_only_the_valid_files_and_reports_each_failure(
    send: Callable[..., Response],
    manager: User,
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    stored_media_dir: Path,
    db: Session,
) -> None:
    location = make_location()
    files = {
        file_name(location, 1): make_audio(2),
        file_name(location, 2): bytes(get_settings().max_media_file_mb * MB + 1),
        file_name(location, 3): make_audio(get_settings().max_audio_seconds + 1),
        file_name(location, 1, "ja"): b"SCI333",
    }

    body = report(send(CLUE_AUDIO_URL, zip_of(files)))

    names = list(files)
    assert body["imported"] == [{"file": names[0]}]
    assert rejections(body) == [
        (names[1], ErrorCode.FILE_TOO_LARGE),
        (names[2], ErrorCode.AUDIO_TOO_LONG),
        (names[3], ErrorCode.UNSUPPORTED_MEDIA_TYPE),
    ]
    db.rollback()  # the route committed: this only undoes what was not
    assert recordings(location, db) == [(1, "fr", True)]
    recording = db.scalars(select(AudioAsset).filter_by(uploaded_by=manager.id)).one()
    assert [path.name for path in stored_media_dir.iterdir()] == [recording.file_path]


@pytest.mark.parametrize(
    ("url", "make_names"),
    [
        pytest.param(
            CLUE_AUDIO_URL,
            lambda room: [
                f"{room.lower()}-2-fr.mp3",  # lowercase, hyphens
                f"{room}_4_fr.mp3",  # no difficulty 4
                f"{room}_2_xx.mp3",  # no language xx
                f"{room}_2_fr.wav",  # not .mp3 or .m4a
            ],
            id="clue",
        ),
        pytest.param(
            FEEDBACK_AUDIO_URL,
            lambda room: ["NAV_NOPE_fr.mp3", "nav_start_fr.mp3", "NAV_START_xx.mp3"],
            id="feedback",
        ),
    ],
)
def test_file_named_outside_the_format_is_not_imported(
    send: Callable[..., Response],
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    stored_media_dir: Path,
    db: Session,
    url: str,
    make_names: Callable[[str], list[str]],
) -> None:
    location = make_location()
    names = make_names(answer(location))  # a room some location has

    body = report(send(url, zip_of(dict.fromkeys(names, make_audio(1)))))

    assert body["imported"] == []
    assert rejections(body) == [(name, ErrorCode.VALIDATION_ERROR) for name in names]
    db.rollback()
    assert recordings(location, db) == []
    assert list(stored_media_dir.iterdir()) == []


def test_imported_phrases_and_clues_make_the_location_playable(
    send: Callable[..., Response],
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    manager: User,
    make_location: Callable[..., Location],
    make_audio: Callable[..., bytes],
    stored_media_dir: Path,
    db: Session,
) -> None:
    location = make_location()
    # French lacks every phrase, whatever the database already holds (rolled back after the test)
    db.execute(delete(FeedbackAudio).where(FeedbackAudio.language == Language.FR))
    phrases = {f"{code}_fr.mp3": make_audio(1) for code in FeedbackCode}

    feedback = report(send(FEEDBACK_AUDIO_URL, zip_of(phrases)))

    assert (len(feedback["imported"]), feedback["rejected"]) == (len(FeedbackCode), [])
    db.rollback()  # before the next request, whose commit would also save what this one did not
    french = select(FeedbackAudio.file_path).where(FeedbackAudio.language == Language.FR)
    assert sorted(db.scalars(french)) == sorted(path.name for path in stored_media_dir.iterdir())

    clues = report(send(CLUE_AUDIO_URL, zip_of({file_name(location): make_audio(2)})))

    assert (clues["imported"], clues["rejected"]) == ([{"file": file_name(location)}], [])
    coverage = client.get(COVERAGE_URL, headers=auth_headers(manager))
    assert coverage.status_code == 200, coverage.text
    assert playable(coverage, location) == [(Language.FR, Difficulty.ADVANCING)]


@pytest.mark.parametrize("url", ZIP_URLS)
def test_upload_that_is_not_a_zip_is_415(
    send: Callable[..., Response], assert_error: Callable[..., None], url: str
) -> None:
    response = send(url, b"SCI333")

    assert_error(response, 415, ErrorCode.UNSUPPORTED_MEDIA_TYPE)
    assert response.json()["error"]["message"] == "Upload a zip file"


@pytest.mark.parametrize("url", ZIP_URLS)
def test_import_without_a_file_is_422_on_file(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    manager: User,
    assert_error: Callable[..., None],
    url: str,
) -> None:
    response = client.post(url, headers=auth_headers(manager))

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    assert [(d["in"], d["field"]) for d in response.json()["error"]["details"]] == [
        ("body", "file")
    ]


@pytest.mark.parametrize("url", ZIP_URLS)
def test_only_content_managers_can_import(
    send: Callable[..., Response],
    valid_zips: dict[str, bytes],
    assistant: User,
    make_user: Callable[..., User],
    assert_error: Callable[..., None],
    stored_media_dir: Path,
    url: str,
) -> None:
    teacher = make_user(role=UserRole.STAFF)  # a staff account without content rights

    for user in (teacher, assistant, make_user(role=UserRole.ADMIN)):
        response = send(url, valid_zips[url], user=user)

        assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)
    # each import saves the file before any row, so no file means nothing was imported
    assert list(stored_media_dir.iterdir()) == []


@pytest.mark.parametrize("url", ZIP_URLS)
def test_content_manager_can_import(
    send: Callable[..., Response],
    valid_zips: dict[str, bytes],
    stored_media_dir: Path,
    url: str,
) -> None:
    body = report(send(url, valid_zips[url]))  # so the 403s above are not the file's fault

    assert (len(body["imported"]), body["rejected"]) == (1, [])


@pytest.mark.parametrize("url", ZIP_URLS)
def test_every_import_needs_a_token(
    client: TestClient, assert_error: Callable[..., None], url: str
) -> None:
    assert_error(client.post(url), 401, ErrorCode.NOT_AUTHENTICATED)
