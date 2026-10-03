"""Shared fixtures for backend tests (database fixtures come with the CI task)."""

"""Shared fixtures for backend tests."""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.main import create_app

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://hunt:hunt@localhost:5432/hunt_test",
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(TEST_DATABASE_URL)
    connection = engine.connect()
    transaction = connection.begin()

    session = Session(bind=connection)

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()