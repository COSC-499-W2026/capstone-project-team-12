"""Refuses a request body over its size limit with 413 FILE_TOO_LARGE, on every route.

FastAPI reads a whole body before any dependency runs, the token check included: a JSON or
urlencoded body into memory, a multipart body into temporary files. Without a limit, a request of
any size, even one without a token, is read before the route can refuse it.

Two limits: a multipart body sent to a route that takes a file (an upload or a zip import) may be
up to MAX_UPLOAD_BODY_MB, as it is streamed to disk; every other body up to MAX_REQUEST_BODY_MB,
as it is held in memory. The services still check each file against MAX_MEDIA_FILE_MB.

A body whose Content-Length is over the limit is refused before the route reads any of it, and
one sent without a Content-Length (chunked) as soon as more than the limit has arrived; the
response closes the connection, so the client cannot go on sending. Bodies are counted as the
route reads them, and only until the response starts, after which a 413 can no longer be sent.
"""

from http import HTTPStatus

from fastapi import params
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings

_ONE_MB = 1024 * 1024


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


class _LimitedBody:
    """One request's `receive` and `send`. receive() raises a 413 HTTPException once the body
    is known to be over its limit: an HTTPException because FastAPI turns any other exception
    raised while it reads the body into a 400 (app.errors gives it the code FILE_TOO_LARGE)."""

    def __init__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self._scope = scope
        self._receive = receive
        self._send = send
        self._limit_mb: int | None = None  # set when the route first reads the body
        self._received = 0
        self._response_started = False

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self._response_started = True
        await self._send(message)

    async def receive(self) -> Message:
        if self._limit_mb is None:
            # the route is known by now: the router adds it to the scope before calling it
            self._limit_mb = _limit_mb(self._scope)
            if _declared_length(self._scope) > self._limit_mb * _ONE_MB:
                self._refuse()
        message = await self._receive()
        self._received += len(message.get("body", b""))
        if self._received > self._limit_mb * _ONE_MB:
            self._refuse()
        return message

    def _refuse(self) -> None:
        if self._response_started:  # e.g. a streaming response listening for a disconnect
            return
        raise HTTPException(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            f"The request body can be at most {self._limit_mb} MB",
            headers={"Connection": "close"},
        )


def _limit_mb(scope: Scope) -> int:
    """MAX_UPLOAD_BODY_MB for a multipart body to a route that takes a file, which FastAPI
    streams to disk; MAX_REQUEST_BODY_MB for any other body, which it reads into memory."""
    settings = get_settings()
    route = scope.get("route")
    takes_file = (
        isinstance(route, APIRoute)
        and route.body_field is not None
        and isinstance(route.body_field.field_info, params.File)
    )
    content_type = Headers(scope=scope).get("content-type", "")
    if takes_file and content_type.startswith("multipart/form-data"):
        return settings.max_upload_body_mb
    return settings.max_request_body_mb


def _declared_length(scope: Scope) -> int:
    """The Content-Length, or 0 if there is none (a chunked body) or it is not a number."""
    declared = Headers(scope=scope).get("content-length", "")
    # ASCII digits only, as HTTP allows: isdigit() alone also accepts "²", which int() refuses
    return int(declared) if declared.isascii() and declared.isdecimal() else 0
