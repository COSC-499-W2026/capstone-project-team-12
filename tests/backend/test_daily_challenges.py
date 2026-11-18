"""Tests for A13 daily challenge generation."""

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from app.engagement.daily_challenges import ensure_daily_challenges
from app.errors import AppError, ErrorCode
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
    assert [challenge.params for challenge in challenges] == [
        {"count": 1},
        {"count": 3},
        {"count": 1, "difficulty": 3},
    ]
    assert challenges[2].description_key == "complete_1_challenging_location"


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
        DailyChallenge(slot=1, params={}, progress_required=1),
        DailyChallenge(slot=2, params={"count": 3}, progress_required=3),
        DailyChallenge(
            slot=3,
            params={"difficulty": "hard"},
            progress_required=1,
            description_key="complete_1_hard_location",
        ),
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
    assert existing[0].params == {"count": 1}
    assert existing[2].params == {"count": 1, "difficulty": 3}
    assert existing[2].description_key == "complete_1_challenging_location"
    db.add_all.assert_not_called()
    db.flush.assert_called_once()


def test_incomplete_challenge_set_fills_missing_slot(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    now = datetime(2026, 11, 14, 20, tzinfo=UTC)
    first, second, third = ensure_daily_challenges(student.id, db, now=now)
    second.progress_current = 1
    db.delete(third)

    challenges = ensure_daily_challenges(student.id, db, now=now)

    assert [challenge.slot for challenge in challenges] == [1, 2, 3]
    assert challenges[:2] == (first, second)
    assert challenges[1].progress_current == 1
    assert challenges[2].id != third.id


def test_missing_student_raises_not_found(db: Session) -> None:
    with pytest.raises(AppError) as error:
        ensure_daily_challenges(uuid4(), db)
    assert error.value.code == ErrorCode.NOT_FOUND


def test_challenge_date_changes_at_vancouver_midnight() -> None:
    db = MagicMock()
    db.scalar.return_value = MagicMock()
    db.scalars.return_value = []
    student_id = uuid4()

    midnight = datetime(2026, 11, 15, tzinfo=ZoneInfo("America/Vancouver")).astimezone(UTC)
    before = ensure_daily_challenges(
        student_id,
        db,
        now=midnight - timedelta(minutes=1),
    )
    after = ensure_daily_challenges(
        student_id,
        db,
        now=midnight,
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

    midnight = datetime(2026, 11, 15, tzinfo=ZoneInfo("America/Vancouver")).astimezone(UTC)
    yesterday = ensure_daily_challenges(
        student.id,
        db,
        now=midnight - timedelta(minutes=1),
    )
    today = ensure_daily_challenges(
        student.id,
        db,
        now=midnight,
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
