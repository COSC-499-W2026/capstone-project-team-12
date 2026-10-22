"""Database engine, session factory and declarative base."""

from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import DateTime, MetaData, Text, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Fixed constraint names, so a later migration can drop or change a constraint by name.
# A CheckConstraint needs name=..., e.g. name="role" on users becomes ck_users_role.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(column_0_label)s",
}


class Base(DeclarativeBase):
    """Base class for all ORM models. Every model module is imported in app/models/__init__.py."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # str columns are Postgres text, and every timestamp is timestamptz (stored in UTC)
    type_annotation_map = {str: Text(), datetime: DateTime(timezone=True)}


def violated_constraint(error: IntegrityError) -> str | None:
    """The name of the constraint an IntegrityError broke, or None if the driver didn't say."""
    diagnostics = getattr(error.orig, "diag", None)
    return None if diagnostics is None else diagnostics.constraint_name


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
