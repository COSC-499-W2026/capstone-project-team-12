"""Tests for A13 daily streak tracking."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.engagement.streaks import record_active_day, vancouver_date
from app.models.engagement import UserStats


def test_vancouver_date_before_midnight() -> None:
    now = datetime(2026, 11, 15, 6, 59, tzinfo=UTC)
    assert vancouver_date(now).isoformat() == "2026-11-14"


def test_vancouver_date_after_midnight() -> None:
    now = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)
    assert vancouver_date(now).isoformat() == "2026-11-15"


def test_vancouver_date_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        vancouver_date(datetime(2026, 11, 14))


def test_first_completion_starts_streak() -> None:
    db = MagicMock()
    db.get.return_value = None
    student_id = uuid4()

    streak = record_active_day(
        student_id,
        db,
        now=datetime(2026, 11, 14, 20, tzinfo=UTC),
    )

    assert streak == 1
    stats = db.add.call_args.args[0]
    assert isinstance(stats, UserStats)
    assert stats.current_streak_days == 1
    assert stats.best_streak_days == 1
    assert stats.last_active_date.isoformat() == "2026-11-14"
    db.flush.assert_called_once()


def test_same_day_completion_does_not_extend_streak() -> None:
    db = MagicMock()
    stats = UserStats(
        user_id=uuid4(),
        current_streak_days=2,
        best_streak_days=2,
        last_active_date=vancouver_date(datetime(2026, 11, 14, 20, tzinfo=UTC)),
    )
    db.get.return_value = stats

    streak = record_active_day(
        stats.user_id,
        db,
        now=datetime(2026, 11, 15, 6, tzinfo=UTC),
    )

    assert streak == 2
    assert stats.current_streak_days == 2
    db.flush.assert_not_called()


def test_consecutive_day_extends_streak() -> None:
    db = MagicMock()
    stats = UserStats(
        user_id=uuid4(),
        current_streak_days=1,
        best_streak_days=1,
        last_active_date=vancouver_date(datetime(2026, 11, 14, 20, tzinfo=UTC)),
    )
    db.get.return_value = stats

    streak = record_active_day(
        stats.user_id,
        db,
        now=datetime(2026, 11, 15, 20, tzinfo=UTC),
    )

    assert streak == 2
    assert stats.best_streak_days == 2
    db.flush.assert_called_once()


def test_missed_day_resets_streak() -> None:
    db = MagicMock()
    stats = UserStats(
        user_id=uuid4(),
        current_streak_days=5,
        best_streak_days=5,
        last_active_date=vancouver_date(datetime(2026, 11, 12, 20, tzinfo=UTC)),
    )
    db.get.return_value = stats

    streak = record_active_day(
        stats.user_id,
        db,
        now=datetime(2026, 11, 14, 20, tzinfo=UTC),
    )

    assert streak == 1
    assert stats.best_streak_days == 5
    db.flush.assert_called_once()


def test_older_event_does_not_rewind_streak() -> None:
    db = MagicMock()
    stats = UserStats(
        user_id=uuid4(),
        current_streak_days=3,
        best_streak_days=3,
        last_active_date=vancouver_date(datetime(2026, 11, 15, 20, tzinfo=UTC)),
    )
    db.get.return_value = stats

    streak = record_active_day(
        stats.user_id,
        db,
        now=datetime(2026, 11, 14, 20, tzinfo=UTC),
    )

    assert streak == 3
    assert stats.last_active_date.isoformat() == "2026-11-15"
    db.flush.assert_not_called()
