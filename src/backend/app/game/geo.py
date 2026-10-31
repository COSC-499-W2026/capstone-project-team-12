"""Distances on the ground for Stage 1 navigation (spec, Stage 1: Navigation).

Pure functions with no database, so the rules can be tested on their own.
"""

import math
from collections.abc import Sequence
from enum import StrEnum

# mean Earth radius; plenty for distances of a few hundred metres on campus
EARTH_RADIUS_M = 6_371_000.0

# band limits from the spec (Stage 1, step 4)
NEAR_LIMIT_M = 100.0
APPROACHING_LIMIT_M = 300.0


class Band(StrEnum):
    """How far the student is from the target. Internal: only used to pick the spoken phrase."""

    FAR = "far"  # more than 300 m
    APPROACHING = "approaching"  # 100-300 m
    NEAR = "near"  # radius-100 m
    ARRIVED = "arrived"  # within the radius


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres between two points given in degrees."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))


def nearest_entrance(
    lat: float, lng: float, entrances: Sequence[tuple[float, float]]
) -> tuple[int, float]:
    """The index of the closest entrance and the distance to it. The first one wins a tie."""
    if not entrances:
        raise ValueError("a location needs at least one entrance")
    distances = [haversine_m(lat, lng, e_lat, e_lng) for e_lat, e_lng in entrances]
    index = min(range(len(distances)), key=distances.__getitem__)
    return index, distances[index]


def band(distance_m: float, radius_m: float) -> Band:
    """The proximity band for a distance. Exactly on a limit counts as the closer band."""
    if distance_m <= radius_m:
        return Band.ARRIVED
    if distance_m <= NEAR_LIMIT_M:
        return Band.NEAR
    if distance_m <= APPROACHING_LIMIT_M:
        return Band.APPROACHING
    return Band.FAR
