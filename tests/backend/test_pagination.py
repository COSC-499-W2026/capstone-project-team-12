"""The shared page and page_size query parameters (docs/api-conventions.md, Pagination)."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.pagination import PageParams, page_params
from app.main import create_app


@pytest.fixture
def api() -> TestClient:
    """The real app plus one route that reads the page parameters."""
    app: FastAPI = create_app()

    @app.get("/test-only/paged")
    def paged(params: Annotated[PageParams, Depends(page_params)]) -> dict[str, int]:
        return {"page": params.page, "page_size": params.page_size}

    return TestClient(app)


def test_the_largest_page_size_is_accepted(api: TestClient) -> None:
    response = api.get("/test-only/paged", params={"page_size": 100})

    assert response.json() == {"page": 1, "page_size": 100}


@pytest.mark.parametrize(
    ("params", "field"),
    [({"page": 0}, "page"), ({"page_size": 0}, "page_size"), ({"page_size": 101}, "page_size")],
)
def test_out_of_range_values_are_refused(
    api: TestClient, params: dict[str, int], field: str
) -> None:
    response = api.get("/test-only/paged", params=params)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [(d["in"], d["field"]) for d in error["details"]] == [("query", field)]
