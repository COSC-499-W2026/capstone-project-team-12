"""Paged collection responses (docs/api-conventions.md, Pagination)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Generic, Self, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")


@dataclass(frozen=True)
class PageParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageParams:
    """The standard page and page_size query parameters."""
    return PageParams(page, page_size)


class Page(BaseModel, Generic[T]):
    """Subclass it with a name per endpoint, so the OpenAPI schema keeps a readable name."""

    items: list[T]
    page: int
    page_size: int
    total: int
    has_next_page: bool

    @classmethod
    def of(cls, items: Sequence[T], params: PageParams, total: int) -> Self:
        """Build the page from items already sliced to it."""
        return cls(
            items=list(items),
            page=params.page,
            page_size=params.page_size,
            total=total,
            has_next_page=params.offset + params.page_size < total,
        )
