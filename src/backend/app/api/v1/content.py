"""Content management (A20): locations, clue text and clue audio, the coverage report and the bulk
imports, for staff only.

Room numbers and accepted answers are the game's answers, so only staff accounts may read them:
students, including students who are a class's TA, and admins get 403. Changes also need content
rights (`can_manage_content`).
"""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, UploadFile
from sqlalchemy.orm import Session

from app.accounts.interface import can_manage_content, get_current_user, require_staff
from app.content.audio_import import import_clue_audio, import_feedback_audio
from app.content.clue_audio import add_clue_recording
from app.content.coverage import coverage_report
from app.content.location_import import import_locations
from app.content.locations import (
    create_location,
    deactivate_location,
    list_locations,
    replace_location,
    set_clue_text,
)
from app.content.schemas import (
    AudioImportReport,
    ClueOut,
    ClueRecordingOut,
    ClueText,
    CoverageReport,
    LocationFields,
    LocationImportReport,
    LocationOut,
    LocationReplacement,
)
from app.db import get_db
from app.errors import error_responses
from app.models.accounts import User
from app.models.content import AudioAsset, Clue, Location
from app.models.enums import Difficulty, Language

content_router = APIRouter(prefix="/content", tags=["content"])

# reads are for any staff account; content rights can only be given to staff (a CHECK on users)
STAFF = [Depends(require_staff)]
CONTENT_MANAGER = [Depends(can_manage_content)]
ACCESS_ERRORS = (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)


@content_router.get(
    "/locations",
    operation_id="listLocations",
    summary="List locations",
    dependencies=STAFF,
    response_model=list[LocationOut],
    responses=error_responses(*ACCESS_ERRORS),
)
def get_locations(db: Annotated[Session, Depends(get_db)]) -> list[Location]:
    """Every location, deactivated ones too (so they can be brought back), by room number.

    Not paged: a campus has a few dozen locations. Any staff account may read them; 403
    FORBIDDEN_ROLE for students (TAs included, since room numbers are the answers) and admins.
    """
    return list_locations(db)


@content_router.post(
    "/locations",
    operation_id="createLocation",
    summary="Add a location",
    status_code=HTTPStatus.CREATED,
    dependencies=CONTENT_MANAGER,
    response_model=LocationOut,
    responses=error_responses(*ACCESS_ERRORS),
)
def add_location(fields: LocationFields, db: Annotated[Session, Depends(get_db)]) -> Location:
    """Add a location, active unless `is_active` is false.

    The room number may be typed as `sci333`, `SCI-333` or `SCI 333`; it is stored as printed on
    the sign (`SCI 333`). `accepted_answers` always starts with the room number's own answer
    (`SCI333`), without duplicates; send only extra answers, or leave it out. The radius defaults
    to the `GPS_RADIUS_METERS` setting. 422 VALIDATION_ERROR (on `room_number`) if another
    location, a deactivated one included, has the room number. Needs content rights: 403
    FORBIDDEN_ROLE otherwise.
    """
    location = create_location(fields, db)
    db.commit()
    return location


@content_router.put(
    "/locations/{location_id}",
    operation_id="replaceLocation",
    summary="Replace a location",
    dependencies=CONTENT_MANAGER,
    response_model=LocationOut,
    responses=error_responses(*ACCESS_ERRORS, HTTPStatus.NOT_FOUND),
)
def put_location(
    location_id: UUID, fields: LocationReplacement, db: Annotated[Session, Depends(get_db)]
) -> Location:
    """Replace every field of a location, checked as for `createLocation`. Every field must be
    sent (`floor_number` may be null, `accepted_answers` empty), so nothing is reset by being left
    out; `is_active` true brings a deactivated location back.

    Changes only affect new games: a game in progress keeps the copy it drew. 404 NOT_FOUND for
    an unknown id; 422 VALIDATION_ERROR for a missing field or if another location has the room
    number. Needs content rights: 403 FORBIDDEN_ROLE otherwise.
    """
    location = replace_location(location_id, fields, db)
    db.commit()
    return location


@content_router.delete(
    "/locations/{location_id}",
    operation_id="deleteLocation",
    summary="Deactivate a location",
    status_code=HTTPStatus.NO_CONTENT,
    dependencies=CONTENT_MANAGER,
    responses=error_responses(*ACCESS_ERRORS, HTTPStatus.NOT_FOUND),
)
def delete_location(location_id: UUID, db: Annotated[Session, Depends(get_db)]) -> None:
    """Soft delete: the location stays, with its clues and recordings, but is marked inactive so
    new games do not draw it. `replaceLocation` with `is_active` true brings it back. Deleting it
    again changes nothing. 404 NOT_FOUND for an unknown id. Needs content rights: 403
    FORBIDDEN_ROLE otherwise.
    """
    deactivate_location(location_id, db)
    db.commit()


@content_router.put(
    "/locations/{location_id}/clues/{difficulty}/{language}",
    operation_id="setClueText",
    summary="Set a clue's text",
    dependencies=CONTENT_MANAGER,
    response_model=ClueOut,
    responses=error_responses(*ACCESS_ERRORS, HTTPStatus.NOT_FOUND),
)
def put_clue_text(
    location_id: UUID,
    difficulty: Difficulty,
    language: Language,
    request: ClueText,
    db: Annotated[Session, Depends(get_db)],
) -> Clue:
    """Set the transcript of the location's clue for a difficulty (1-3) and language, creating
    the clue if it has none yet. `text` is required: send null or blank to clear it (stored as
    null). Students never see it: clues are only played as audio.

    404 NOT_FOUND for an unknown location (deactivated ones can be edited). Needs content rights:
    403 FORBIDDEN_ROLE otherwise.
    """
    clue = set_clue_text(location_id, difficulty, language, request.text, db)
    db.commit()
    return clue


@content_router.post(
    "/locations/{location_id}/clues/{difficulty}/{language}/audio",
    operation_id="uploadClueAudio",
    summary="Upload a clue recording",
    status_code=HTTPStatus.CREATED,
    dependencies=CONTENT_MANAGER,
    response_model=ClueRecordingOut,
    responses=error_responses(
        *ACCESS_ERRORS,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    ),
)
def upload_clue_audio(
    location_id: UUID,
    difficulty: Difficulty,
    language: Language,
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    make_primary: Annotated[bool, Form()] = False,
) -> AudioAsset:
    """Add a recording (multipart `file`, MP3 or M4A) to the location's clue for a difficulty
    (1-3) and language, creating the clue if it has none yet.

    The clue's first recording becomes primary (the one new games draw); a later one only when
    `make_primary` is true. Earlier recordings are kept: games in progress keep playing theirs.
    The file is re-encoded without its tags (an ID3 title could give the answer away) and stored
    under a random name.

    404 NOT_FOUND for an unknown location (deactivated ones can be edited); 413 FILE_TOO_LARGE
    over `MAX_MEDIA_FILE_MB`; 415 UNSUPPORTED_MEDIA_TYPE if it is not MP3 or M4A audio; 422
    AUDIO_TOO_LONG over `MAX_AUDIO_SECONDS`, or VALIDATION_ERROR on `file` without a file or for
    a recording under 5 ms. Needs content rights: 403 FORBIDDEN_ROLE otherwise.
    """
    recording = add_clue_recording(
        location_id, difficulty, language, file.file, user.id, db, make_primary=make_primary
    )
    db.commit()
    return recording


@content_router.get(
    "/coverage",
    operation_id="getContentCoverage",
    summary="Report which content is playable",
    dependencies=STAFF,
    response_model=CoverageReport,
    responses=error_responses(*ACCESS_ERRORS),
)
def get_coverage(db: Annotated[Session, Depends(get_db)]) -> CoverageReport:
    """Every location (deactivated ones too) with each language and difficulty: whether it has
    clue text, a primary recording, and is playable (new games can draw it: active, with a room
    number and a primary recording, in a language that has every feedback phrase). Per language,
    the missing feedback phrases and how many locations each difficulty can draw from.

    Any staff account may read it; 403 FORBIDDEN_ROLE for students (TAs included, since room
    numbers are the answers) and admins.
    """
    return coverage_report(db)


@content_router.post(
    "/import/locations",
    operation_id="importLocations",
    summary="Import locations from a CSV",
    dependencies=CONTENT_MANAGER,
    response_model=LocationImportReport,
    responses=error_responses(*ACCESS_ERRORS, HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
)
def post_location_import(
    file: UploadFile, db: Annotated[Session, Depends(get_db)], dry_run: bool = False
) -> LocationImportReport:
    """Add a location for each valid row of a CSV (multipart `file`, UTF-8) and report every row,
    imported or rejected with its reason. The valid rows are imported even when others are
    rejected. With `dry_run=true` nothing is saved, and the report is the one the import would give.

    The first row names the columns, in any order: `name`, `building_name`, `room_number` and
    `entrances`, and optionally `floor_number` and `gps_radius_meters` (blank means no floor, or
    the `GPS_RADIUS_METERS` setting). `entrances` holds every entrance in one quoted cell:
    `"49.9394,-119.3948;49.9401,-119.3952"`. Each row is checked as in `createLocation`; a room
    number another row or location has (a deactivated one included: reactivate it with
    `replaceLocation`) is rejected. Imports, dry runs included, run one at a time: a second one
    waits until the first has finished.

    413 FILE_TOO_LARGE over `MAX_IMPORT_FILE_KB`; 422 VALIDATION_ERROR on `file` without a file,
    or when the whole file is refused: not UTF-8, not valid CSV (e.g. a quote left open), a header
    without those columns, or more than `MAX_IMPORT_ROWS` rows. Needs content rights: 403
    FORBIDDEN_ROLE otherwise.
    """
    report = import_locations(file.file, db, dry_run)
    # a dry run's inserts were already undone; this ends the transaction, and the import's lock
    db.commit()
    return report


@content_router.post(
    "/import/clue-audio",
    operation_id="importClueAudio",
    summary="Import clue recordings from a zip",
    dependencies=CONTENT_MANAGER,
    response_model=AudioImportReport,
    responses=error_responses(*ACCESS_ERRORS, HTTPStatus.UNSUPPORTED_MEDIA_TYPE),
)
def post_clue_audio_import(
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AudioImportReport:
    """Add each valid recording in a zip (multipart `file`) as its clue's primary recording and
    report every file by its path in the zip, imported or rejected with its reason; each list
    keeps the zip's order. The valid files are imported even when others are rejected.

    Files are named `{ROOM}_{difficulty}_{language}.mp3` (or `.m4a`), e.g. `SCI333_2_fr.mp3`: the
    room number in capitals without a space. Only the file's own name counts, not its folders;
    folders and hidden files (`.DS_Store`, `__MACOSX/`) are skipped. A file is rejected, and
    nothing saved for it, if its name is not in this format, another file names the same clue, it
    cannot be read from the zip, no location has the room number (deactivated ones count), or it
    fails the checks of `uploadClueAudio` (its code then is FILE_TOO_LARGE, UNSUPPORTED_MEDIA_TYPE
    or AUDIO_TOO_LONG). Each file is re-encoded without its tags and stored under a random name.
    The old primary recording is kept, as games in progress keep playing it, so importing the
    same zip again adds a new primary recording to each clue.

    415 UNSUPPORTED_MEDIA_TYPE if the upload is not a zip; 422 VALIDATION_ERROR on `file` without
    a file or with more than `MAX_IMPORT_FILES` files (100 by default: one zip per language covers
    a campus). Needs content rights: 403 FORBIDDEN_ROLE otherwise.
    """
    report = import_clue_audio(file.file, user.id, db)
    db.commit()
    return report


@content_router.post(
    "/import/feedback-audio",
    operation_id="importFeedbackAudio",
    summary="Import feedback phrases from a zip",
    dependencies=CONTENT_MANAGER,
    response_model=AudioImportReport,
    responses=error_responses(*ACCESS_ERRORS, HTTPStatus.UNSUPPORTED_MEDIA_TYPE),
)
def post_feedback_audio_import(
    file: UploadFile, db: Annotated[Session, Depends(get_db)]
) -> AudioImportReport:
    """Make each valid recording in a zip (multipart `file`) the feedback phrase it is named for
    and report every file, imported or rejected with its reason. The valid files are imported
    even when others are rejected. A language is only playable once it has all 13 phrases.

    Files are named `{CODE}_{language}.mp3` (or `.m4a`), e.g. `NAV_START_fr.mp3`, CODE being a
    feedback code. A file replaces the language's phrase for its code (clearing its transcript)
    or adds it. Folders, hidden files, the report and the reasons a file is rejected are as in
    `importClueAudio`, with an unknown code rejected by the name format. Every phrase of every
    language (78) fits in one zip.

    415 UNSUPPORTED_MEDIA_TYPE if the upload is not a zip; 422 VALIDATION_ERROR on `file` without
    a file or with more than `MAX_IMPORT_FILES` files. Needs content rights: 403 FORBIDDEN_ROLE
    otherwise.
    """
    report = import_feedback_audio(file.file, db)
    db.commit()
    return report
