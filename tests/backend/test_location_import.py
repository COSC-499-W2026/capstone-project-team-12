from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.location_import import import_locations
from app.content.schemas import ImportedRow, LocationOut
from app.errors import AppError, ErrorCode
from app.models.content import Location

HEADER = "name,building_name,floor_number,room_number,entrances,gps_radius_meters"


def csv_file(*rows: str, header: str = HEADER) -> bytes:
    return "\n".join([header, *rows]).encode()


def row(room_number: str) -> str:
    """A valid row with the default radius and no floor."""
    return f'Lab {room_number},Science Building,,{room_number},"49.9394,-119.3948",'


def saved(room_number: str, db: Session) -> Location | None:
    return db.scalars(select(Location).filter_by(room_number=room_number)).one_or_none()


def location_count(db: Session) -> int:
    return db.scalars(select(func.count()).select_from(Location)).one()


@pytest.fixture
def row_limit(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """MAX_IMPORT_ROWS lowered to 2 for this test."""
    monkeypatch.setenv("MAX_IMPORT_ROWS", "2")
    get_settings.cache_clear()
    yield 2
    get_settings.cache_clear()  # the next caller reads the restored environment


def test_import_saves_every_column(db: Session) -> None:
    entrances = '"49.9394,-119.3948; 49.9401 , -119.3952"'
    data = csv_file(f"Chem lab,Science Building,3,qzx-901,{entrances},45")

    report = import_locations(data, db)

    assert report.model_dump() == {
        "dry_run": False,
        "imported": [{"row": 2, "room_number": "QZX 901"}],
        "rejected": [],
    }
    db.expire_all()
    location = saved("QZX 901", db)
    assert location is not None
    assert LocationOut.model_validate(location).model_dump(exclude={"id"}) == {
        "name": "Chem lab",
        "building_name": "Science Building",
        "floor_number": 3,
        "room_number": "QZX 901",
        "accepted_answers": ["QZX901"],  # from the room number
        "gps_points": [{"lat": 49.9394, "lng": -119.3948}, {"lat": 49.9401, "lng": -119.3952}],
        "gps_radius_meters": 45,
        "is_active": True,
    }


@pytest.mark.parametrize(
    ("header", "cells"),
    [
        (HEADER, 'Lab,Science Building,,QZX 901,"49.9,-119.3",'),
        (HEADER, 'Lab,Science Building,,QZX 901,"49.9,-119.3"'),  # last cell left off
        ("name,building_name,room_number,entrances", 'Lab,Science Building,QZX 901,"49.9,-119.3"'),
        # any order and case, and the byte order mark Excel puts before the first column
        ("\ufeffEntrances , ROOM_NUMBER,Building_Name,Name", '"49.9,-119.3",QZX 901,Sci,Lab'),
        # a spreadsheet pads every line to its widest one
        (f"{HEADER},,", 'Lab,Science Building,,QZX 901,"49.9,-119.3",,,'),
    ],
)
def test_optional_columns_may_be_blank_or_left_out(header: str, cells: str, db: Session) -> None:
    report = import_locations(csv_file(cells, header=header), db)

    assert report.rejected == []
    location = saved("QZX 901", db)
    assert location is not None
    assert location.floor_number is None
    assert location.gps_radius_meters == get_settings().gps_radius_meters


@pytest.mark.parametrize("entrances", ["49.9,-119.3;", "49.9,-119.3;;49.8,-119.2", ";49.9,-119.3"])
def test_stray_semicolons_in_entrances_are_ignored(entrances: str, db: Session) -> None:
    report = import_locations(csv_file(f'Lab,Sci,,QZX 901,"{entrances}",'), db)

    assert report.rejected == []
    location = saved("QZX 901", db)
    assert location is not None
    assert location.gps_points[0] == {"lat": 49.9, "lng": -119.3}


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    ("bad_row", "reason"),
    [
        ('Lab,Sci,,QZX 90,"49.9,-119.3",', "room_number: Value error, expected 3 letters and"),
        ('Lab,Sci,,QZX 902,"49.9,-119.3;91,-119.3",', "entrances.2.lat: Input should be less"),
        ('Lab,Sci,,QZX 902,"nan,-119.3",', "entrances.1.lat: Input should be less than or equal"),
        ('Lab,Sci,,QZX 902,"49.9;-119.3",', "entrances: Value error, expected lat,lng pairs"),
        # without quotes and with the radius left out, the row still has 6 cells
        ("Lab,Sci,,QZX 902,49.9,-119.3", "entrances: Value error, expected lat,lng pairs"),
        ('Lab,Sci,,QZX 902,";",', "entrances: List should have at least 1 item"),
        ("Lab,Sci,,QZX 902", "entrances: Field required"),
        (' ,Sci,,QZX 902,"49.9,-119.3",', "name: Field required"),
        ('Lab,Sci,1st,QZX 902,"49.9,-119.3",', "floor_number: Input should be a valid integer"),
        ('Lab,Sci,,QZX 902,"49.9,-119.3",0', "gps_radius_meters: Input should be greater than 0"),
        (
            "Lab,Sci,,QZX 902,49.9,-119.3,30",
            "The row has 7 cells but the header only 6; are the entrances in quotes?",
        ),
    ],
)
def test_invalid_row_is_reported_and_the_others_imported(
    bad_row: str, reason: str, dry_run: bool, db: Session
) -> None:
    report = import_locations(csv_file(row("QZX 901"), bad_row, row("QZX 903")), db, dry_run)

    assert report.imported == [
        ImportedRow(row=2, room_number="QZX 901"),
        ImportedRow(row=4, room_number="QZX 903"),
    ]
    [rejected] = report.rejected
    assert (rejected.row, rejected.code) == (3, ErrorCode.VALIDATION_ERROR)
    assert rejected.reason.startswith(reason)
    assert saved("QZX 902", db) is None


@pytest.mark.parametrize("dry_run", [False, True])
def test_room_number_already_in_use_is_reported(
    dry_run: bool, db: Session, make_location: Callable[..., Location]
) -> None:
    make_location(room_number="QZX 902", accepted_answers=["QZX902"], is_active=False)
    data = csv_file(row("QZX 901"), row("qzx-901"), row("QZX 902"))

    report = import_locations(data, db, dry_run)

    assert report.imported == [ImportedRow(row=2, room_number="QZX 901")]
    assert [(rejected.row, rejected.code, rejected.reason) for rejected in report.rejected] == [
        (3, ErrorCode.VALIDATION_ERROR, "Another location already has room number QZX 901"),
        (4, ErrorCode.VALIDATION_ERROR, "Another location already has room number QZX 902"),
    ]


def test_dry_run_saves_nothing_and_keeps_the_callers_changes(
    db: Session, make_location: Callable[..., Location]
) -> None:
    earlier = make_location()  # not committed: only the dry run's own inserts are undone
    count = location_count(db)

    report = import_locations(csv_file(row("QZX 901"), row("QZX 902")), db, dry_run=True)

    assert report.dry_run
    assert [imported.room_number for imported in report.imported] == ["QZX 901", "QZX 902"]
    assert location_count(db) == count
    assert db.get(Location, earlier.id) is earlier


def test_empty_lines_are_skipped_but_counted(db: Session) -> None:
    data = csv_file("", ",,, ,,", row("QZX 901"), header=f",,\n{HEADER}")

    report = import_locations(data, db)

    assert report.imported == [ImportedRow(row=5, room_number="QZX 901")]
    assert report.rejected == []


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        pytest.param(
            b"", "missing 'building_name', 'entrances', 'name', 'room_number'", id="empty"
        ),
        pytest.param(
            b"name,building,room_number,entrances",
            "missing 'building_name'; unknown 'building'",
            id="misspelt column",
        ),
        pytest.param(f"name,,{HEADER[5:]}".encode(), "unknown ''", id="unnamed column"),
        pytest.param(f"{HEADER},name".encode(), "repeated 'name'", id="repeated column"),
        pytest.param(csv_file(row("QZX 901")).replace(b"Lab", b"Caf\xe9"), "UTF-8", id="not UTF-8"),
        pytest.param(csv_file(f'"{"x" * 200_000}"'), "UTF-8", id="cell over the csv limit"),
    ],
)
def test_unreadable_file_is_refused_whole(data: bytes, problem: str, db: Session) -> None:
    count = location_count(db)

    with pytest.raises(AppError) as raised:
        import_locations(data, db)

    assert raised.value.code == ErrorCode.VALIDATION_ERROR
    assert problem in raised.value.message
    assert [detail["field"] for detail in raised.value.details] == ["file"]
    assert location_count(db) == count


def test_file_over_the_row_limit_is_refused_whole(row_limit: int, db: Session) -> None:
    rows = [row(f"QZX {901 + number}") for number in range(row_limit)]
    assert len(import_locations(csv_file(*rows, ""), db, dry_run=True).imported) == row_limit

    with pytest.raises(AppError, match=f"at most {row_limit} can be imported") as raised:
        import_locations(csv_file(*rows, row("QZX 999")), db)

    assert raised.value.code == ErrorCode.VALIDATION_ERROR
    assert saved("QZX 901", db) is None


def test_import_leaves_the_commit_to_the_caller(db: Session) -> None:
    # not transaction_calls: it also counts releasing a savepoint as a commit
    import_locations(csv_file(row("QZX 901")), db)
    db.rollback()

    assert saved("QZX 901", db) is None
