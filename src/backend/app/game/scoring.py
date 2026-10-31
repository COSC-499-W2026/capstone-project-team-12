"""Connect finalized game locations to engagement scoring.

The caller must lock the student, game, then location, validate ownership,
and set the location's final status before calling this function.
The caller owns the transaction.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engagement.interface import EngagementResult, apply_location_result
from app.models.enums import Language, LocationStatus
from app.models.game import GameSession, SessionLocation


def score_finalized_location(
    game: GameSession,
    location: SessionLocation,
    db: Session,
) -> EngagementResult:
    """Persist a finalized location's score and recalculate the game total."""
    if location.session_id != game.id:
        raise ValueError("Location does not belong to this game")

    if location.status not in (
        LocationStatus.COMPLETED,
        LocationStatus.FAILED,
        LocationStatus.SKIPPED,
    ):
        raise ValueError("Location must have a final status")

    completed = location.status == LocationStatus.COMPLETED

    result = apply_location_result(
        location.id,
        game.student_id,
        completed=completed,
        target_language=Language(game.target_language),
        class_id=game.class_id,
        db=db,
    )

    if result.location_points_awarded:
        location.points_awarded = result.location_points_awarded

    db.flush()

    game.total_score = (
        db.scalar(
            select(func.coalesce(func.sum(SessionLocation.points_awarded), 0)).where(
                SessionLocation.session_id == game.id
            )
        )
        or 0
    )

    db.flush()
    return result
