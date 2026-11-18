"""Tests for A13 daily challenge generation."""

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engagement import daily_challenges
from app.engagement.daily_challenges import ensure_daily_challenges
from app.engagement.streaks import app_calendar_date
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.engagement import DailyChallenge
from app.models.enums import UserRole


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


@pytest.mark.usefixtures("fixed_daily_challenge_targets")
def test_postgres_persists_exactly_three_challenges(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()
    now = datetime(2026, 11, 14, 20, tzinfo=UTC)

    generated = ensure_daily_challenges(student.id, db, now=now)

    db.expire_all()
    saved = db.scalars(
        select(DailyChallenge)
        .where(DailyChallenge.user_id == student.id)
        .order_by(DailyChallenge.slot)
    ).all()

    assert len(generated) == 3
    assert len(saved) == 3
    assert [challenge.slot for challenge in saved] == [1, 2, 3]
    assert all(challenge.challenge_date == app_calendar_date(now) for challenge in saved)
    assert [challenge.challenge_type for challenge in saved] == [
        "completion",
        "completion",
        "difficulty",
    ]
    assert [challenge.reward_points for challenge in saved] == [50, 100, 100]
    assert [challenge.progress_required for challenge in saved] == [1, 3, 1]
    assert [challenge.params for challenge in saved] == [
        {"count": 1},
        {"count": 3},
        {"count": 1, "difficulty": 3},
    ]
    assert saved[2].description_key == "complete_1_challenging_location"
    assert all(challenge.progress_current == 0 for challenge in saved)
    assert all(challenge.completed_at is None for challenge in saved)


def test_postgres_rewrites_legacy_challenge_params(
    db: Session, make_user: Callable[..., User]
) -> None:
    student_id = make_user().id
    now = datetime(2026, 11, 14, 20, tzinfo=UTC)
    legacy = DailyChallenge(
        user_id=student_id,
        challenge_date=app_calendar_date(now),
        slot=3,
        challenge_type="difficulty",
        params={"difficulty": "hard"},
        description_key="complete_1_hard_location",
        reward_points=100,
        progress_required=1,
        progress_current=1,
        completed_at=now,
    )
    completion = DailyChallenge(
        user_id=student_id,
        challenge_date=legacy.challenge_date,
        slot=1,
        challenge_type="completion",
        params={},
        description_key="complete_1_location",
        reward_points=50,
        progress_required=1,
    )
    db.add_all([legacy, completion])
    db.flush()
    legacy_id, completion_id = legacy.id, completion.id
    db.expire_all()

    ensure_daily_challenges(student_id, db, now=now)
    db.expire_all()

    saved = db.get(DailyChallenge, legacy_id)
    assert saved is not None
    assert saved.params == {"count": 1, "difficulty": 3}
    assert saved.description_key == "complete_1_challenging_location"
    assert saved.progress_current == 1
    assert saved.completed_at == now
    saved_completion = db.get(DailyChallenge, completion_id)
    assert saved_completion is not None
    assert saved_completion.params == {"count": 1}


def test_postgres_generation_is_idempotent(
    db: Session,
    make_user: Callable[..., User],
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
    make_user: Callable[..., User],
) -> None:
    student = make_user()

    zone = ZoneInfo(get_settings().app_timezone)
    midnight = datetime(2026, 11, 15, tzinfo=zone).astimezone(UTC)
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

    assert {item.challenge_date for item in yesterday} == {date(2026, 11, 14)}
    assert {item.challenge_date for item in today} == {date(2026, 11, 15)}
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
    make_user: Callable[..., User],
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


@pytest.mark.parametrize("difficulty_based", [False, True])
def test_draw_uses_type_weights_and_distinct_targets(
    db: Session,
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
    difficulty_based: bool,
) -> None:
    draws = []

    def draw(categories: tuple[bool, bool], *, weights: tuple[int, int]) -> list[bool]:
        assert categories == (False, True)
        assert weights == (60, 40)
        draws.append(difficulty_based)
        return [difficulty_based]

    monkeypatch.setattr(daily_challenges, "choices", draw)
    assigned = ensure_daily_challenges(make_user().id, db)

    assert len(draws) == 3
    assert len({(item.progress_required, item.params.get("difficulty")) for item in assigned}) == 3
    expected_type = "difficulty" if difficulty_based else "completion"
    assert all(item.challenge_type == expected_type for item in assigned)
    assert all(item.params["count"] == item.progress_required for item in assigned)
    assert all(item.reward_points in (50, 100) for item in assigned)


def test_new_local_day_has_at_least_one_new_target(
    db: Session, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Always aim for the first available completion target, including on day two.
    monkeypatch.setattr(daily_challenges, "choices", lambda *args, **kwargs: [False])
    monkeypatch.setattr(daily_challenges, "choice", lambda available: available[0])
    student = make_user()
    first_day = datetime(2026, 7, 1, 19, tzinfo=UTC)
    first = ensure_daily_challenges(student.id, db, now=first_day)
    second = ensure_daily_challenges(student.id, db, now=first_day + timedelta(days=1))

    assert [item.progress_required for item in first] == [1, 2, 3]
    assert [item.progress_required for item in second] == [1, 2, 5]
