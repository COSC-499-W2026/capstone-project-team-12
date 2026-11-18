"""#322: the global limit counts per user with a valid access token, otherwise per IP."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from jwt import encode

from app.config import get_settings
from app.errors import ErrorCode
from app.models.accounts import User

LIMIT = 3
URL = "/api/v1/me/profile"
AUTH_URL = "/api/v1/auth/refresh"


@pytest.fixture(autouse=True)
def small_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "global_rate_limit", LIMIT)


@pytest.fixture
def send(client: TestClient) -> Callable[..., Response]:
    def get(headers: dict[str, str] | None = None) -> Response:
        return client.get(URL, headers=headers or {})

    return get


def use_up(send: Callable[..., Response], headers: dict[str, str] | None = None) -> None:
    for _ in range(LIMIT):
        assert send(headers).status_code != 429


def is_limited(response: Response) -> bool:
    return (
        response.status_code == 429
        and response.json()["error"]["code"] == ErrorCode.RATE_LIMITED.value
    )


def test_a_signed_in_user_has_their_own_limit(
    send: Callable[..., Response],
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    alice, bob = auth_headers(make_user()), auth_headers(make_user())

    use_up(send, alice)

    assert is_limited(send(alice))
    assert not is_limited(send(bob))
    assert not is_limited(send())


def test_an_anonymous_client_is_limited_by_ip(
    send: Callable[..., Response],
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    use_up(send)

    assert is_limited(send())
    assert not is_limited(send(auth_headers(make_user())))


def test_the_auth_routes_count_against_the_ip(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    for _ in range(LIMIT):
        assert client.post(AUTH_URL).status_code != 429

    assert is_limited(client.post(AUTH_URL, headers=auth_headers(make_user())))


def expired_token() -> str:
    claims = {"sub": str(uuid4()), "exp": datetime.now(UTC) - timedelta(minutes=1)}
    return encode(claims, str(get_settings().access_token_secret_key), "HS256")


@pytest.mark.parametrize(
    "authorization",
    ["Bearer not-a-token", f"Bearer {expired_token()}", "Basic abc", "Bearer"],
    ids=["bad token", "expired", "basic", "empty"],
)
def test_a_bad_token_counts_against_the_ip(
    send: Callable[..., Response], authorization: str
) -> None:
    use_up(send)

    assert is_limited(send({"Authorization": authorization}))


def test_every_api_route_documents_429(client: TestClient) -> None:
    paths = client.app.openapi()["paths"]
    missing = [
        f"{method.upper()} {path}"
        for path, item in paths.items()
        if path.startswith("/api/v1/")
        for method, operation in item.items()
        if "429" not in operation["responses"]
    ]

    assert not missing


def use_up(send: Callable[..., Response], headers: dict[str, str] | None = None) -> None:
    for _ in range(LIMIT):
        # 200 signed in, 401 otherwise: a 404 would mean URL isn't a route, so nothing is counted
        assert send(headers).status_code in (HTTPStatus.OK, HTTPStatus.UNAUTHORIZED)