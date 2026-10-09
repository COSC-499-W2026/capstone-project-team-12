"""Application errors shared by every component (docs/api-conventions.md, spec Error Codes).

Services and interface functions raise AppError with an ErrorCode. The handlers in app.main
turn it into {"error": {"code", "message", "details"}} with the HTTP status of that code, so
routers never build error responses themselves.
"""

import logging
from enum import StrEnum
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    # from the spec's Error Codes table
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    INVALID_ACCESS_CODE = "INVALID_ACCESS_CODE"
    USERNAME_TAKEN = "USERNAME_TAKEN"
    EMAIL_TAKEN = "EMAIL_TAKEN"
    ALREADY_IN_CLASS_FOR_LANGUAGE = "ALREADY_IN_CLASS_FOR_LANGUAGE"
    TA_SAME_LANGUAGE_CONFLICT = "TA_SAME_LANGUAGE_CONFLICT"
    SESSION_ALREADY_OPEN = "SESSION_ALREADY_OPEN"
    NOT_ENOUGH_CONTENT = "NOT_ENOUGH_CONTENT"
    SESSION_NOT_ACTIVE = "SESSION_NOT_ACTIVE"
    INVALID_STAGE = "INVALID_STAGE"
    NOT_WITHIN_RADIUS = "NOT_WITHIN_RADIUS"
    STALE_POSITION = "STALE_POSITION"
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"
    SHUFFLE_LIMIT_REACHED = "SHUFFLE_LIMIT_REACHED"
    RESET_LIMIT_REACHED = "RESET_LIMIT_REACHED"
    GPS_OVERRIDE_NOT_ALLOWED = "GPS_OVERRIDE_NOT_ALLOWED"
    STATE_CHANGED = "STATE_CHANGED"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    REMOVED_FROM_CLASS = "REMOVED_FROM_CLASS"
    CLASS_ARCHIVED = "CLASS_ARCHIVED"
    FORBIDDEN_ROLE = "FORBIDDEN_ROLE"
    UPLOAD_IN_PROGRESS = "UPLOAD_IN_PROGRESS"
    ALREADY_REPORTED = "ALREADY_REPORTED"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    INVALID_TOKEN = "INVALID_TOKEN"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    AUDIO_TOO_LONG = "AUDIO_TOO_LONG"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    LEADERBOARD_DISABLED = "LEADERBOARD_DISABLED"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    RATE_LIMITED = "RATE_LIMITED"
    OCR_BUSY = "OCR_BUSY"
    # generic codes for the status table in api-conventions.md
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"  # missing, invalid or expired access token
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_CONFLICTS = {
    ErrorCode.USERNAME_TAKEN,
    ErrorCode.EMAIL_TAKEN,
    ErrorCode.ALREADY_IN_CLASS_FOR_LANGUAGE,
    ErrorCode.TA_SAME_LANGUAGE_CONFLICT,
    ErrorCode.SESSION_ALREADY_OPEN,
    ErrorCode.NOT_ENOUGH_CONTENT,
    ErrorCode.SESSION_NOT_ACTIVE,
    ErrorCode.INVALID_STAGE,
    ErrorCode.NOT_WITHIN_RADIUS,
    ErrorCode.STALE_POSITION,
    ErrorCode.ATTEMPTS_EXHAUSTED,
    ErrorCode.SHUFFLE_LIMIT_REACHED,
    ErrorCode.RESET_LIMIT_REACHED,
    ErrorCode.GPS_OVERRIDE_NOT_ALLOWED,
    ErrorCode.STATE_CHANGED,
    ErrorCode.REMOVED_FROM_CLASS,
    ErrorCode.CLASS_ARCHIVED,
    ErrorCode.UPLOAD_IN_PROGRESS,
    ErrorCode.ALREADY_REPORTED,
}

STATUS: dict[ErrorCode, HTTPStatus] = {code: HTTPStatus.CONFLICT for code in _CONFLICTS} | {
    ErrorCode.INVALID_CREDENTIALS: HTTPStatus.UNAUTHORIZED,
    ErrorCode.NOT_AUTHENTICATED: HTTPStatus.UNAUTHORIZED,
    ErrorCode.INVALID_ACCESS_CODE: HTTPStatus.BAD_REQUEST,
    ErrorCode.INVALID_TOKEN: HTTPStatus.BAD_REQUEST,
    ErrorCode.ACCOUNT_DISABLED: HTTPStatus.FORBIDDEN,
    ErrorCode.FORBIDDEN_ROLE: HTTPStatus.FORBIDDEN,
    ErrorCode.LEADERBOARD_DISABLED: HTTPStatus.FORBIDDEN,
    ErrorCode.USER_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.METHOD_NOT_ALLOWED: HTTPStatus.METHOD_NOT_ALLOWED,
    ErrorCode.FILE_TOO_LARGE: HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.AUDIO_TOO_LONG: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.VALIDATION_ERROR: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.RATE_LIMITED: HTTPStatus.TOO_MANY_REQUESTS,
    ErrorCode.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.OCR_BUSY: HTTPStatus.SERVICE_UNAVAILABLE,
}


class AppError(Exception):
    """An expected failure the client should see, e.g. AppError(ErrorCode.CLASS_ARCHIVED).

    The message is for people and may change; clients rely on the code. Pass headers for
    codes that need them, e.g. headers={"Retry-After": "2"} with UPLOAD_IN_PROGRESS.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str = "",
        details: list[Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.message = message or code.replace("_", " ").capitalize()
        self.details = details or []
        self.headers = headers
        super().__init__(f"{code}: {self.message}")

    @property
    def status(self) -> HTTPStatus:
        return STATUS[self.code]

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


# HTTPExceptions raised by FastAPI itself (unknown route, wrong method, missing bearer token)
_FRAMEWORK_CODES = {
    HTTPStatus.UNAUTHORIZED: ErrorCode.NOT_AUTHENTICATED,
    HTTPStatus.NOT_FOUND: ErrorCode.NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
}


def _response(error: AppError) -> JSONResponse:
    return JSONResponse(error.body(), status_code=error.status, headers=error.headers)


async def _app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return _response(exc)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        {
            "in": str(error["loc"][0]),  # body, query, path or header
            "field": ".".join(str(part) for part in error["loc"][1:]),
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return _response(AppError(ErrorCode.VALIDATION_ERROR, "Request validation failed", details))


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _FRAMEWORK_CODES.get(HTTPStatus(exc.status_code))
    if code is None:
        # a status we have no code for (e.g. a plain HTTPException(403)): keep the status and
        # use the standard name as the code, so the body still has the standard format
        status = HTTPStatus(exc.status_code)
        body = {"error": {"code": status.name, "message": status.phrase, "details": []}}
        return JSONResponse(body, status_code=status, headers=exc.headers)
    headers = dict(exc.headers) if exc.headers else None
    return _response(AppError(code, headers=headers))


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # logged with the stack trace, but the client never sees it (api-conventions.md)
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return _response(AppError(ErrorCode.INTERNAL_ERROR, "Something went wrong"))


def add_error_handlers(app: FastAPI) -> None:
    """Make every error response use the {"error": {...}} format."""
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
