"""Shared fixtures for backend tests."""

from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
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
def transaction_calls(db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every commit, flush and rollback made on the test session while the test runs.

    Flush is spied on directly: SQLAlchemy only fires its flush events when something is
    pending, so listening for them would miss a flush of a clean session, and the automatic
    flush before a query. The transaction is opened first because rolling back a session
    without one does nothing, which would hide a rollback inside a lookup.
    """
    db.connection()
    calls: list[str] = []
    real_flush = db.flush

    def spy_flush(*args: Any, **kwargs: Any) -> None:
        calls.append("flush")
        real_flush(*args, **kwargs)

    def record(name: str) -> Callable[..., None]:
        return lambda *_: calls.append(name)

    monkeypatch.setattr(db, "flush", spy_flush)
    event.listen(db, "after_commit", record("commit"))
    event.listen(db, "after_soft_rollback", record("rollback"))
    yield calls


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
