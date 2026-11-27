"""Refuses a request body over its size limit with 413, on every route.

FastAPI reads a whole body before any dependency runs, the token check included: a JSON or
urlencoded body into memory, a multipart body into temporary files. Without a limit, a request of
any size, even one without a token, is read before the route can refuse it.

A route that takes a file declares the largest file it takes with @file_limit. A multipart body
sent to it may be that size plus MULTIPART_MARGIN, and over that is 413 FILE_TOO_LARGE. Every
other body (JSON, urlencoded forms, multipart to a route without a file limit) may be up to
MAX_REQUEST_BODY_MB, as FastAPI holds it in memory, and over that is 413 REQUEST_TOO_LARGE. The
services still check each file, and each file in a zip, against its own limit.

A body whose Content-Length is over the limit is refused before the route reads any of it, and
one sent without a Content-Length (chunked) as soon as more than the limit has arrived; the
response closes the connection, so the client cannot go on sending. Bodies are counted as the
route reads them, and only until the response starts, after which a 413 can no longer be sent.
"""

from collections.abc import Callable, Iterable
from http import HTTPStatus
from typing import Any, NamedTuple, TypeVar

from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.routing import BaseRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings, get_settings
from app.errors import AppError, AppHTTPException, ErrorCode
from app.services.uploads import BYTES_PER_KB, BYTES_PER_MB

# the form's boundaries and part headers, and its other fields (e.g. make_primary)
MULTIPART_MARGIN = 64 * BYTES_PER_KB

FileLimit = Callable[[Settings], int]  # the largest file a route takes, in bytes
Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])

_file_limits: dict[Callable[..., Any], FileLimit] = {}


def file_limit(limit: FileLimit) -> Callable[[Endpoint], Endpoint]:
    """Declares the largest file a route takes, read from the settings on each request, e.g.
    @file_limit(lambda settings: settings.max_media_file_mb * BYTES_PER_MB) on the route.
    Without it, a multipart body to the route may be at most MAX_REQUEST_BODY_MB."""

    def declare(endpoint: Endpoint) -> Endpoint:
        _file_limits[endpoint] = limit
        return endpoint

    return declare


def document_body_limit(routes: Iterable[BaseRoute]) -> None:
    """Lists 413 in the OpenAPI document for each route that reads a body. A route without one
    never reads it, so it cannot be refused; a route that already lists 413 keeps its entry."""
    too_large = {
        "description": HTTPStatus.REQUEST_ENTITY_TOO_LARGE.phrase,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
    }
    for route in routes:
        if isinstance(route, APIRoute) and route.body_field is not None:
            route.responses.setdefault(HTTPStatus.REQUEST_ENTITY_TOO_LARGE.value, too_large)


class RequestBodyLimit:
    """ASGI middleware, added with app.add_middleware(RequestBodyLimit)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = _LimitedBody(scope, receive, send)
        await self.app(scope, body.receive, body.send)


class _Limit(NamedTuple):
    max_bytes: int
    code: ErrorCode
    message: str


class _LimitedBody:
    """One request's `receive` and `send`. receive() raises a 413 AppHTTPException once the body
    is known to be over its limit: an HTTPException because FastAPI turns any other exception
    raised while it reads the body into a 400."""

    def __init__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self._scope = scope
        self._receive = receive
        self._send = send
        self._limit: _Limit | None = None  # set when the route first reads the body
        self._received = 0
        self._response_started = False

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self._response_started = True
        await self._send(message)

    async def receive(self) -> Message:
        limit = self._limit
        if limit is None:
            # the route is known by now: the router adds it to the scope before calling it
            limit = self._limit = _limit(self._scope)
            if _declared_length(self._scope) > limit.max_bytes:
                self._refuse(limit)
        message = await self._receive()
        self._received += len(message.get("body", b""))
        if self._received > limit.max_bytes:
            self._refuse(limit)
        return message

    def _refuse(self, limit: _Limit) -> None:
        if self._response_started:  # e.g. a streaming response listening for a disconnect
            return
        raise AppHTTPException(AppError(limit.code, limit.message, headers={"Connection": "close"}))


def _limit(scope: Scope) -> _Limit:
    """The route's file limit plus MULTIPART_MARGIN for a multipart body to a route that declared
    one, which FastAPI streams to disk; MAX_REQUEST_BODY_MB for any other body, which it reads
    into memory."""
    settings = get_settings()
    route = scope.get("route")
    declared = _file_limits.get(route.endpoint) if isinstance(route, APIRoute) else None
    content_type = Headers(scope=scope).get("content-type", "")
    if declared is not None and content_type.startswith("multipart/form-data"):
        max_file = declared(settings)
        return _Limit(
            max_file + MULTIPART_MARGIN,
            ErrorCode.FILE_TOO_LARGE,
            f"The file can be at most {_size(max_file)}",
        )
    max_mb = settings.max_request_body_mb
    return _Limit(
        max_mb * BYTES_PER_MB,
        ErrorCode.REQUEST_TOO_LARGE,
        f"The request body can be at most {max_mb} MB",
    )


def _size(n_bytes: int) -> str:
    """5 MB or 512 KB: the units the settings use."""
    if n_bytes % BYTES_PER_MB == 0:
        return f"{n_bytes // BYTES_PER_MB} MB"
    return f"{n_bytes // BYTES_PER_KB} KB"


def _declared_length(scope: Scope) -> int:
    """The Content-Length, or 0 if there is none (a chunked body) or it is not a number."""
    declared = Headers(scope=scope).get("content-length", "")
    # ASCII digits only, as HTTP allows: isdigit() alone also accepts "²", which int() refuses
    return int(declared) if declared.isascii() and declared.isdecimal() else 0
