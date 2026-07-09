"""Evaluate an aircraft state against the low-altitude rule.

This approximates 14 CFR 91.119(b) (over congested areas: 1,000 ft above the
highest obstacle within a 2,000 ft radius) using height above terrain (AGL).

It intentionally produces *apparent* low-altitude events and annotates the
common legal exception (takeoff/landing near an airport), rather than claiming
a definitive violation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .geo import haversine_m


# Registry categories treated as rotorcraft (exempt under 91.119(d)) and other
# categories that fly under different altitude rules.
_ROTOR_CATEGORIES = {"rotorcraft", "gyroplane"}
_OTHER_EXEMPT_CATEGORIES = {"glider", "balloon"}


@dataclass
class Evaluation:
    is_low: bool
    msl_ft: float
    agl_ft: float | None
    threshold_ft: float
    near_airport: str | None
    near_helipad: str | None
    likely_approach_departure: bool
    is_rotorcraft: bool
    aircraft_type: str | None
    aircraft_model: str | None
    is_exempt: bool
    exempt_reason: str | None
    notes: list[str] = field(default_factory=list)


def _nearest(
    lat: float, lon: float, points
) -> tuple[str | None, float]:
    """Return (code, distance_km) of the closest configured point."""
    best_code: str | None = None
    best_km = float("inf")
    for p in points:
        km = haversine_m(lat, lon, p.lat, p.lon) / 1000.0
        if km < best_km:
            best_km, best_code = km, p.code
    return best_code, best_km


def nearest_airport(
    settings: Settings, lat: float, lon: float
) -> tuple[str | None, float]:
    """Return (code, distance_km) of the closest configured airport."""
    return _nearest(lat, lon, settings.airports)


def nearest_helipad(
    settings: Settings, lat: float, lon: float
) -> tuple[str | None, float]:
    """Return (code, distance_km) of the closest configured helipad."""
    return _nearest(lat, lon, settings.helipads)


def evaluate(
    settings: Settings,
    *,
    lat: float,
    lon: float,
    msl_ft: float,
    ground_elev_ft: float | None,
    vertical_rate_fpm: float | None,
    is_rotorcraft: bool,
    aircraft_category: str | None = None,
    aircraft_model: str | None = None,
) -> Evaluation:
    """Decide whether a state is an apparent low-altitude event.

    ``is_low`` stays a pure altitude test, so genuinely low aircraft are always
    recorded. ``is_exempt`` layers on the legal exceptions — approach/departure
    near an airport *or helipad*, rotorcraft (by ADS-B category or FAA registry),
    and gliders/balloons — so those can be annotated and excluded from the count
    of unexplained events rather than hidden.
    """
    notes: list[str] = []
    threshold = settings.threshold_agl_ft + settings.obstacle_buffer_ft

    agl_ft = None if ground_elev_ft is None else msl_ft - ground_elev_ft
    if ground_elev_ft is None:
        notes.append("No terrain elevation available; AGL unknown.")

    is_low = agl_ft is not None and agl_ft < threshold

    airport_code, airport_km = nearest_airport(settings, lat, lon)
    near_airport = airport_code if airport_km <= settings.airport_proximity_km else None
    helipad_code, helipad_km = nearest_helipad(settings, lat, lon)
    near_helipad = helipad_code if helipad_km <= settings.helipad_proximity_km else None
    steep = (
        vertical_rate_fpm is not None
        and abs(vertical_rate_fpm) >= settings.vertical_rate_excepted_fpm
    )
    likely_approach_departure = (
        near_airport is not None or near_helipad is not None or steep
    )

    # FAA registry is authoritative; fall back to the ADS-B emitter category.
    category = (aircraft_category or "").lower() or None
    rotor = is_rotorcraft or category in _ROTOR_CATEGORIES
    aircraft_type = category or ("rotorcraft" if is_rotorcraft else None)

    reasons: list[str] = []
    if near_airport is not None:
        reasons.append(f"within {settings.airport_proximity_km:.0f} km of {near_airport} (likely takeoff/landing)")
    if near_helipad is not None:
        reasons.append("near a hospital helipad (likely medevac landing)")
    if steep:
        reasons.append(f"steep climb/descent ({vertical_rate_fpm:+.0f} fpm)")
    if rotor:
        reasons.append("rotorcraft (exempt under 91.119(d))")
    elif category in _OTHER_EXEMPT_CATEGORIES:
        reasons.append(f"{category} (flies under different altitude rules)")

    is_exempt = bool(reasons)
    exempt_reason = "; ".join(reasons) or None
    notes.extend(reasons)

    return Evaluation(
        is_low=is_low,
        msl_ft=msl_ft,
        agl_ft=agl_ft,
        threshold_ft=threshold,
        near_airport=near_airport,
        near_helipad=near_helipad,
        likely_approach_departure=likely_approach_departure,
        is_rotorcraft=rotor,
        aircraft_type=aircraft_type,
        aircraft_model=aircraft_model,
        is_exempt=is_exempt,
        exempt_reason=exempt_reason,
        notes=notes,
    )
