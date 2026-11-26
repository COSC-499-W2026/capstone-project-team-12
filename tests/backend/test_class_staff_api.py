"""Teaching assistant routes. What they do is tested in test_class_staff_service.py."""

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.accounts.interface import invite_assistant
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import UserRole

INVITATIONS = "/api/v1/me/invitations"


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
