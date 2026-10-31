"""Stage 1 navigation rules (A19): distances, bands, trend and speech.

`evaluate` is pure, so these tests pass the time in and need no database.
"""

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.game.geo import EARTH_RADIUS_M, Band, band, haversine_m, nearest_entrance
from app.game.navigation import (
    NavigationRules,
    Ping,
    PingResult,
    Tracking,
    Trend,
    _trend,
    evaluate,
)
from app.models.enums import FeedbackCode

RULES = NavigationRules(
    trend_interval=timedelta(seconds=25),
    trend_reset=timedelta(seconds=60),
    dead_band_m=10,
)
T0 = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
TARGET = (49.9394, -119.3948)
RADIUS = 30.0


def north(metres: float, of: tuple[float, float] = TARGET) -> tuple[float, float]:
    """The point `metres` north of `of` (south when negative)."""
    return of[0] + math.degrees(metres / EARTH_RADIUS_M), of[1]


def ping_at(metres: float, accuracy: float = 10) -> Ping:
    lat, lng = north(metres)
    return Ping(lat, lng, accuracy)


def tracking(**fields: object) -> Tracking:
    values: dict[str, object] = {"entrances": (TARGET,), "radius_m": RADIUS}
    return Tracking(**(values | fields))  # type: ignore[arg-type]


def walk(start: Tracking, steps: list[tuple[float, float, float]]) -> list[PingResult]:
    """Feed pings (seconds after T0, metres north of the target, accuracy) one after another."""
    results, state = [], start
    for seconds, metres, accuracy in steps:
        result = evaluate(state, ping_at(metres, accuracy), T0 + timedelta(seconds=seconds), RULES)
        results.append(result)
        state = result.tracking
    return results


# geo


def test_haversine_matches_one_degree_of_latitude() -> None:
    assert haversine_m(0, 0, 1, 0) == pytest.approx(111_195, abs=1)
    assert haversine_m(*TARGET, *TARGET) == 0
    assert haversine_m(*TARGET, *north(250)) == pytest.approx(250, abs=0.01)


def test_nearest_entrance_and_ties() -> None:
    entrances = [north(120), north(-40), north(40)]
    assert nearest_entrance(*TARGET, entrances) == (1, pytest.approx(40, abs=0.01))
    # a tie goes to the first entrance in the list
    index, _ = nearest_entrance(*TARGET, [north(40), north(-40)])
    assert index == 0
    with pytest.raises(ValueError):
        nearest_entrance(*TARGET, [])


@pytest.mark.parametrize(
    ("distance", "expected"),
    [
        (0, Band.ARRIVED),
        (30, Band.ARRIVED),
        (30.01, Band.NEAR),
        (100, Band.NEAR),
        (100.01, Band.APPROACHING),
        (300, Band.APPROACHING),
        (300.01, Band.FAR),
    ],
)
def test_bands(distance: float, expected: Band) -> None:
    assert band(distance, RADIUS) == expected


# first pings


def test_the_first_good_ping_says_where_to_start() -> None:
    result = evaluate(tracking(), ping_at(250), T0, RULES)

    assert (result.feedback, result.speak, result.trend) == (FeedbackCode.NAV_START, True, None)
    assert result.distance_m == pytest.approx(250, abs=0.01)
    assert not result.can_confirm
    stored = result.tracking
    assert (stored.ref_point_index, stored.ref_at) == (0, T0)
    assert stored.ref_distance_m == pytest.approx(250, abs=0.01)
    assert (stored.last_ping_at, stored.last_feedback_code, stored.last_spoken_at) == (
        T0,
        FeedbackCode.NAV_START,
        T0,
    )


def test_a_first_ping_inside_the_radius_is_arrived_not_start() -> None:
    result = evaluate(tracking(), ping_at(29), T0, RULES)

    assert (result.feedback, result.speak, result.can_confirm) == (
        FeedbackCode.NAV_ARRIVED,
        True,
        True,
    )


# trend


@pytest.mark.parametrize(("seconds", "evaluated"), [(24, False), (25, True)])
def test_a_trend_needs_a_reference_25_seconds_old(seconds: float, evaluated: bool) -> None:
    _, second = walk(tracking(), [(0, 250, 10), (seconds, 200, 10)])

    if evaluated:
        assert (second.trend, second.feedback, second.speak) == (
            Trend.CLOSER,
            FeedbackCode.NAV_GETTING_CLOSER,
            True,
        )
        assert second.tracking.ref_at == T0 + timedelta(seconds=seconds)
    else:
        # not spoken: the last spoken code comes back, with no trend
        assert (second.trend, second.feedback, second.speak) == (
            None,
            FeedbackCode.NAV_START,
            False,
        )
        assert second.tracking.ref_at == T0


@pytest.mark.parametrize(("seconds", "reset"), [(60, False), (61, True)])
def test_a_reference_older_than_60_seconds_is_reset(seconds: float, reset: bool) -> None:
    _, second = walk(tracking(), [(0, 250, 10), (seconds, 200, 10)])

    if reset:
        assert (second.trend, second.speak) == (None, False)
        assert second.tracking.ref_at == T0 + timedelta(seconds=seconds)
        assert second.tracking.ref_distance_m == pytest.approx(200, abs=0.01)
    else:
        assert (second.trend, second.speak) == (Trend.CLOSER, True)


@pytest.mark.parametrize(
    ("moved", "trend", "code"),
    [
        (-9.99, Trend.STEADY, FeedbackCode.NAV_KEEP_GOING),
        (9.99, Trend.STEADY, FeedbackCode.NAV_KEEP_GOING),
        # exactly ±10 m of change counts as a move; a ground walk of 10 m is not exactly 10
        (-10.0, Trend.CLOSER, FeedbackCode.NAV_GETTING_CLOSER),
        (10.0, Trend.FARTHER, FeedbackCode.NAV_GETTING_FARTHER),
        (-10.01, Trend.CLOSER, FeedbackCode.NAV_GETTING_CLOSER),
        (10.01, Trend.FARTHER, FeedbackCode.NAV_GETTING_FARTHER),
    ],
)
def test_the_10_metre_dead_band(moved: float, trend: Trend, code: FeedbackCode) -> None:
    if abs(moved) == 10.0:
        assert _trend(moved, RULES) == trend
        return
    _, second = walk(tracking(), [(0, 250, 10), (25, 250 + moved, 10)])

    assert (second.trend, second.feedback, second.speak) == (trend, code, True)


@pytest.mark.parametrize(
    ("moved", "code"),
    [
        (-20, FeedbackCode.NAV_VERY_CLOSE),
        (0, FeedbackCode.NAV_VERY_CLOSE),
        (20, FeedbackCode.NAV_GETTING_FARTHER),
    ],
)
def test_near_the_target_closer_or_steady_is_very_close(moved: float, code: FeedbackCode) -> None:
    _, second = walk(tracking(), [(0, 70, 10), (25, 70 + moved, 10)])

    assert (second.feedback, second.speak) == (code, True)


def test_the_trend_keeps_comparing_with_the_reference_entrance() -> None:
    # entrance 0 is 200 m north, entrance 1 is 205 m south; the student starts at the target
    entrances = (north(200), north(-205))
    first = evaluate(tracking(entrances=entrances), ping_at(0), T0, RULES)
    assert first.tracking.ref_point_index == 0

    # 20 m south: entrance 1 is now nearest (185 m) but entrance 0 is 20 m farther
    second = evaluate(first.tracking, ping_at(-20), T0 + timedelta(seconds=25), RULES)

    assert (second.trend, second.feedback) == (Trend.FARTHER, FeedbackCode.NAV_GETTING_FARTHER)
    assert second.distance_m == pytest.approx(185, abs=0.01)
    # the next reference is the entrance that is nearest now
    assert second.tracking.ref_point_index == 1


# arrival


def test_arrived_is_spoken_once_on_entering_the_radius() -> None:
    results = walk(
        tracking(),
        [
            (0, 120, 10),
            (25, 80, 10),
            (30, 25, 10),  # enters the radius
            (35, 20, 10),
            (60, 15, 10),  # a trend is due, but inside the radius it is not spoken or reported
        ],
    )

    assert [(r.feedback, r.speak) for r in results] == [
        (FeedbackCode.NAV_START, True),
        (FeedbackCode.NAV_VERY_CLOSE, True),
        (FeedbackCode.NAV_ARRIVED, True),
        (FeedbackCode.NAV_ARRIVED, False),
        (FeedbackCode.NAV_ARRIVED, False),
    ]
    assert [r.can_confirm for r in results] == [False, False, True, True, True]
    assert results[4].trend is None


def test_wobbling_on_the_edge_does_not_repeat_arrived() -> None:
    results = walk(
        tracking(),
        [
            (0, 25, 10),  # arrived
            (25, 35, 10),  # 5 m outside: within the dead band of the edge
            (30, 28, 10),  # back inside
            (55, 48, 10),  # 18 m outside at a trend: the student walked away
            (60, 20, 10),  # entered again
        ],
    )

    assert [(r.feedback, r.speak) for r in results] == [
        (FeedbackCode.NAV_ARRIVED, True),
        (FeedbackCode.NAV_ARRIVED, False),
        (FeedbackCode.NAV_ARRIVED, False),
        (FeedbackCode.NAV_GETTING_FARTHER, True),
        (FeedbackCode.NAV_ARRIVED, True),
    ]
    assert [r.can_confirm for r in results] == [True, False, True, False, True]
    # the edge ping evaluated a trend and then dropped it
    assert results[1].trend is None
    assert results[3].trend == Trend.FARTHER


def test_a_recorded_route_at_walking_speed_gets_closer_then_arrives() -> None:
    # Proposal Test Case 1: walking straight at the target at 1.4 m/s, a ping every 5 s
    steps = [(float(t), 300 - 1.4 * t, 8.0) for t in range(0, 215, 5)]
    results = walk(tracking(), steps)

    spoken = [r.feedback for r in results if r.speak]
    assert spoken[0] == FeedbackCode.NAV_START
    assert spoken[-1] == FeedbackCode.NAV_ARRIVED
    trends = spoken[1:-1]
    assert FeedbackCode.NAV_GETTING_CLOSER in trends
    assert set(trends) <= {FeedbackCode.NAV_GETTING_CLOSER, FeedbackCode.NAV_VERY_CLOSE}
    # spoken about every 25 s: never on two pings in a row before arriving
    flags = [r.speak for r in results[:-1]]
    assert not any(a and b for a, b in zip(flags, flags[1:], strict=False))
    assert results[-1].can_confirm
    assert all(r.trend in (None, Trend.CLOSER) for r in results)
