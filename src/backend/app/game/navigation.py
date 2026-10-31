"""Stage 1 navigation (A19, spec Stage 1: Navigation; DFD 2.0 process 2.3).

`evaluate` turns one GPS ping into the feedback for the student and the tracking state to store.
It is pure (the time is passed in), so every rule can be tested without a database.

Guidance is only closer / farther / steady: no direction and never the target's position, so
the student still has to understand the clue.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from app.config import get_settings
from app.game.geo import Band, band, haversine_m, nearest_entrance
from app.models.enums import FeedbackCode


class Trend(StrEnum):
    CLOSER = "closer"
    FARTHER = "farther"
    STEADY = "steady"


@dataclass(frozen=True)
class NavigationRules:
    """The Stage 1 settings, read once so a test can pass its own."""

    trend_interval: timedelta
    trend_reset: timedelta
    dead_band_m: float

    @classmethod
    def from_settings(cls) -> "NavigationRules":
        settings = get_settings()
        return cls(
            trend_interval=timedelta(seconds=settings.gps_trend_interval_seconds),
            trend_reset=timedelta(seconds=settings.gps_trend_reset_seconds),
            dead_band_m=settings.gps_dead_band_meters,
        )


@dataclass(frozen=True)
class Ping:
    lat: float
    lng: float
    accuracy_m: float


@dataclass(frozen=True)
class Tracking:
    """What a session location remembers between pings (its columns of the same names)."""

    entrances: tuple[tuple[float, float], ...]
    radius_m: float
    last_lat: float | None = None
    last_lng: float | None = None
    last_accuracy_m: float | None = None
    last_distance_m: float | None = None
    last_ping_at: datetime | None = None
    # the entrance the trend compares with, so it doesn't jump when another one becomes nearest
    ref_point_index: int | None = None
    ref_distance_m: float | None = None
    ref_at: datetime | None = None
    # the last code that was spoken, and when
    last_feedback_code: FeedbackCode | None = None
    last_spoken_at: datetime | None = None


@dataclass(frozen=True)
class PingResult:
    tracking: Tracking  # the state to store
    distance_m: float  # to the nearest entrance
    can_confirm: bool
    trend: Trend | None  # None unless a trend was evaluated on this ping
    feedback: FeedbackCode
    speak: bool


def evaluate(tracking: Tracking, ping: Ping, now: datetime, rules: NavigationRules) -> PingResult:
    """Process one ping (spec, Stage 1 steps 2-8)."""
    index, distance = nearest_entrance(ping.lat, ping.lng, tracking.entrances)
    inside = distance <= tracking.radius_m
    # confirm-arrival reads the last position
    stored = replace(
        tracking,
        last_lat=ping.lat,
        last_lng=ping.lng,
        last_accuracy_m=ping.accuracy_m,
        last_distance_m=distance,
        last_ping_at=now,
    )

    if inside and tracking.last_feedback_code != FeedbackCode.NAV_ARRIVED:
        # spoken as soon as the student enters the radius; the reference starts again here
        stored = replace(stored, ref_point_index=index, ref_distance_m=distance, ref_at=now)
        return _result(stored, distance, inside, None, FeedbackCode.NAV_ARRIVED, True, now)

    if tracking.ref_at is None or tracking.ref_point_index is None:
        # the first good ping of this location: set the reference and say where to start
        stored = replace(stored, ref_point_index=index, ref_distance_m=distance, ref_at=now)
        return _result(stored, distance, inside, None, FeedbackCode.NAV_START, True, now)

    age = now - tracking.ref_at
    if age > rules.trend_reset:
        # pings stopped (e.g. the app was in the background): start the reference again
        stored = replace(stored, ref_point_index=index, ref_distance_m=distance, ref_at=now)
        return _silent(stored, distance, inside)
    if age < rules.trend_interval:
        return _silent(stored, distance, inside)

    # compare with the same entrance as the reference, then start a new reference
    ref_lat, ref_lng = tracking.entrances[tracking.ref_point_index]
    same_entrance = haversine_m(ping.lat, ping.lng, ref_lat, ref_lng)
    trend = _trend(same_entrance - (tracking.ref_distance_m or 0.0), rules)
    stored = replace(stored, ref_point_index=index, ref_distance_m=distance, ref_at=now)

    if (
        tracking.last_feedback_code == FeedbackCode.NAV_ARRIVED
        and distance <= tracking.radius_m + rules.dead_band_m
    ):
        # just inside or wobbling on the edge after NAV_ARRIVED: don't repeat or contradict it
        return PingResult(stored, distance, inside, None, FeedbackCode.NAV_ARRIVED, False)
    code = _trend_code(trend, band(distance, tracking.radius_m))
    return _result(stored, distance, inside, trend, code, True, now)


def _trend(change_m: float, rules: NavigationRules) -> Trend:
    # a change smaller than the dead band is GPS noise; exactly the dead band counts as a move
    if abs(change_m) < rules.dead_band_m:
        return Trend.STEADY
    return Trend.CLOSER if change_m < 0 else Trend.FARTHER


def _trend_code(trend: Trend, where: Band) -> FeedbackCode:
    if where in (Band.NEAR, Band.ARRIVED):
        if trend == Trend.FARTHER:
            return FeedbackCode.NAV_GETTING_FARTHER
        return FeedbackCode.NAV_VERY_CLOSE
    if trend == Trend.STEADY:
        return FeedbackCode.NAV_KEEP_GOING
    if trend == Trend.CLOSER:
        return FeedbackCode.NAV_GETTING_CLOSER
    return FeedbackCode.NAV_GETTING_FARTHER


def _silent(stored: Tracking, distance: float, inside: bool) -> PingResult:
    # no trend this ping: repeat the last spoken code without speaking it
    code = stored.last_feedback_code or FeedbackCode.NAV_KEEP_GOING
    return PingResult(stored, distance, inside, None, code, False)


def _result(
    stored: Tracking,
    distance: float,
    inside: bool,
    trend: Trend | None,
    code: FeedbackCode,
    speak: bool,
    now: datetime,
) -> PingResult:
    if speak:
        stored = replace(stored, last_feedback_code=code, last_spoken_at=now)
    return PingResult(stored, distance, inside, trend, code, speak)
