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
    weak_accuracy_m: float
    weak_pings_for_override: int
    override_after: timedelta
    speed_anomaly_mps: float

    @classmethod
    def from_settings(cls) -> "NavigationRules":
        settings = get_settings()
        return cls(
            trend_interval=timedelta(seconds=settings.gps_trend_interval_seconds),
            trend_reset=timedelta(seconds=settings.gps_trend_reset_seconds),
            dead_band_m=settings.gps_dead_band_meters,
            weak_accuracy_m=settings.gps_weak_accuracy_meters,
            weak_pings_for_override=settings.gps_weak_pings_for_override,
            override_after=timedelta(minutes=settings.gps_override_after_minutes),
            speed_anomaly_mps=settings.speed_anomaly_mps,
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
    weak_pings_in_row: int = 0


@dataclass(frozen=True)
class PingResult:
    tracking: Tracking  # the state to store
    distance_m: float  # to the nearest entrance
    can_confirm: bool
    trend: Trend | None  # None unless a trend was evaluated on this ping
    feedback: FeedbackCode
    speak: bool
    speed_anomaly: bool


def is_weak(accuracy_m: float, rules: NavigationRules) -> bool:
    """A fix whose accuracy is worse than the limit; exactly the limit is still good."""
    return accuracy_m > rules.weak_accuracy_m


def evaluate(tracking: Tracking, ping: Ping, now: datetime, rules: NavigationRules) -> PingResult:
    """Process one ping (spec, Stage 1 steps 2-8)."""
    index, distance = nearest_entrance(ping.lat, ping.lng, tracking.entrances)
    inside = distance <= tracking.radius_m
    # a weak fix is never confirmable, even inside the radius
    can_confirm = inside and not is_weak(ping.accuracy_m, rules)
    # position facts are stored for every ping, weak or not: confirm-arrival reads them
    stored = replace(
        tracking,
        last_lat=ping.lat,
        last_lng=ping.lng,
        last_accuracy_m=ping.accuracy_m,
        last_distance_m=distance,
        last_ping_at=now,
    )

    if is_weak(ping.accuracy_m, rules):
        # no trend and no speed check: weak fixes jump around
        stored = replace(stored, weak_pings_in_row=tracking.weak_pings_in_row + 1)
        if inside and tracking.last_feedback_code == FeedbackCode.NAV_ARRIVED:
            # still inside after NAV_ARRIVED: keep that phrase, and don't speak it again
            return PingResult(
                stored, distance, can_confirm, None, FeedbackCode.NAV_ARRIVED, False, False
            )
        speak = tracking.last_feedback_code != FeedbackCode.NAV_GPS_WEAK or _due(
            tracking.last_spoken_at, now, rules.trend_interval
        )
        return _result(stored, distance, can_confirm, None, FeedbackCode.NAV_GPS_WEAK, speak, now)

    stored = replace(stored, weak_pings_in_row=0)
    speed_anomaly = _speed_anomaly(tracking, ping, now, rules)

    if inside and tracking.last_feedback_code != FeedbackCode.NAV_ARRIVED:
        # spoken as soon as the student enters the radius; the reference starts again here
        stored = replace(stored, ref_point_index=index, ref_distance_m=distance, ref_at=now)
        return _result(
            stored, distance, can_confirm, None, FeedbackCode.NAV_ARRIVED, True, now, speed_anomaly
        )

    if tracking.ref_at is None or tracking.ref_point_index is None:
        # the first good ping of this location: set the reference and say where to start
        stored = replace(stored, ref_point_index=index, ref_distance_m=distance, ref_at=now)
        return _result(
            stored, distance, can_confirm, None, FeedbackCode.NAV_START, True, now, speed_anomaly
        )

    age = now - tracking.ref_at
    if age > rules.trend_reset:
        # pings stopped (e.g. the app was in the background): start the reference again
        stored = replace(stored, ref_point_index=index, ref_distance_m=distance, ref_at=now)
        return _between_trends(stored, tracking, distance, can_confirm, speed_anomaly)
    if age < rules.trend_interval:
        return _between_trends(stored, tracking, distance, can_confirm, speed_anomaly)

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
        return PingResult(
            stored, distance, can_confirm, None, FeedbackCode.NAV_ARRIVED, False, speed_anomaly
        )
    code = _trend_code(trend, band(distance, tracking.radius_m))
    return _result(stored, distance, can_confirm, trend, code, True, now, speed_anomaly)


def override_available_at(
    started_at: datetime,
    weak_pings_in_row: int,
    last_ping_at: datetime | None,
    rules: NavigationRules,
) -> datetime:
    """When the student may confirm arrival without GPS (spec, Arrival).

    After `override_after` in Stage 1, or once the last `weak_pings_for_override` pings were all
    weak. In that case it is the latest ping's time, which has already passed; it moves with
    every further weak ping, so it is only for comparing with the current time, not a countdown
    start. A good ping resets the count, and the rule falls back to the time in Stage 1.
    """
    if weak_pings_in_row >= rules.weak_pings_for_override and last_ping_at is not None:
        return min(last_ping_at, started_at + rules.override_after)
    return started_at + rules.override_after


def _due(last: datetime | None, now: datetime, interval: timedelta) -> bool:
    return last is None or now - last >= interval


def _speed_anomaly(tracking: Tracking, ping: Ping, now: datetime, rules: NavigationRules) -> bool:
    # only between two good fixes; a weak one can be far off and would flag honest students
    previous = (tracking.last_lat, tracking.last_lng, tracking.last_accuracy_m)
    if tracking.last_ping_at is None or None in previous:
        return False
    assert tracking.last_lat is not None and tracking.last_lng is not None
    assert tracking.last_accuracy_m is not None
    if is_weak(tracking.last_accuracy_m, rules):
        return False
    seconds = (now - tracking.last_ping_at).total_seconds()
    if seconds <= 0:
        return False
    moved = haversine_m(tracking.last_lat, tracking.last_lng, ping.lat, ping.lng)
    return moved / seconds > rules.speed_anomaly_mps


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


def _between_trends(
    stored: Tracking,
    previous: Tracking,
    distance: float,
    can_confirm: bool,
    speed_anomaly: bool,
) -> PingResult:
    # a good fix must not show the weak phrase, but stays quiet until the next trend; the stored
    # code stays NAV_GPS_WEAK so a flapping signal doesn't speak the warning on every ping
    if previous.last_feedback_code == FeedbackCode.NAV_GPS_WEAK:
        code = _trend_code(Trend.STEADY, band(distance, stored.radius_m))
        return PingResult(stored, distance, can_confirm, None, code, False, speed_anomaly)
    return _silent(stored, distance, can_confirm, speed_anomaly)


def _silent(
    stored: Tracking, distance: float, can_confirm: bool, speed_anomaly: bool
) -> PingResult:
    # no trend this ping: repeat the last spoken code without speaking it
    code = stored.last_feedback_code or FeedbackCode.NAV_KEEP_GOING
    return PingResult(stored, distance, can_confirm, None, code, False, speed_anomaly)


def _result(
    stored: Tracking,
    distance: float,
    can_confirm: bool,
    trend: Trend | None,
    code: FeedbackCode,
    speak: bool,
    now: datetime,
    speed_anomaly: bool = False,
) -> PingResult:
    if speak:
        stored = replace(stored, last_feedback_code=code, last_spoken_at=now)
    return PingResult(stored, distance, can_confirm, trend, code, speak, speed_anomaly)
