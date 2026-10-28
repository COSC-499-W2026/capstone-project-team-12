"""Choosing the locations of a new game (A11, spec Starting a Session).

Carry-over comes first: if the student's last game in the same mode (the same class, or
Practice), language and difficulty was quit today, its unplayed locations are drawn again, so
quitting cannot be used to reroll. The other places are filled at random, preferring locations
the student has not completed yet in that language and difficulty. Content decides which
locations are eligible; this module only picks among them.
"""

import random
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
    completed = _completed(student_id, language, difficulty, db)
    fresh = list(rest - completed)
    played = list(rest & completed)
    random.shuffle(fresh)
    random.shuffle(played)

    drawn: list[content.LocationSnapshot] = []
    for location_id in carried + fresh + played:
        try:
            drawn.append(content.location_snapshot(location_id, language, difficulty, db))
        except content.LocationNotEligibleError:
            # e.g. deactivated since the eligible list was read
            continue
        if len(drawn) == count:
            return drawn
    raise _not_enough_content()


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
