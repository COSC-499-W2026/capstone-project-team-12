"""Class leaderboard scoring queries for A14 (#88).

This module contains read-only scoring logic. HTTP authorization and
leaderboard-enabled checks belong to the API layer.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import ClassEnrollment, PointsLedger


@dataclass(frozen=True)
class LeaderboardEntry:
    """One student in a class leaderboard."""

    rank: int
    username: str
    total_points: int
    is_me: bool


def get_class_leaderboard(
    class_id: UUID,
    db: Session,
    *,
    viewer_id: UUID | None = None,
) -> list[LeaderboardEntry]:
    """Return active students ranked by points earned in this class.

    Practice points and points earned in other classes are excluded.
    Students with no class points receive zero points.

    Equal scores share a rank. Results are ordered by descending
    points, then username and student ID for deterministic ties.

    Only the viewer's entry is marked as their own. Entries expose usernames.
    The caller must validate class access and leaderboard settings.
    """
    totals = (
        select(
            PointsLedger.user_id.label("student_id"),
            func.sum(PointsLedger.points).label("total_points"),
        )
        .where(PointsLedger.class_id == class_id)
        .group_by(PointsLedger.user_id)
        .subquery()
    )

    points = func.coalesce(totals.c.total_points, 0)

    statement = (
        select(User.id, User.username, points)
        .join(
            ClassEnrollment,
            ClassEnrollment.student_id == User.id,
        )
        .outerjoin(
            totals,
            totals.c.student_id == User.id,
        )
        .where(
            ClassEnrollment.class_id == class_id,
            ClassEnrollment.is_active.is_(True),
        )
        .order_by(
            points.desc(),
            User.username.asc(),
            User.id.asc(),
        )
    )

    rows = db.execute(statement).all()

    entries: list[LeaderboardEntry] = []
    previous_points: int | None = None
    current_rank = 0

    for position, (student_id, username, total_points) in enumerate(rows, start=1):
        score = int(total_points)

        if score != previous_points:
            current_rank = position

        entries.append(
            LeaderboardEntry(
                rank=current_rank,
                username=username,
                total_points=score,
                is_me=student_id == viewer_id,
            )
        )
        previous_points = score

    return entries
