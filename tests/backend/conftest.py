"""Shared fixtures for backend tests (database fixtures come with the CI task)."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
