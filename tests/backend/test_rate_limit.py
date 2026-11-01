"""Tests for API rate-limiting"""

from fastapi.testclient import TestClient

from app.errors import ErrorCode
from app.limiter import limiter


def test_global_rate_limit(client: TestClient):
    limiter.reset()  # prevent other tests leaking into here
    try:
        for _i in range(100):
            response = client.post("/api/v1/auth/refresh")
            assert response.status_code == 401
        response = client.post("/api/v1/auth/refresh")
        assert response.json()["error"]["code"] == ErrorCode.RATE_LIMITED
    finally:
        limiter.reset()  # prevent leak into other tests


def test_login_rate_limit(client: TestClient):
    limiter.reset()
    try:
        login_data_1 = {"username": "dummy@email.com", "password": "ValidPassword1"}
        login_data_2 = {"username": "fake@email.com", "password": "ValidPassword1"}
        for _i in range(5):
            response = client.post("/api/v1/auth/login", data=login_data_1)
            assert response.status_code == 401
        # 6th try on same email/IP, should return 429
        response = client.post("/api/v1/auth/login", data=login_data_1)
        assert response.json()["error"]["code"] == ErrorCode.RATE_LIMITED

        # Uses different email, should return 401 instead of 429
        response = client.post("/api/v1/auth/login", data=login_data_2)
        assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS
    finally:
        limiter.reset()
