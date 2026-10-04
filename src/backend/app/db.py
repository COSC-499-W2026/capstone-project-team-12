"""Database engine, session factory and declarative base."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for all ORM models. Import models in alembic/env.py so migrations see them."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
