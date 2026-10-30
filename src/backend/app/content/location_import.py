"""5.4 Bulk import: locations from a CSV (A20).

The first row names the columns, in any order: name, building_name, room_number and entrances,
and optionally floor_number and gps_radius_meters (a blank cell means no floor or the default
radius). Each further row is one location; entrances holds every entrance in one quoted cell,
"lat,lng;lat,lng". Accepted answers come from the room number. Empty lines, and the empty cells
spreadsheets add at the end of a line, are ignored. The spec does not define the columns, so
they still need the client's confirmation.

Every row is checked as a location created through the API, and the valid rows are imported
even when others are rejected. A room number already in use, by an earlier row or by an inactive
location, is rejected: a deactivated location is reactivated (`is_active`), not imported again.
A problem with the file itself (not UTF-8 CSV, wrong header) rejects the whole file; that
includes a quoting mistake, because after one the rows can no longer be told apart.

A dry run makes the same inserts in a savepoint of its own and rolls back only that savepoint,
also when a row raises, so its preview rejects exactly the rows the import would and the
caller's transaction is left as it was (as create_location's savepoint does for a duplicate).
Otherwise this flushes and never commits or rolls back: the router owns the transaction.
"""

import csv
import io
from collections import Counter
from collections.abc import Iterator
from typing import Annotated

from pydantic import BeforeValidator, Field, ValidationError
from pydantic_core import ErrorDetails
from sqlalchemy.orm import Session

from app.content.locations import create_location, invalid_field
from app.content.schemas import (
    EntrancePoint,
    ImportedRow,
    LocationFields,
    LocationImportReport,
    RejectedRow,
)
from app.errors import AppError, ErrorCode

COLUMNS = ("name", "building_name", "floor_number", "room_number", "entrances", "gps_radius_meters")
OPTIONAL_COLUMNS = frozenset({"floor_number", "gps_radius_meters"})
REQUIRED_COLUMNS = frozenset(COLUMNS) - OPTIONAL_COLUMNS


def _entrance_points(cell: str) -> list[dict[str, str]]:
    """The points of an entrances cell, "lat,lng;lat,lng" (a stray ';' is ignored), for
    EntrancePoint to check."""
    pairs = [pair.split(",") for pair in cell.split(";") if pair.strip()]
    if any(len(pair) != 2 for pair in pairs):
        example = '"49.9394,-119.3948;49.9401,-119.3952"'
        raise ValueError(f"expected lat,lng pairs separated by ';' in one quoted cell: {example}")
    return [{"lat": lat, "lng": lng} for lat, lng in pairs]


class LocationRow(LocationFields):
    """A location as a CSV row gives it: every entrance in the one entrances cell."""

    gps_points: Annotated[list[EntrancePoint], BeforeValidator(_entrance_points)] = Field(
        min_length=1, alias="entrances"
    )


def import_locations(csv_data: bytes, db: Session, dry_run: bool = False) -> LocationImportReport:
    """Import each valid row of `csv_data` and report every row, imported or rejected with its
    reason; VALIDATION_ERROR on `file` if the whole file is refused (see above)."""
    header, rows = _read_csv(csv_data)
    report = LocationImportReport(dry_run=dry_run)
    preview = db.begin_nested() if dry_run else None
    try:
        for row, cells in rows:
            _import_row(row, header, cells, report, db)
    finally:
        if preview is not None:
            preview.rollback()
    return report


def _read_csv(csv_data: bytes) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """The column names (lowercase) and the rows with their spreadsheet row numbers, empty rows
    left out; VALIDATION_ERROR if the file is not UTF-8 or not valid CSV, or its header is not
    the expected columns."""
    try:
        text = csv_data.decode("utf-8-sig")  # Excel adds a BOM
    except UnicodeDecodeError as error:
        message = "The file must be saved as UTF-8 (in Excel: CSV UTF-8)"
        raise invalid_field("file", message) from error
    lines = _rows(text)
    first = next(lines, None)
    header = [column.lower() for column in first[1]] if first else []
    _check_header(header)  # before reading on: a wrong file is named as such
    return header, list(lines)


def _rows(text: str) -> Iterator[tuple[int, list[str]]]:
    """The rows of `text` that are not empty, numbered as a spreadsheet numbers them (a quoted
    cell may hold a line break, so these are not line numbers); VALIDATION_ERROR at the first
    row that is not valid CSV."""
    # strict: a quote left open is an error, not a cell that swallows the rows after it
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    number = 0  # the last row read whole
    try:
        for number, record in enumerate(reader, start=1):
            if cells := _cells(record):
                yield number, cells
    except csv.Error as error:
        # all three causes: csv's own message would mislead, as a quote left open usually fails
        # further on, at the next quote, as text following a closing quote
        problems = "a quote is not closed, text follows a closing quote, or a cell has more"
        message = f"{problems} than {csv.field_size_limit()} characters"
        raise invalid_field("file", f"Row {number + 1} is not valid CSV: {message}") from error


def _cells(record: list[str]) -> list[str]:
    """The cells of a line, stripped, without the empty cells at its end: spreadsheets pad every
    line to the widest one, and save an empty row as ",,,"."""
    cells = [cell.strip() for cell in record]
    while cells and not cells[-1]:
        cells.pop()
    return cells


def _check_header(header: list[str]) -> None:
    """VALIDATION_ERROR naming every missing, unknown or repeated column of the header."""
    problems = {
        "missing": REQUIRED_COLUMNS - set(header),
        "unknown": set(header) - set(COLUMNS),
        "repeated": {column for column, count in Counter(header).items() if count > 1},
    }
    found = [
        f"{kind} {', '.join(map(repr, sorted(columns)))}"
        for kind, columns in problems.items()
        if columns
    ]
    if found:
        optional = " and ".join(sorted(OPTIONAL_COLUMNS))
        expected = f"{', '.join(COLUMNS)} ({optional} may be left out)"
        message = f"The first row must name each column once: {expected}; {'; '.join(found)}"
        raise invalid_field("file", message)


def _import_row(
    row: int, header: list[str], cells: list[str], report: LocationImportReport, db: Session
) -> None:
    """Create the location of one row and add the row to the report, imported or rejected."""
    try:
        location = create_location(_location_row(header, cells), db)
    except ValidationError as error:
        reason = "; ".join(_problem(problem) for problem in error.errors())
        report.rejected.append(RejectedRow(row=row, code=ErrorCode.VALIDATION_ERROR, reason=reason))
    except AppError as error:  # the cell count, or a room number another location has
        report.rejected.append(RejectedRow(row=row, code=error.code, reason=error.message))
    else:
        report.imported.append(ImportedRow(row=row, room_number=location.room_number))


def _location_row(header: list[str], cells: list[str]) -> LocationRow:
    """The location a row describes; a blank cell, or a missing one at the end of a short row,
    counts as left out. ValidationError, or VALIDATION_ERROR if the row has more cells than
    the header (usually an entrances cell without quotes)."""
    if len(cells) > len(header):
        message = f"The row has {len(cells)} cells but the header only {len(header)}"
        raise AppError(ErrorCode.VALIDATION_ERROR, f"{message}; are the entrances in quotes?")
    filled = {column: cell for column, cell in zip(header, cells, strict=False) if cell}
    return LocationRow.model_validate(filled)


def _problem(problem: ErrorDetails) -> str:
    """One problem of a row as "column: message", e.g. "entrances.2.lat: ...". Unlike a 422's
    `field` (app.errors), which names the JSON body from 0, this names the CSV's columns and
    numbers entrances from 1, as rows are: the report is read by people looking at the file."""
    parts = (part + 1 if isinstance(part, int) else part for part in problem["loc"])
    return f"{'.'.join(map(str, parts))}: {problem['msg']}"
