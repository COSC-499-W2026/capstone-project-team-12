"""Integration tests for GET /api/v1/classes/{class_id}/leaderboard (A14)."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class, ClassEnrollment, PointsLedger
from app.models.enums import EnrollmentEndReason, UserRole

URL = "/api/v1/classes"


def award(db: Session, student: User, class_id: UUID | None, points: int) -> None:
    """Add a unique award to the points ledger."""
    db.add(
        PointsLedger(
            user_id=student.id,
            source_type="location",
            source_ref=str(uuid4()),
            class_id=class_id,
            language="ja",
            points=points,
        )
    )
    db.flush()


def test_enrolled_student_sees_class_scores_only(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    school_class = make_class()
    other_class = make_class()
    alice = make_user(username="Alice")
    bob = make_user(username="Bob")
    make_enrollment(alice, school_class)
    make_enrollment(bob, school_class)
    award(db, alice, school_class.id, 100)
    award(db, alice, None, 900)
    award(db, alice, other_class.id, 700)
    award(db, bob, school_class.id, 200)

    response = client.get(f"{URL}/{school_class.id}/leaderboard", headers=auth_headers(alice))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "items": [
            {"rank": 1, "username": "Bob", "total_points": 200, "is_me": False},
            {"rank": 2, "username": "Alice", "total_points": 100, "is_me": True},
        ],
        "page": 1,
        "page_size": 20,
        "total": 2,
        "has_next_page": False,
    }


def test_owner_can_view_leaderboard(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    response = client.get(f"{URL}/{school_class.id}/leaderboard", headers=auth_headers(owner))

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


def test_unenrolled_student_cannot_view_leaderboard(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    outsider = make_user(role=UserRole.STUDENT)
    school_class = make_class()

    response = client.get(f"{URL}/{school_class.id}/leaderboard", headers=auth_headers(outsider))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


def test_unassigned_staff_cannot_view_leaderboard(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    outsider = make_user(role=UserRole.STAFF)
    school_class = make_class()

    response = client.get(f"{URL}/{school_class.id}/leaderboard", headers=auth_headers(outsider))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


def test_disabled_leaderboard_returns_403_for_enrolled_student(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user()
    school_class = make_class(leaderboard_enabled=False)
    make_enrollment(student, school_class)

    response = client.get(f"{URL}/{school_class.id}/leaderboard", headers=auth_headers(student))

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == ErrorCode.LEADERBOARD_DISABLED.value


def test_disabled_leaderboard_does_not_reveal_setting_to_outsider(
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    outsider = make_user()
    school_class = make_class(leaderboard_enabled=False)

    response = client.get(f"{URL}/{school_class.id}/leaderboard", headers=auth_headers(outsider))

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


def test_pagination_preserves_global_ranks(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    school_class = make_class()
    students = [make_user(username=f"Player{i}") for i in range(3)]
    for i, student in enumerate(students):
        make_enrollment(student, school_class)
        award(db, student, school_class.id, (i + 1) * 100)

    response = client.get(
        f"{URL}/{school_class.id}/leaderboard?page=2&page_size=1",
        headers=auth_headers(students[0]),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"] == 2
    assert body["page_size"] == 1
    assert body["total"] == 3
    assert body["has_next_page"] is True
    assert len(body["items"]) == 1
    assert body["items"][0]["rank"] == 2
    assert body["items"][0]["username"] == students[1].username
    assert body["items"][0]["is_me"] is False


@pytest.mark.parametrize("query", ["?page=0", "?page_size=0", "?page_size=101"])
def test_invalid_pagination_returns_422(
    query: str,
    client: TestClient,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)

    response = client.get(
        f"{URL}/{school_class.id}/leaderboard{query}", headers=auth_headers(student)
    )

    assert response.status_code == 422, response.text


def test_unauthenticated_request_returns_401(client: TestClient) -> None:
    response = client.get(f"{URL}/{uuid4()}/leaderboard")

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED.value


def test_archived_class_leaderboard_preserves_final_standings_for_owner(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    make_enrollment: Callable[..., ClassEnrollment],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    student = make_user(username="ArchivedStudent")
    departed = make_user()
    school_class = make_class()
    make_class_staff(school_class, owner)
    archived_enrolment = make_enrollment(student, school_class)
    departed_enrolment = make_enrollment(departed, school_class)
    award(db, student, school_class.id, 100)
    award(db, departed, school_class.id, 200)

    school_class.archived_at = datetime.now(UTC)
    for enrolment, reason in (
        (archived_enrolment, EnrollmentEndReason.ARCHIVED),
        (departed_enrolment, EnrollmentEndReason.LEFT),
    ):
        enrolment.is_active = False
        enrolment.end_reason = reason
        enrolment.left_at = max(school_class.archived_at, enrolment.joined_at)
    db.flush()

    url = f"{URL}/{school_class.id}/leaderboard"
    response = client.get(url, headers=auth_headers(owner))
    assert response.status_code == 200, response.text
    assert response.json()["items"] == [
        {"rank": 1, "username": student.username, "total_points": 100, "is_me": False},
    ]

    response = client.get(url, headers=auth_headers(student))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == ErrorCode.CLASS_ARCHIVED.value

    response = client.get(url, headers=auth_headers(departed))
    assert response.status_code == 404, response.text
