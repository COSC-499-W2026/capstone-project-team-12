"""The request body limits (app.api.body_limit: MAX_REQUEST_BODY_MB, MAX_UPLOAD_BODY_MB).

Through the app over HTTP, through a small app with a route that takes a file, and on the
middleware alone to show how much of a body is taken from the server before it is refused.
"""

import asyncio
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from httpx2 import Response
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.body_limit import RequestBodyLimit
from app.config import get_settings
from app.errors import ErrorCode, add_error_handlers

ONE_MB = 1024 * 1024
CHUNK = 64 * 1024  # how much of a body the server passes on at a time
# a route that needs a token and reads a JSON body: FastAPI reads the body first
GAMES_URL = "/api/v1/games"
LOGIN_URL = "/api/v1/auth/login"  # reads a form, but takes no file


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """1 MB for a body held in memory and 2 MB for an upload, during the test."""
    monkeypatch.setenv("MAX_REQUEST_BODY_MB", "1")
    monkeypatch.setenv("MAX_UPLOAD_BODY_MB", "2")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()  # the next caller reads the restored environment


@pytest.fixture
def upload_client() -> TestClient:
    """An app with the limit and a route that takes a file, answering with the file's size."""
    app = FastAPI()
    app.add_middleware(RequestBodyLimit)
    add_error_handlers(app)

    @app.post("/upload")
    def upload(file: UploadFile) -> int:
        return len(file.file.read())

    return TestClient(app)


def assert_too_large(response: Response, limit_mb: int) -> None:
    """413 FILE_TOO_LARGE naming the limit, closing the connection."""
    assert response.status_code == 413, response.text
    error = response.json()["error"]
    assert error["code"] == ErrorCode.FILE_TOO_LARGE
    assert error["message"] == f"The request body can be at most {limit_mb} MB"
    assert response.headers["connection"] == "close"


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
    assert_too_large(client.post(GAMES_URL, content=bytes(ONE_MB + 1)), 1)


def test_a_body_at_the_limit_is_read(
    client: TestClient, limits: None, assert_error: Callable[..., None]
) -> None:
    response = client.post(GAMES_URL, content=bytes(ONE_MB))

    # read whole, then refused for the missing token
    assert_error(response, 401, ErrorCode.NOT_AUTHENTICATED)


def test_a_body_without_a_content_length_over_the_limit_is_413(
    client: TestClient, limits: None
) -> None:
    response = client.post(GAMES_URL, content=iter([bytes(ONE_MB), b"x"]))  # sent chunked

    assert_too_large(response, 1)


def test_a_multipart_body_to_a_route_without_a_file_gets_the_smaller_limit(
    client: TestClient, limits: None
) -> None:
    response = client.post(LOGIN_URL, files={"username": ("a.bin", bytes(ONE_MB + 1))})

    assert_too_large(response, 1)


def test_an_upload_to_a_route_that_takes_a_file_gets_the_upload_limit(
    upload_client: TestClient, limits: None
) -> None:
    accepted = upload_client.post("/upload", files={"file": ("a.mp3", bytes(ONE_MB * 3 // 2))})
    refused = upload_client.post("/upload", files={"file": ("a.mp3", bytes(2 * ONE_MB + 1))})

    assert (accepted.status_code, accepted.json()) == (200, ONE_MB * 3 // 2)
    assert_too_large(refused, 2)


def test_a_body_that_is_not_multipart_gets_the_smaller_limit_on_any_route(
    upload_client: TestClient, limits: None
) -> None:
    # FastAPI reads a urlencoded form into memory, even for a route that takes a file
    response = upload_client.post("/upload", data={"file": "x" * (ONE_MB * 3 // 2)})

    assert_too_large(response, 1)


def test_a_declared_length_over_the_limit_is_refused_before_any_is_read(limits: None) -> None:
    body = Body(10 * ONE_MB)

    with pytest.raises(HTTPException, match="413"):
        run(read_body, [(b"content-length", str(body.size).encode())], body)

    assert body.taken == 0


# without a usable Content-Length the body is counted; "²" passes str.isdigit but not int()
@pytest.mark.parametrize("headers", [[], [(b"content-length", "²".encode("latin-1"))]])
def test_a_body_is_refused_one_chunk_past_the_limit(
    limits: None, headers: list[tuple[bytes, bytes]]
) -> None:
    body = Body(10 * ONE_MB)

    with pytest.raises(HTTPException, match="413"):
        run(read_body, headers, body)

    assert body.taken == ONE_MB + CHUNK  # not the rest of it


def test_nothing_is_refused_once_the_response_has_started(limits: None) -> None:
    body = Body(10 * ONE_MB)

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
