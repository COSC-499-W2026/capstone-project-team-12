"""Smoke test for classroom creation."""

from collections.abc import Callable
from datetime import UTC, datetime
from time import time

from fastapi.testclient import TestClient
from jwt import encode

from app.config import get_settings
from app.models.accounts import User
from app.models.enums import UserRole


def test_staff_can_create_class(
    client: TestClient,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    token = encode(
        {"sub": str(teacher.id), "exp": int(time()) + 3600},
        str(get_settings().access_token_secret_key),
        "HS256",
    )
    client.headers["Authorization"] = f"Bearer {token}"

    response = client.post(
        "/api/v1/teacher/classes",
        json={
            "name": "Japanese 101",
            "semester": f"{datetime.now(UTC).year + 1}W1",
            "target_language": "ja",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Japanese 101"
    assert len(response.json()["access_code"]) == 8
