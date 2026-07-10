"""Geographic helpers and unit conversions."""

from __future__ import annotations

from math import asin, atan2, cos, degrees, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000.0

M_TO_FT = 3.280839895
FT_TO_M = 1.0 / M_TO_FT
MS_TO_KT = 1.943844
MS_TO_FPM = 196.850393701


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in meters."""
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, in degrees (0-360)."""
    p1, p2 = radians(lat1), radians(lat2)
    dlambda = radians(lon2 - lon1)
    y = sin(dlambda) * cos(p2)
    x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dlambda)
    return (degrees(atan2(y, x)) + 360.0) % 360.0


def angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two bearings, in degrees (0-180)."""
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def bounding_box(
    lat: float, lon: float, radius_m: float
) -> tuple[float, float, float, float]:
    """Return (lamin, lomin, lamax, lomax) covering a circle of ``radius_m``.

    Slightly over-covers the circle (a box around it); callers should still
    filter by :func:`haversine_m` for an exact radius.
    """
    dlat = degrees(radius_m / EARTH_RADIUS_M)
    # Guard against the poles where cos(lat) -> 0.
    dlon = degrees(radius_m / (EARTH_RADIUS_M * max(cos(radians(lat)), 1e-6)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)
