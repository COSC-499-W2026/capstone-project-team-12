"""Every error response uses the format in docs/api-conventions.md."""

from http import HTTPStatus

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.errors import STATUS, AppError, ErrorCode
from app.main import create_app


@pytest.fixture
def api() -> TestClient:
    """The real app plus a few routes that fail on purpose."""
    app: FastAPI = create_app()

    @app.get("/test-only/archived")
    def archived() -> None:
        raise AppError(ErrorCode.CLASS_ARCHIVED, "This class is archived.")

    @app.post("/test-only/upload")
    def upload() -> None:
        raise AppError(ErrorCode.UPLOAD_IN_PROGRESS, headers={"Retry-After": "2"})

    @app.get("/test-only/items/{item_id}")
    def item(item_id: int, page: int = 1) -> dict[str, int]:
        return {"item_id": item_id, "page": page}

    @app.get("/test-only/forbidden")
    def forbidden() -> None:
        raise HTTPException(status_code=403, detail="Unauthorized access")

    @app.get("/test-only/crash")
    def crash() -> None:
        raise RuntimeError("password=hunter2 in SELECT * FROM users")

    # a real server answers 500 instead of raising inside the test
    return TestClient(app, raise_server_exceptions=False)


def test_every_code_has_a_status() -> None:
    assert set(STATUS) == set(ErrorCode)


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ErrorCode.INVALID_ACCESS_CODE, 400),
        (ErrorCode.INVALID_CREDENTIALS, 401),
        (ErrorCode.FORBIDDEN_ROLE, 403),
        (ErrorCode.USER_NOT_FOUND, 404),
        (ErrorCode.CLASS_ARCHIVED, 409),
        (ErrorCode.FILE_TOO_LARGE, 413),
        (ErrorCode.UNSUPPORTED_MEDIA_TYPE, 415),
        (ErrorCode.AUDIO_TOO_LONG, 422),
        (ErrorCode.RATE_LIMITED, 429),
        (ErrorCode.OCR_BUSY, 503),
    ],
)
def test_statuses_match_the_spec(code: ErrorCode, status: int) -> None:
    assert STATUS[code] == status


def test_an_app_error_becomes_the_standard_response(api: TestClient) -> None:
    response = api.get("/test-only/archived")
    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json() == {
        "error": {"code": "CLASS_ARCHIVED", "message": "This class is archived.", "details": []}
    }


def test_headers_are_passed_on(api: TestClient) -> None:
    response = api.post("/test-only/upload")
    assert response.status_code == HTTPStatus.CONFLICT
    assert response.headers["Retry-After"] == "2"
    assert response.json()["error"]["message"] == "Upload in progress"  # default message


def test_validation_errors_list_the_invalid_fields(api: TestClient) -> None:
    response = api.get("/test-only/items/abc", params={"page": "x"})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert {(d["in"], d["field"]) for d in error["details"]} == {
        ("path", "item_id"),
        ("query", "page"),
    }


def test_unknown_route_and_wrong_method_use_the_format(api: TestClient) -> None:
    assert api.get("/api/v1/does-not-exist").json()["error"]["code"] == "NOT_FOUND"
    response = api.post("/test-only/archived")
    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_a_plain_http_exception_still_uses_the_format(api: TestClient) -> None:
    """Code that still raises HTTPException gets a generic code until it moves to AppError."""
    response = api.get("/test-only/forbidden")
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json() == {
        "error": {"code": "FORBIDDEN", "message": "Forbidden", "details": []}
    }


def test_unexpected_errors_hide_the_details(api: TestClient) -> None:
    response = api.get("/test-only/crash")
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text
