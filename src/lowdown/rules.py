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


@dataclass
class Evaluation:
    is_low: bool
    msl_ft: float
    agl_ft: float | None
    threshold_ft: float
    near_airport: str | None
    likely_approach_departure: bool
    is_rotorcraft: bool
    notes: list[str] = field(default_factory=list)


def nearest_airport(
    settings: Settings, lat: float, lon: float
) -> tuple[str | None, float]:
    """Return (code, distance_km) of the closest configured airport."""
    best_code: str | None = None
    best_km = float("inf")
    for ap in settings.airports:
        km = haversine_m(lat, lon, ap.lat, ap.lon) / 1000.0
        if km < best_km:
            best_km, best_code = km, ap.code
    return best_code, best_km


def evaluate(
    settings: Settings,
    *,
    lat: float,
    lon: float,
    msl_ft: float,
    ground_elev_ft: float | None,
    vertical_rate_fpm: float | None,
    is_rotorcraft: bool,
) -> Evaluation:
    """Decide whether a state is an apparent low-altitude event."""
    notes: list[str] = []
    threshold = settings.threshold_agl_ft + settings.obstacle_buffer_ft

    agl_ft = None if ground_elev_ft is None else msl_ft - ground_elev_ft
    if ground_elev_ft is None:
        notes.append("No terrain elevation available; AGL unknown.")

    is_low = agl_ft is not None and agl_ft < threshold

    code, dist_km = nearest_airport(settings, lat, lon)
    near_airport = code if dist_km <= settings.airport_proximity_km else None
    steep = (
        vertical_rate_fpm is not None
        and abs(vertical_rate_fpm) >= settings.vertical_rate_excepted_fpm
    )
    likely_approach_departure = near_airport is not None or steep

    if near_airport is not None:
        notes.append(
            f"Within {settings.airport_proximity_km:.0f} km of {near_airport}: "
            "likely takeoff/landing, which is exempt from 91.119 minimums."
        )
    if steep:
        notes.append(
            f"Steep climb/descent ({vertical_rate_fpm:+.0f} fpm): likely "
            "departure/approach."
        )
    if is_rotorcraft:
        notes.append(
            "Rotorcraft — helicopters are exempt from 91.119(b) under 91.119(d)."
        )

    return Evaluation(
        is_low=is_low,
        msl_ft=msl_ft,
        agl_ft=agl_ft,
        threshold_ft=threshold,
        near_airport=near_airport,
        likely_approach_departure=likely_approach_departure,
        is_rotorcraft=is_rotorcraft,
        notes=notes,
    )
