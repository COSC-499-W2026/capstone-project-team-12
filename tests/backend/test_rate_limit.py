"""Tests for API rate-limiting. An autouse fixture in conftest.py resets the limiter per test."""

from collections.abc import Callable
from http import HTTPStatus
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.errors import ErrorCode
from app.models.accounts import User


def test_global_rate_limit_is_shared_by_all_routes(client: TestClient) -> None:
    for _i in range(get_settings().global_rate_limit):
        response = client.post("/api/v1/auth/refresh")
        assert response.status_code == HTTPStatus.UNAUTHORIZED
    # the next request from this IP is limited on any API route, not just /auth/refresh
    response = client.get("/api/v1/games/current")
    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.json()["error"]["code"] == ErrorCode.RATE_LIMITED
    assert "Retry-After" in response.headers
    # health checks are never limited
    response = client.get("/health")
    assert response.status_code == HTTPStatus.OK


def test_login_rate_limit_per_email_and_ip(client: TestClient) -> None:
    login_data_1 = {"username": "dummy@email.com", "password": "ValidPassword1"}
    login_data_2 = {"username": "fake@email.com", "password": "ValidPassword1"}
    for _i in range(get_settings().login_attempts_per_email):
        response = client.post("/api/v1/auth/login", data=login_data_1)
        assert response.status_code == HTTPStatus.UNAUTHORIZED
    # one more try on the same email/IP, should return 429
    response = client.post("/api/v1/auth/login", data=login_data_1)
    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.json()["error"]["code"] == ErrorCode.RATE_LIMITED
    assert "Retry-After" in response.headers

    # Uses different email, should return 401 instead of 429
    response = client.post("/api/v1/auth/login", data=login_data_2)
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS


def test_login_is_limited_per_ip_across_emails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "login_attempts_per_ip", 3)
    for i in range(3):
        form = {"username": f"user{i}@example.com", "password": "ValidPassword1"}
        assert client.post("/api/v1/auth/login", data=form).status_code == HTTPStatus.UNAUTHORIZED

    form = {"username": "another@example.com", "password": "ValidPassword1"}
    response = client.post("/api/v1/auth/login", data=form)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert "Retry-After" in response.headers


def test_register_is_limited_per_ip(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "register_attempts_per_ip", 3)
    other_ip = TestClient(client.app, client=("10.0.0.2", 50000))
    url = "/api/v1/auth/register"
    for _i in range(3):
        assert client.post(url, json={}).status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    response = client.post(url, json={})

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.json()["error"]["code"] == ErrorCode.RATE_LIMITED
    assert "Retry-After" in response.headers
    assert other_ip.post(url, json={}).status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_photo_uploads_are_limited_per_user(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    monkeypatch.setattr(get_settings(), "photo_uploads_per_user", 3)
    alice, bob = auth_headers(make_user()), auth_headers(make_user())
    url = f"/api/v1/games/{uuid4()}/verification/photo"
    photo = {"photo": ("sign.jpg", b"not checked", "image/jpeg")}
    for _ in range(3):
        assert client.post(url, files=photo, headers=alice).status_code == 404

    response = client.post(url, files=photo, headers=alice)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.json()["error"]["code"] == ErrorCode.RATE_LIMITED
    assert "Retry-After" in response.headers
    assert client.post(url, files=photo, headers=bob).status_code == 404
