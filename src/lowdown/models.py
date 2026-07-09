"""Database models (SQLModel)."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class ElevationCache(SQLModel, table=True):
    """Cached terrain elevation for a rounded lat/lon grid cell."""

    key: str = Field(primary_key=True)
    elevation_m: float


class LowAltitudeEvent(SQLModel, table=True):
    """A grouped run of consecutive low-altitude observations for one aircraft."""

    id: int | None = Field(default=None, primary_key=True)
    icao24: str = Field(index=True)
    n_number: str | None = Field(default=None, index=True)
    callsign: str | None = None

    first_seen: datetime
    last_seen: datetime = Field(index=True)

    min_agl_ft: float
    min_msl_ft: float
    closest_distance_m: float
    sample_count: int = 1

    # Annotations — an "apparent" low-altitude event may be perfectly legal.
    near_airport: str | None = None
    likely_approach_departure: bool = False
    is_rotorcraft: bool = False

    is_open: bool = Field(default=True, index=True)


class Observation(SQLModel, table=True):
    """A single sample belonging to a :class:`LowAltitudeEvent` (flight path)."""

    id: int | None = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="lowaltitudeevent.id", index=True)
    ts: datetime

    lat: float
    lon: float
    msl_ft: float
    agl_ft: float | None = None
    ground_elev_ft: float | None = None
    distance_m: float
    velocity_kt: float | None = None
    heading: float | None = None
    vertical_rate_fpm: float | None = None
