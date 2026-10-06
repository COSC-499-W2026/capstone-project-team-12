"""Shared fixtures for backend tests."""

from collections.abc import Callable, Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import create_app
from app.services.storage import MEDIA_URL_PREFIX


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db(test_engine: Engine) -> Iterator[Session]:
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def split_media_url() -> Callable[[str], tuple[str, int, str]]:
    """Split a signed media URL into its (key, expires, signature) parts."""

    def split(url: str) -> tuple[str, int, str]:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        key = parts.path.removeprefix(f"{MEDIA_URL_PREFIX}/")
        return key, int(query["expires"][0]), query["sig"][0]

    return split


@pytest.fixture
def client() -> TestClient:
    # TODO: Override get_db with the test session when database-backed API tests are added.
    return TestClient(create_app())
