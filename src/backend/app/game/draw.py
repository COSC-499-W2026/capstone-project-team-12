"""Choosing the locations of a new game (A11, spec Starting a Session), and the replacements
for a shuffle (A9).

Carry-over comes first: if the student's last game in the same mode (the same class, or
Practice), language and difficulty was quit today, its unplayed locations are drawn again, so
quitting cannot be used to reroll. The other places are filled at random, preferring locations
the student has not completed yet in that language and difficulty. Content decides which
locations are eligible; this module only picks among them.
"""

import random
from collections.abc import Collection, Sequence
from collections.abc import Set as AbstractSet
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content import interface as content
from app.errors import AppError, ErrorCode
from app.game.sessions import start_of_today
from app.models.enums import Difficulty, Language, LocationStatus, SessionEndReason, SkipReason
from app.models.game import OPEN_SESSION_STATES, GameSession, SessionLocation


def draw_locations(
    student_id: UUID,
    class_id: UUID | None,
    language: Language,
    difficulty: Difficulty,
    db: Session,
    *,
    carry_over: bool = True,
) -> list[content.LocationSnapshot]:
    """Snapshots of the locations for a new game, in play order.

    `carry_over=False` skips the carry-over: a reset passes it, since a reset must reroll and
    the last quit game could otherwise bring its locations back.

    Raises 409 NOT_ENOUGH_CONTENT if fewer than `locations_per_game` locations can be drawn.
    Only reads.
    """
    count = get_settings().locations_per_game
    eligible = content.eligible_location_ids(language, difficulty, db)
    if len(eligible) < count:
        raise _not_enough_content()

    unplayed = _carried_over(student_id, class_id, language, difficulty, db) if carry_over else []
    carried = [location_id for location_id in unplayed if location_id in eligible]
    rest = eligible.difference(carried)
    return _pick(count, carried, rest, student_id, language, difficulty, db)


def draw_replacements(
    student_id: UUID,
    language: Language,
    difficulty: Difficulty,
    exclude: Collection[UUID],
    count: int,
    db: Session,
) -> list[content.LocationSnapshot]:
    """Snapshots of `count` eligible locations that aren't in `exclude`, for a shuffle.

    `exclude` is every location already in the game, including rows an earlier shuffle
    replaced, so a shuffle never brings one back. Uncompleted locations come first, as in a new
    game; there is no carry-over. Raises 409 NOT_ENOUGH_CONTENT. Only reads.
    """
    eligible = content.eligible_location_ids(language, difficulty, db)
    return _pick(count, [], eligible.difference(exclude), student_id, language, difficulty, db)


def _pick(
    count: int,
    first: Sequence[UUID],
    rest: AbstractSet[UUID],
    student_id: UUID,
    language: Language,
    difficulty: Difficulty,
    db: Session,
) -> list[content.LocationSnapshot]:
    """`count` snapshots: `first` in order, then `rest` at random, the locations the student
    hasn't completed before the ones they have. Raises NOT_ENOUGH_CONTENT."""
    completed = _completed(student_id, language, difficulty, db)
    fresh = list(rest - completed)
    played = list(rest & completed)
    random.shuffle(fresh)
    random.shuffle(played)

    drawn: list[content.LocationSnapshot] = []
    for location_id in [*first, *fresh, *played]:
        if len(drawn) == count:
            break
        try:
            drawn.append(content.location_snapshot(location_id, language, difficulty, db))
        except content.LocationNotEligibleError:
            # e.g. deactivated since the eligible list was read
            continue
    if len(drawn) < count:
        raise _not_enough_content()
    return drawn


def _not_enough_content() -> AppError:
    return AppError(
        ErrorCode.NOT_ENOUGH_CONTENT, "Not enough locations for this language and difficulty yet"
    )


def _carried_over(
    student_id: UUID,
    class_id: UUID | None,
    language: Language,
    difficulty: Difficulty,
    db: Session,
) -> list[UUID]:
    """Unplayed locations of the last game in this mode, if it was quit today, by position."""
    # Quit is a game action, so last_activity_at is when the game was quit.
    last = db.scalar(
        select(GameSession)
        .where(
            GameSession.student_id == student_id,
            GameSession.class_id.is_not_distinct_from(class_id),
            GameSession.target_language == language,
            GameSession.difficulty == difficulty,
            GameSession.state.not_in(OPEN_SESSION_STATES),
        )
        .order_by(GameSession.last_activity_at.desc())
        .limit(1)
    )
    if last is None or last.end_reason != SessionEndReason.QUIT:
        return []
    if last.last_activity_at < start_of_today():
        return []
    return list(
        db.scalars(
            select(SessionLocation.location_id)
            .where(
                SessionLocation.session_id == last.id,
                SessionLocation.skip_reason == SkipReason.SESSION_ENDED,
            )
            .order_by(SessionLocation.position)
        )
    )


def _completed(
    student_id: UUID, language: Language, difficulty: Difficulty, db: Session
) -> set[UUID]:
    """Locations the student has completed in this language and difficulty, in any mode."""
    return set(
        db.scalars(
            select(SessionLocation.location_id)
            .distinct()
            .join(GameSession, GameSession.id == SessionLocation.session_id)
            .where(
                GameSession.student_id == student_id,
                GameSession.target_language == language,
                GameSession.difficulty == difficulty,
                SessionLocation.status == LocationStatus.COMPLETED,
            )
        )
    )
