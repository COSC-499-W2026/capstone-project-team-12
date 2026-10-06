"""Column and CHECK helpers shared by every model, so all tables follow the same conventions."""

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enums import Language


def uuid_pk() -> Mapped[UUID]:
    """UUID primary key. uuid4 gives an id before flush; gen_random_uuid() covers raw SQL."""
    return mapped_column(primary_key=True, default=uuid4, server_default=func.gen_random_uuid())


def created_at() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


def updated_at() -> Mapped[datetime]:
    """Only ORM updates refresh this; a raw UPDATE has to set it itself."""
    return mapped_column(server_default=func.now(), onupdate=func.now())


def in_check(column: str, values: Iterable[str] | type[StrEnum], name: str) -> CheckConstraint:
    """CHECK (column IN (...)) for a text column holding one of a fixed set of values.

    A NULL value passes, so nullable columns need no extra condition.
    """
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


def language_check(column: str = "language", name: str = "language") -> CheckConstraint:
    """The language codes appear in many tables; adding a language only changes Language."""
    return in_check(column, Language, name)
