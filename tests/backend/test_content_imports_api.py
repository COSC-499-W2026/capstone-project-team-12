"""The locations CSV import route, /api/v1/content/import/locations (A20), over HTTP.

The rules are tested in test_location_import.py. These check who may call the route, the status
codes, the report and that an import is committed while a dry run is not.
"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session
from test_location_import import csv_file, row, saved

from app.config import get_settings
from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole

URL = "/api/v1/content/import/locations"


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
    """Post `data` as the CSV file, as `user` (the content manager by default), with any query
    parameters, e.g. dry_run="true". The file's name and type say nothing of its content, as
    neither is trusted."""

    def post(data: bytes, user: User | None = None, **params: str) -> Response:
        return client.post(
            URL,
            files={"file": ("upload", data, "application/octet-stream")},
            params=params,
            headers=auth_headers(user or manager),
        )

    return post


def report(response: Response) -> dict[str, Any]:
    """The report in a 200 response; fails with the server's error otherwise."""
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.parametrize(("dry_run", "is_saved"), [("false", True), ("true", False)])
def test_import_reports_every_row_and_a_dry_run_saves_nothing(
    send: Callable[..., Response], db: Session, dry_run: str, is_saved: bool
) -> None:
    data = csv_file(row("QZX 901"), row("Q 1"))

    body = report(send(data, dry_run=dry_run))

    assert body["dry_run"] is not is_saved
    assert body["imported"] == [{"row": 2, "room_number": "QZX 901"}]
    assert [(rejected["row"], rejected["code"]) for rejected in body["rejected"]] == [
        (3, ErrorCode.VALIDATION_ERROR)
    ]
    assert body["rejected"][0]["reason"].startswith("room_number: ")
    db.rollback()  # the route committed: this only undoes what was not
    assert (saved("QZX 901", db) is not None) == is_saved


def test_file_with_a_wrong_header_is_refused_whole(
    send: Callable[..., Response], assert_error: Callable[..., None], db: Session
) -> None:
    response = send(csv_file(row("QZX 901"), header="room,lat,lng"))

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    assert [(d["in"], d["field"]) for d in response.json()["error"]["details"]] == [
        ("body", "file")
    ]
    assert saved("QZX 901", db) is None


def test_file_over_the_size_limit_is_413(
    send: Callable[..., Response], assert_error: Callable[..., None]
) -> None:
    limit_kb = get_settings().max_import_file_kb

    response = send(bytes(limit_kb * 1024 + 1))

    assert_error(response, 413, ErrorCode.FILE_TOO_LARGE)
    assert response.json()["error"]["message"] == f"CSV files can be at most {limit_kb} KB"


def test_import_without_a_file_is_422_on_file(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    manager: User,
    assert_error: Callable[..., None],
) -> None:
    response = client.post(URL, params={"dry_run": "true"}, headers=auth_headers(manager))

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    assert [(d["in"], d["field"]) for d in response.json()["error"]["details"]] == [
        ("body", "file")
    ]


def test_only_content_managers_can_import(
    send: Callable[..., Response],
    assistant: User,
    make_user: Callable[..., User],
    assert_error: Callable[..., None],
    db: Session,
) -> None:
    teacher = make_user(role=UserRole.STAFF)  # a staff account without content rights

    for user in (teacher, assistant, make_user(role=UserRole.ADMIN)):
        response = send(csv_file(row("QZX 901")), user=user)

        assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)
    assert saved("QZX 901", db) is None


def test_import_needs_a_token(client: TestClient, assert_error: Callable[..., None]) -> None:
    assert_error(client.post(URL), 401, ErrorCode.NOT_AUTHENTICATED)
