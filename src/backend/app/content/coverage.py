"""5.7 Report coverage (A20): which location, language and difficulty combinations are playable.

A combination is playable when new games can draw it: the clue is drawable (`DRAWABLE`: the
spec's eligible location, active with a room number and a primary recording) and every feedback
phrase exists in that language. The feedback phrase rule is the content interface's (a game plays
them all), stricter than the spec's definition of an eligible location, so the report lists each
language's missing phrases to show why nothing in it is playable. Clue text is reported but does
not affect play.

Read-only, two queries however many locations there are: locations with their clues and primary
recordings in one statement (so a location committed meanwhile cannot appear without its clues),
and the feedback phrases. A phrase import committed between the two only makes the report show
the newer phrase list; each language's missing phrases and playable flags come from the same read.
Never flushes or commits.
"""

from collections import Counter
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.content.schemas import (
    CombinationCoverage,
    CoverageReport,
    DifficultyCoverage,
    LanguageCoverage,
    LocationCoverage,
)
from app.models.content import AudioAsset, Clue, FeedbackAudio, Location
from app.models.enums import Difficulty, FeedbackCode, Language

# Join condition from clues to their primary recording (at most one per clue).
PRIMARY_RECORDING = and_(AudioAsset.clue_id == Clue.id, AudioAsset.is_primary)
# A clue new games can draw, over locations joined to clues and PRIMARY_RECORDING. The real
# eligible_location_ids is to reuse both, so the report and the draw cannot disagree.
DRAWABLE = and_(Location.is_active, Location.room_number.is_not(None), AudioAsset.id.is_not(None))


class _ClueState(NamedTuple):
    has_text: bool
    has_primary_audio: bool
    drawable: bool


class _Label(NamedTuple):
    name: str
    room_number: str | None
    is_active: bool


_NO_CLUE = _ClueState(has_text=False, has_primary_audio=False, drawable=False)
_MissingCodes = dict[Language, list[FeedbackCode]]
_Clues = dict[tuple[UUID, str, int], _ClueState]  # by (location id, language, difficulty)


def coverage_report(db: Session) -> CoverageReport:
    """Every location (inactive ones too, by room number) with each language and difficulty, and
    per language the missing feedback phrases and the playable locations at each difficulty."""
    missing = _missing_feedback_codes(db)
    locations = _locations(db, missing)
    playable = Counter(
        (combination.language, combination.difficulty)
        for location in locations
        for combination in location.combinations
        if combination.playable
    )
    languages = [
        LanguageCoverage(
            language=language,
            missing_feedback_codes=missing[language],
            difficulties=[
                DifficultyCoverage(
                    difficulty=difficulty, playable_locations=playable[language, difficulty]
                )
                for difficulty in Difficulty
            ],
        )
        for language in Language
    ]
    return CoverageReport(languages=languages, locations=locations)


def _missing_feedback_codes(db: Session) -> _MissingCodes:
    rows = db.execute(select(FeedbackAudio.language, FeedbackAudio.code))
    recorded = {(language, code) for language, code in rows}
    return {
        language: [code for code in FeedbackCode if (language, code) not in recorded]
        for language in Language
    }


def _locations(db: Session, missing: _MissingCodes) -> list[LocationCoverage]:
    # one row per clue, or one row with no clue for a location without any; only the columns
    # the report shows, so no answers or entrance points are loaded
    rows = db.execute(
        select(
            Location.id,
            Location.name,
            Location.room_number,
            Location.is_active,
            Clue.language,
            Clue.difficulty,
            Clue.text.is_not(None),
            AudioAsset.id.is_not(None),
            DRAWABLE,
        )
        .outerjoin(Clue, Clue.location_id == Location.id)
        .outerjoin(AudioAsset, PRIMARY_RECORDING)
        .order_by(Location.room_number, Location.name, Location.id)
    )
    labels: dict[UUID, _Label] = {}
    clues: _Clues = {}
    for location_id, name, room, active, language, difficulty, *state in rows:
        labels.setdefault(location_id, _Label(name, room, active))
        if language is not None:
            clues[location_id, language, difficulty] = _ClueState(*state)
    return [
        _location_coverage(location_id, label, clues, missing)
        for location_id, label in labels.items()
    ]


def _location_coverage(
    location_id: UUID,
    label: _Label,
    clues: _Clues,
    missing: _MissingCodes,
) -> LocationCoverage:
    combinations = []
    for language in Language:
        for difficulty in Difficulty:
            clue = clues.get((location_id, language, difficulty), _NO_CLUE)
            combinations.append(
                CombinationCoverage(
                    language=language,
                    difficulty=difficulty,
                    has_clue_text=clue.has_text,
                    has_primary_audio=clue.has_primary_audio,
                    playable=clue.drawable and not missing[language],
                )
            )
    return LocationCoverage(
        id=location_id,
        name=label.name,
        room_number=label.room_number,
        is_active=label.is_active,
        combinations=combinations,
    )
