"""Tests for A13 daily streak tracking."""

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engagement.streaks import app_calendar_date, record_active_day
from app.models.accounts import User
from app.models.engagement import UserStats
from app.services.locks import lock_user


def test_app_calendar_date_before_midnight() -> None:
    now = datetime(2026, 11, 15, 6, 59, tzinfo=UTC)
    assert app_calendar_date(now).isoformat() == "2026-11-14"


def test_app_calendar_date_after_midnight() -> None:
    now = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)
    assert app_calendar_date(now).isoformat() == "2026-11-15"


def test_calendar_date_uses_configured_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "app_timezone", "UTC")
    now = datetime(2026, 11, 15, 6, 59, tzinfo=UTC)
    assert app_calendar_date(now) == date(2026, 11, 15)


def test_app_calendar_date_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        app_calendar_date(datetime(2026, 11, 14))


@pytest.mark.parametrize(
    ("previous_day", "previous_streak", "completion_day", "current", "best", "last_active"),
    [
        (None, 0, 14, 1, 1, 14),
        (14, 2, 14, 2, 2, 14),
        (14, 1, 15, 2, 2, 15),
        (12, 5, 14, 1, 5, 14),
        (15, 3, 14, 3, 3, 15),
    ],
    ids=["first", "same-day", "consecutive", "missed-day", "out-of-order"],
)
def test_record_active_day_persists_streak(
    db: Session,
    make_user: Callable[..., User],
    previous_day: int | None,
    previous_streak: int,
    completion_day: int,
    current: int,
    best: int,
    last_active: int,
) -> None:
    student_id = make_user().id
    if previous_day is not None:
        db.add(
            UserStats(
                user_id=student_id,
                current_streak_days=previous_streak,
                best_streak_days=previous_streak,
                last_active_date=date(2026, 11, previous_day),
            )
        )

    assert lock_user(student_id, db) is not None
    now = datetime(2026, 11, completion_day, 20, tzinfo=UTC)
    assert record_active_day(student_id, db, now=now) == current

    db.expire_all()
    saved = db.get(UserStats, student_id)
    assert saved is not None
    assert saved.current_streak_days == current
    assert saved.best_streak_days == best
    assert saved.last_active_date == date(2026, 11, last_active)
