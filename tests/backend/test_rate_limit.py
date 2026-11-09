"""Tests for API rate-limiting. An autouse fixture in conftest.py resets the limiter per test."""

from http import HTTPStatus

from fastapi.testclient import TestClient

from app.config import get_settings
from app.errors import ErrorCode


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
