"""Teaching assistant routes. What they do is tested in test_class_staff_service.py."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from app.accounts.interface import invite_assistant
from app.errors import ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole

INVITATIONS = "/api/v1/me/invitations"
CLASSES = "/api/v1/teacher/classes"


def test_the_invitee_lists_and_answers_invitations(
    client: TestClient,
    db: Session,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    student = make_user()
    first, second = make_class(name="JAPN 101"), make_class(name="JAPN 201")
    accepted = invite_assistant(first.id, owner, student.email, db)
    declined = invite_assistant(second.id, owner, student.email, db)
    headers = auth_headers(student)

    listed = client.get(INVITATIONS, headers=headers)

    assert listed.status_code == 200
    assert {(item["id"], item["class_name"]) for item in listed.json()} == {
        (str(accepted.id), "JAPN 101"),
        (str(declined.id), "JAPN 201"),
    }
    answers = [
        client.post(f"{INVITATIONS}/{accepted.id}/accept", headers=headers),
        client.post(f"{INVITATIONS}/{declined.id}/decline", headers=headers),
    ]
    assert [(answer.status_code, answer.json()) for answer in answers] == [
        (200, {"class_id": str(first.id), "status": "accepted"}),
        (200, {"class_id": str(second.id), "status": "declined"}),
    ]


def test_an_assistant_who_accepted_can_export_participation_until_removed(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)
    student = make_user()
    staff = f"{CLASSES}/{school_class.id}/staff"
    body = {"email": student.email}

    def export() -> Response:
        dates = {"start_date": "2026-11-14", "end_date": "2026-11-14"}
        url = f"{CLASSES}/{school_class.id}/participation.csv"
        return client.get(url, params=dates, headers=auth_headers(student))

    invited = client.post(staff, json=body, headers=auth_headers(owner))
    assert invited.status_code == 201
    client.post(f"{INVITATIONS}/{invited.json()['id']}/accept", headers=auth_headers(student))
    assert export().status_code == 200

    removed = client.request("DELETE", staff, json=body, headers=auth_headers(owner))

    assert removed.status_code == 204
    assert export().status_code == 403


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_an_assistant_cannot_add_or_remove_staff(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    assert_error: Callable[[Response, int, ErrorCode], None],
    method: str,
) -> None:
    school_class = make_class()
    assistant = make_user()
    make_class_staff(school_class, assistant, ClassStaffRole.ASSISTANT)

    response = client.request(
        method,
        f"{CLASSES}/{school_class.id}/staff",
        json={"email": make_user().email},
        headers=auth_headers(assistant),
    )

    assert_error(response, 403, ErrorCode.FORBIDDEN_ROLE)
