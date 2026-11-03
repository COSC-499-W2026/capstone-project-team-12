"""The location and clue-text routes under /api/v1/content (A20) over HTTP.

The rules are tested in test_content_locations.py. These check who may call each route, the
status codes, the response bodies and that the routes commit.
"""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.schemas import LocationOut
from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.content import Clue, Location
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole

URL = "/api/v1/content/locations"
NEW_LOCATION = {
    "name": "Science lab",
    "building_name": "Science Building",
    "room_number": "sci-333",
    "gps_points": [{"lat": 49.9394, "lng": -119.3948}],
}
# a replace sends every field
REPLACEMENT = NEW_LOCATION | {
    "floor_number": None,
    "accepted_answers": [],
    "gps_radius_meters": 45,
    "is_active": True,
}
LOCATION_FIELDS = set(LocationOut.model_fields)
# method, path under URL ({id} is an existing location) and body of each route
READ = ("GET", "", None)
WRITES = [
    ("POST", "", NEW_LOCATION),
    ("PUT", "/{id}", REPLACEMENT),
    ("DELETE", "/{id}", None),
    ("PUT", "/{id}/clues/2/fr", {"text": "trois cent trente-trois"}),
]
# students (TAs too) and admins are refused every route, teachers without content rights changes
REFUSED = [
    *[(kind, *route) for route in [READ, *WRITES] for kind in ("student", "assistant", "admin")],
    *[("teacher", *route) for route in WRITES],
]


@pytest.fixture
def commits(db: Session, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every db.commit() call. transaction_calls cannot tell them apart from the savepoints that
    create and replace release; the services never commit (test_content_locations.py)."""
    calls: list[str] = []
    real_commit = db.commit

    def spy() -> None:
        calls.append("commit")
        real_commit()

    monkeypatch.setattr(db, "commit", spy)
    return calls


@pytest.fixture
def call(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[..., Response]:
    """Send a request to `URL + path` as `user`, with an optional JSON body."""

    def send(user: User, method: str, path: str = "", body: Any = None) -> Response:
        return client.request(method, URL + path, json=body, headers=auth_headers(user))

    return send


@pytest.fixture
def account(
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
) -> Callable[[str], User]:
    """A user of one kind: manager (content rights), teacher, student, assistant (a student who
    is a class's TA) or admin."""

    def make(kind: str) -> User:
        if kind == "manager":
            return make_user(role=UserRole.STAFF, can_manage_content=True)
        if kind == "teacher":
            return make_user(role=UserRole.STAFF)
        if kind == "admin":
            return make_user(role=UserRole.ADMIN)
        user = make_user()
        if kind == "assistant":
            make_class_staff(make_class(), user, role=ClassStaffRole.ASSISTANT)
        return user

    return make


def content_rows(db: Session) -> tuple[int | None, int | None]:
    """How many locations and clues there are."""
    return db.scalar(select(func.count(Location.id))), db.scalar(select(func.count(Clue.id)))


def test_any_staff_account_lists_locations_inactive_ones_too(
    call: Callable[..., Response],
    account: Callable[[str], User],
    make_location: Callable[..., Location],
) -> None:
    active, inactive = make_location(), make_location(is_active=False)

    response = call(account("teacher"), "GET")

    assert response.status_code == 200, response.text
    listed = {item["id"]: item for item in response.json()}
    assert {str(active.id), str(inactive.id)} <= set(listed)
    assert set(listed[str(inactive.id)]) == LOCATION_FIELDS
    assert listed[str(inactive.id)]["is_active"] is False


@pytest.mark.parametrize(("kind", "method", "path", "body"), REFUSED)
def test_refused_users_see_and_change_nothing(
    call: Callable[..., Response],
    account: Callable[[str], User],
    make_location: Callable[..., Location],
    assert_error: Callable[..., None],
    db: Session,
    kind: str,
    method: str,
    path: str,
    body: Any,
) -> None:
    location = make_location()
    user, rows = account(kind), content_rows(db)

    response = call(user, method, path.format(id=location.id), body)

    assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)
    assert location.room_number not in response.text  # room numbers are the answers
    # nothing was written: no new location or clue, and the location is as it was
    assert content_rows(db) == rows
    db.refresh(location)
    assert location.room_number != "SCI 333" and location.is_active


def test_rights_are_checked_before_the_body(
    call: Callable[..., Response], account: Callable[[str], User], assert_error: Callable[..., None]
) -> None:
    assert_error(call(account("teacher"), "POST", body={}), 403, ErrorCode.FORBIDDEN_ROLE)


@pytest.mark.parametrize(("method", "path", "body"), [READ, *WRITES])
def test_every_route_needs_a_token(
    client: TestClient, assert_error: Callable[..., None], method: str, path: str, body: Any
) -> None:
    response = client.request(method, URL + path.format(id=uuid4()), json=body)

    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_create_location(
    call: Callable[..., Response],
    account: Callable[[str], User],
    db: Session,
    commits: list[str],
) -> None:
    response = call(account("manager"), "POST", body=NEW_LOCATION)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == LOCATION_FIELDS
    # stored as printed on the sign, with the room's own answer and the default radius
    assert (body["room_number"], body["accepted_answers"]) == ("SCI 333", ["SCI333"])
    assert body["gps_radius_meters"] == get_settings().gps_radius_meters
    assert body["is_active"] is True
    assert db.get_one(Location, body["id"]).name == "Science lab"
    assert commits == ["commit"]


def test_create_with_a_used_room_number_is_422_on_room_number(
    call: Callable[..., Response],
    account: Callable[[str], User],
    make_location: Callable[..., Location],
    assert_error: Callable[..., None],
) -> None:
    make_location(room_number="SCI 333", accepted_answers=["SCI333"], is_active=False)

    response = call(account("manager"), "POST", body=NEW_LOCATION)

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    assert [(d["in"], d["field"]) for d in response.json()["error"]["details"]] == [
        ("body", "room_number")
    ]


def test_create_without_an_entrance_is_422(
    call: Callable[..., Response], account: Callable[[str], User], assert_error: Callable[..., None]
) -> None:
    response = call(account("manager"), "POST", body=NEW_LOCATION | {"gps_points": []})

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    assert response.json()["error"]["details"][0]["field"] == "gps_points"


def test_replace_brings_a_deactivated_location_back(
    call: Callable[..., Response],
    account: Callable[[str], User],
    make_location: Callable[..., Location],
    db: Session,
    commits: list[str],
) -> None:
    location = make_location(is_active=False)

    response = call(account("manager"), "PUT", f"/{location.id}", REPLACEMENT)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(location.id)
    db.refresh(location)
    assert (location.room_number, location.gps_radius_meters) == ("SCI 333", 45)
    assert location.is_active is True
    assert commits == ["commit"]


def test_replace_needs_every_field(
    call: Callable[..., Response],
    account: Callable[[str], User],
    make_location: Callable[..., Location],
    assert_error: Callable[..., None],
    db: Session,
) -> None:
    # left out, these would be reset: the location reactivated, the radius back to the setting
    location = make_location(is_active=False, gps_radius_meters=45)

    response = call(account("manager"), "PUT", f"/{location.id}", NEW_LOCATION)

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    missing = {d["field"] for d in response.json()["error"]["details"]}
    assert missing == {"floor_number", "accepted_answers", "gps_radius_meters", "is_active"}
    db.refresh(location)
    assert (location.is_active, location.gps_radius_meters) == (False, 45)


def test_delete_deactivates_the_location(
    call: Callable[..., Response],
    account: Callable[[str], User],
    make_location: Callable[..., Location],
    db: Session,
    commits: list[str],
) -> None:
    location = make_location()

    response = call(account("manager"), "DELETE", f"/{location.id}")

    assert response.status_code == 204, response.text
    assert response.content == b""
    db.refresh(location)
    assert location.is_active is False
    assert commits == ["commit"]


@pytest.mark.parametrize(("method", "path", "body"), WRITES[1:])
def test_unknown_location_is_404(
    call: Callable[..., Response],
    account: Callable[[str], User],
    assert_error: Callable[..., None],
    method: str,
    path: str,
    body: Any,
) -> None:
    response = call(account("manager"), method, path.format(id=uuid4()), body)

    assert_error(response, 404, ErrorCode.NOT_FOUND)


def test_set_clue_text_creates_the_clue_and_blank_clears_it(
    call: Callable[..., Response],
    account: Callable[[str], User],
    make_location: Callable[..., Location],
    db: Session,
    commits: list[str],
) -> None:
    location = make_location()
    manager = account("manager")
    path = f"/{location.id}/clues/2/fr"

    response = call(manager, "PUT", path, {"text": "  trois cent trente-trois "})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "id": body["id"],
        "location_id": str(location.id),
        "difficulty": 2,
        "language": "fr",
        "text": "trois cent trente-trois",
    }
    assert commits == ["commit"]

    cleared = call(manager, "PUT", path, {"text": "   "})

    assert cleared.json() == body | {"text": None}
    clue = db.scalars(select(Clue).where(Clue.location_id == location.id)).one()
    assert clue.text is None


def test_clue_text_must_be_sent(
    call: Callable[..., Response],
    account: Callable[[str], User],
    make_location: Callable[..., Location],
    assert_error: Callable[..., None],
) -> None:
    # a PUT replaces the text, so clearing it is an explicit null, not an empty body
    response = call(account("manager"), "PUT", f"/{make_location().id}/clues/2/fr", {})

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    assert [d["field"] for d in response.json()["error"]["details"]] == ["text"]


@pytest.mark.parametrize(("clue", "field"), [("4/fr", "difficulty"), ("2/xx", "language")])
def test_unknown_difficulty_or_language_is_422(
    call: Callable[..., Response],
    account: Callable[[str], User],
    make_location: Callable[..., Location],
    assert_error: Callable[..., None],
    clue: str,
    field: str,
) -> None:
    response = call(account("manager"), "PUT", f"/{make_location().id}/clues/{clue}", {"text": ""})

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    assert [(d["in"], d["field"]) for d in response.json()["error"]["details"]] == [("path", field)]
