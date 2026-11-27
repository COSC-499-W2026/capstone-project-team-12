"""The request body limits (app.api.body_limit): each upload route's file limit, and
MAX_REQUEST_BODY_MB for every other body.

Through the app over HTTP, on the OpenAPI document, and on the middleware alone to show how much
of a body is taken from the server before it is refused.
"""

import asyncio
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.body_limit import RequestBodyLimit
from app.config import get_settings
from app.errors import AppHTTPException, ErrorCode
from app.main import create_app
from app.services.uploads import BYTES_PER_KB, BYTES_PER_MB

CHUNK = 64 * BYTES_PER_KB  # how much of a body the server passes on at a time
# a route that needs a token and reads a JSON body: FastAPI reads the body first
GAMES_URL = "/api/v1/games"
LOGIN_URL = "/api/v1/auth/login"  # reads a form, but takes no file
CSV_IMPORT_URL = "/api/v1/content/import/locations"

# every route that takes a file: its URL, the form field and the file limit set by `limits`
FILE_ROUTES = {
    "uploadClueAudio": (f"/api/v1/content/locations/{uuid4()}/clues/1/fr/audio", "file", "2 MB"),
    "uploadPhoto": (f"/api/v1/games/{uuid4()}/verification/photo", "photo", "2 MB"),
    "importLocations": (CSV_IMPORT_URL, "file", "512 KB"),
    "importClueAudio": ("/api/v1/content/import/clue-audio", "file", "3 MB"),
    "importFeedbackAudio": ("/api/v1/content/import/feedback-audio", "file", "3 MB"),
}
UNIT = {"KB": BYTES_PER_KB, "MB": BYTES_PER_MB}


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A different limit for each kind of body during the test, so a route given the wrong one
    fails: 1 MB for a body held in memory, 2 MB for a recording or photo, 512 KB for a CSV and
    3 MB for a zip."""
    monkeypatch.setenv("MAX_REQUEST_BODY_MB", "1")
    monkeypatch.setenv("MAX_MEDIA_FILE_MB", "2")
    monkeypatch.setenv("MAX_IMPORT_FILE_KB", "512")
    monkeypatch.setenv("MAX_IMPORT_ZIP_MB", "3")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()  # the next caller reads the restored environment


def assert_too_large(response: Response, code: ErrorCode, message: str) -> None:
    """413 with `code` and `message`, closing the connection."""
    assert response.status_code == 413, response.text
    assert response.json()["error"] == {"code": code, "message": message, "details": []}
    assert response.headers["connection"] == "close"


def assert_request_too_large(response: Response) -> None:
    assert_too_large(response, ErrorCode.REQUEST_TOO_LARGE, "The request body can be at most 1 MB")


def in_bytes(limit: str) -> int:
    """512 KB -> 524288."""
    number, unit = limit.split()
    return int(number) * UNIT[unit]


class Body:
    """A body of `size` bytes as the server passes it on, in CHUNKs."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.taken = 0

    async def receive(self) -> Message:
        chunk = min(CHUNK, self.size - self.taken)
        self.taken += chunk
        return {"type": "http.request", "body": bytes(chunk), "more_body": self.taken < self.size}


def run(app: ASGIApp, headers: list[tuple[bytes, bytes]], body: Body) -> None:
    """Send a request with `headers` and `body` through the middleware to `app`."""

    async def send(message: Message) -> None:
        pass

    scope = {"type": "http", "headers": headers}
    asyncio.run(RequestBodyLimit(app)(scope, body.receive, send))


async def read_body(scope: Scope, receive: Receive, send: Send) -> None:
    while (await receive())["more_body"]:
        pass


async def respond_then_read_body(scope: Scope, receive: Receive, send: Send) -> None:
    # as a streaming response does while it listens for the client to disconnect
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await read_body(scope, receive, send)
    await send({"type": "http.response.body", "body": b"done"})


def test_a_body_over_the_limit_is_413_even_without_a_token(
    client: TestClient, limits: None
) -> None:
    assert_request_too_large(client.post(GAMES_URL, content=bytes(BYTES_PER_MB + 1)))


def test_a_body_at_the_limit_is_read(
    client: TestClient, limits: None, assert_error: Callable[..., None]
) -> None:
    response = client.post(GAMES_URL, content=bytes(BYTES_PER_MB))

    # read whole, then refused for the missing token
    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_a_body_without_a_content_length_over_the_limit_is_413(
    client: TestClient, limits: None
) -> None:
    response = client.post(GAMES_URL, content=iter([bytes(BYTES_PER_MB), b"x"]))  # sent chunked

    assert_request_too_large(response)


def test_a_multipart_body_to_a_route_without_a_file_gets_the_request_limit(
    client: TestClient, limits: None
) -> None:
    response = client.post(LOGIN_URL, files={"username": ("a.bin", bytes(BYTES_PER_MB + 1))})

    assert_request_too_large(response)


@pytest.mark.parametrize(("url", "field", "limit"), FILE_ROUTES.values(), ids=FILE_ROUTES)
def test_a_file_at_the_route_limit_is_read(
    client: TestClient,
    limits: None,
    assert_error: Callable[..., None],
    url: str,
    field: str,
    limit: str,
) -> None:
    response = client.post(url, files={field: ("a", bytes(in_bytes(limit)))})

    # read whole, then refused for the missing token
    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


@pytest.mark.parametrize(("url", "field", "limit"), FILE_ROUTES.values(), ids=FILE_ROUTES)
def test_a_file_over_the_route_limit_and_margin_is_413(
    client: TestClient, limits: None, url: str, field: str, limit: str
) -> None:
    # the file alone is as large as the body may be: the limit plus the 64 KB multipart margin
    response = client.post(url, files={field: ("a", bytes(in_bytes(limit) + 64 * BYTES_PER_KB))})

    assert_too_large(response, ErrorCode.FILE_TOO_LARGE, f"The file can be at most {limit}")


def operations() -> list[dict[str, Any]]:
    """Every operation in the app's OpenAPI document."""
    paths = create_app().openapi()["paths"].values()
    return [operation for path in paths for operation in path.values()]


def test_every_route_that_takes_a_file_is_checked_above() -> None:
    # a file route left out of FILE_ROUTES may have no file limit, so an upload over
    # MAX_REQUEST_BODY_MB would be refused
    takes_file = {
        op["operationId"]
        for op in operations()
        if "multipart/form-data" in op.get("requestBody", {}).get("content", {})
    }

    assert takes_file == set(FILE_ROUTES)


def test_a_body_that_is_not_multipart_gets_the_request_limit_on_a_file_route(
    client: TestClient, limits: None, assert_error: Callable[..., None]
) -> None:
    # FastAPI reads a urlencoded form into memory, even for a route that takes a file; the CSV
    # route's own limit (512 KB) is for a file, so this body may be larger
    accepted = client.post(CSV_IMPORT_URL, data={"file": "x" * (BYTES_PER_MB * 3 // 4)})
    refused = client.post(CSV_IMPORT_URL, data={"file": "x" * BYTES_PER_MB})

    assert_error(accepted, 401, ErrorCode.NOT_AUTHENTICATED)
    assert_request_too_large(refused)


def test_413_is_listed_with_the_error_body_only_for_routes_that_take_a_body() -> None:
    error_body = {"$ref": "#/components/schemas/ErrorResponse"}

    for op in operations():
        too_large = op["responses"].get("413")
        if "requestBody" in op:
            assert too_large["content"]["application/json"]["schema"] == error_body
        else:
            assert too_large is None, op["operationId"]


def test_a_declared_length_over_the_limit_is_refused_before_any_is_read(limits: None) -> None:
    body = Body(10 * BYTES_PER_MB)

    with pytest.raises(AppHTTPException, match="413"):
        run(read_body, [(b"content-length", str(body.size).encode())], body)

    assert body.taken == 0


# without a usable Content-Length the body is counted; "²" passes str.isdigit but not int()
@pytest.mark.parametrize("headers", [[], [(b"content-length", "²".encode("latin-1"))]])
def test_a_body_is_refused_one_chunk_past_the_limit(
    limits: None, headers: list[tuple[bytes, bytes]]
) -> None:
    body = Body(10 * BYTES_PER_MB)

    with pytest.raises(AppHTTPException, match="413"):
        run(read_body, headers, body)

    assert body.taken == BYTES_PER_MB + CHUNK  # not the rest of it


def test_nothing_is_refused_once_the_response_has_started(limits: None) -> None:
    body = Body(10 * BYTES_PER_MB)

    run(respond_then_read_body, [(b"content-length", str(body.size).encode())], body)

    assert body.taken == body.size  # a 413 could no longer be sent


def test_other_connections_pass_through_unchanged() -> None:
    passed: list[Receive] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        passed.append(receive)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        pass

    asyncio.run(RequestBodyLimit(app)({"type": "lifespan"}, receive, send))

    assert passed == [receive]
