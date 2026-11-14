"""Tests for A13 daily challenge generation."""

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from app.engagement.daily_challenges import ensure_daily_challenges
from app.models.accounts import User
from app.models.engagement import DailyChallenge
from app.models.enums import UserRole


def test_generates_exactly_three_challenges() -> None:
    db = MagicMock()
    db.scalar.return_value = MagicMock()
    db.scalars.return_value = []
    student_id = uuid4()

    challenges = ensure_daily_challenges(
        student_id,
        db,
        now=datetime(2026, 11, 14, 20, tzinfo=UTC),
    )

    assert len(challenges) == 3
    assert [challenge.slot for challenge in challenges] == [1, 2, 3]
    assert all(isinstance(challenge, DailyChallenge) for challenge in challenges)
    assert all(challenge.user_id == student_id for challenge in challenges)
    assert all(challenge.challenge_date.isoformat() == "2026-11-14" for challenge in challenges)
    db.add_all.assert_called_once()
    assert db.flush.call_count == 2


def test_challenge_types_and_rewards() -> None:
    db = MagicMock()
    db.scalar.return_value = MagicMock()
    db.scalars.return_value = []

    challenges = ensure_daily_challenges(
        uuid4(),
        db,
        now=datetime(2026, 11, 14, 20, tzinfo=UTC),
    )

    assert [challenge.challenge_type for challenge in challenges] == [
        "completion",
        "completion",
        "difficulty",
    ]
    assert [challenge.reward_points for challenge in challenges] == [50, 100, 100]
    assert [challenge.progress_required for challenge in challenges] == [1, 3, 1]
    assert challenges[2].params == {"difficulty": "hard"}


def test_new_challenges_start_without_progress() -> None:
    db = MagicMock()
    db.scalar.return_value = MagicMock()
    db.scalars.return_value = []

    challenges = ensure_daily_challenges(
        uuid4(),
        db,
        now=datetime(2026, 11, 14, 20, tzinfo=UTC),
    )

    assert all(challenge.progress_current == 0 for challenge in challenges)
    assert all(challenge.completed_at is None for challenge in challenges)


def test_existing_challenges_are_reused() -> None:
    db = MagicMock()
    db.scalar.return_value = MagicMock()
    existing = [
        MagicMock(slot=1),
        MagicMock(slot=2),
        MagicMock(slot=3),
    ]
    existing[0].progress_current = 1
    db.scalars.return_value = existing

    challenges = ensure_daily_challenges(
        uuid4(),
        db,
        now=datetime(2026, 11, 14, 20, tzinfo=UTC),
    )

    assert challenges == tuple(existing)
    assert existing[0].progress_current == 1
    db.add_all.assert_not_called()
    db.flush.assert_called_once()


def test_incomplete_challenge_set_is_rejected() -> None:
    db = MagicMock()
    db.scalar.return_value = MagicMock()
    db.scalars.return_value = [MagicMock(slot=1), MagicMock(slot=2)]

    with pytest.raises(ValueError, match="Incomplete daily challenge set"):
        ensure_daily_challenges(
            uuid4(),
            db,
            now=datetime(2026, 11, 14, 20, tzinfo=UTC),
        )

    db.add_all.assert_not_called()


def test_duplicate_slots_are_rejected() -> None:
    db = MagicMock()
    db.scalar.return_value = MagicMock()
    db.scalars.return_value = [
        MagicMock(slot=1),
        MagicMock(slot=1),
        MagicMock(slot=3),
    ]

    with pytest.raises(ValueError, match="Incomplete daily challenge set"):
        ensure_daily_challenges(
            uuid4(),
            db,
            now=datetime(2026, 11, 14, 20, tzinfo=UTC),
        )


def test_challenge_date_changes_at_vancouver_midnight() -> None:
    db = MagicMock()
    db.scalar.return_value = MagicMock()
    db.scalars.return_value = []
    student_id = uuid4()

    before = ensure_daily_challenges(
        student_id,
        db,
        now=datetime(2026, 11, 15, 6, 59, tzinfo=UTC),
    )
    after = ensure_daily_challenges(
        student_id,
        db,
        now=datetime(2026, 11, 15, 7, 0, tzinfo=UTC),
    )

    assert before[0].challenge_date.isoformat() == "2026-11-14"
    assert after[0].challenge_date.isoformat() == "2026-11-15"


def test_postgres_persists_exactly_three_challenges(
    db: Session,
    make_user: Callable[..., object],
) -> None:
    student = make_user()
    now = datetime(2026, 11, 14, 20, tzinfo=UTC)

    generated = ensure_daily_challenges(student.id, db, now=now)

    saved = db.scalars(
        select(DailyChallenge)
        .where(DailyChallenge.user_id == student.id)
        .order_by(DailyChallenge.slot)
    ).all()

    assert len(generated) == 3
    assert len(saved) == 3
    assert [challenge.slot for challenge in saved] == [1, 2, 3]


def test_postgres_generation_is_idempotent(
    db: Session,
    make_user: Callable[..., object],
) -> None:
    student = make_user()
    now = datetime(2026, 11, 14, 20, tzinfo=UTC)

    first = ensure_daily_challenges(student.id, db, now=now)
    first[0].progress_current = 1
    db.flush()

    second = ensure_daily_challenges(student.id, db, now=now)

    assert [challenge.id for challenge in first] == [challenge.id for challenge in second]
    assert second[0].progress_current == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(DailyChallenge)
            .where(DailyChallenge.user_id == student.id)
        )
        == 3
    )


def test_postgres_midnight_creates_new_challenges(
    db: Session,
    make_user: Callable[..., object],
) -> None:
    student = make_user()

    yesterday = ensure_daily_challenges(
        student.id,
        db,
        now=datetime(2026, 11, 15, 6, 59, tzinfo=UTC),
    )
    today = ensure_daily_challenges(
        student.id,
        db,
        now=datetime(2026, 11, 15, 7, 0, tzinfo=UTC),
    )

    assert len(yesterday) == 3
    assert len(today) == 3
    assert {challenge.id for challenge in yesterday}.isdisjoint(
        {challenge.id for challenge in today}
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(DailyChallenge)
            .where(DailyChallenge.user_id == student.id)
        )
        == 6
    )


def test_postgres_challenges_are_student_specific(
    db: Session,
    make_user: Callable[..., object],
) -> None:
    first_student = make_user()
    second_student = make_user()
    now = datetime(2026, 11, 14, 20, tzinfo=UTC)

    first = ensure_daily_challenges(first_student.id, db, now=now)
    second = ensure_daily_challenges(second_student.id, db, now=now)

    assert len(first) == len(second) == 3
    assert {challenge.id for challenge in first}.isdisjoint({challenge.id for challenge in second})
    assert all(challenge.user_id == first_student.id for challenge in first)
    assert all(challenge.user_id == second_student.id for challenge in second)


@contextmanager
def _committed_student(engine: Engine) -> Any:
    """Create a committed student visible to concurrent sessions."""
    with Session(engine) as session:
        student = User(
            email=f"{uuid4().hex[:12]}@example.com",
            username=f"user_{uuid4().hex[:8]}",
            role=UserRole.STUDENT,
        )
        session.add(student)
        session.flush()
        student_id: UUID = student.id
        session.commit()

    try:
        yield student_id
    finally:
        with Session(engine) as session:
            session.execute(delete(DailyChallenge).where(DailyChallenge.user_id == student_id))
            session.execute(delete(User).where(User.id == student_id))
            session.commit()


def test_concurrent_generation_creates_only_three_challenges(
    test_engine: Engine,
    run_together: Callable[..., list[Any]],
) -> None:
    """Concurrent requests must reuse one challenge set."""
    now = datetime(2026, 11, 14, 20, tzinfo=UTC)

    with _committed_student(test_engine) as student_id:

        def generate(session: Session) -> tuple[UUID, ...]:
            challenges = ensure_daily_challenges(
                student_id,
                session,
                now=now,
            )
            return tuple(challenge.id for challenge in challenges)

        first, second = run_together(
            generate,
            (generate, 0.3),
            hold=0.3,
        )

        assert len(first) == len(second) == 3
        assert first == second

        with Session(test_engine) as session:
            count = session.scalar(
                select(func.count())
                .select_from(DailyChallenge)
                .where(DailyChallenge.user_id == student_id)
            )
            assert count == 3
