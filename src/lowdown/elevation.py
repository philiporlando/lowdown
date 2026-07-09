"""Terrain elevation lookups (for converting MSL altitude to AGL).

Uses the free Open-Meteo elevation API, caching results on a coarse lat/lon
grid both in memory and in the database so repeat queries are cheap. Falls
back to a configured fixed elevation when the API is unavailable or the
provider is set to ``fixed``.
"""

from __future__ import annotations

import logging

import httpx
from sqlmodel import select

from .config import Settings
from .db import get_session
from .models import ElevationCache

log = logging.getLogger(__name__)


class ElevationProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._memory: dict[str, float] = {}

    def _grid_key(self, lat: float, lon: float) -> tuple[str, float, float]:
        step = self._settings.elevation_grid_deg
        glat = round(round(lat / step) * step, 6)
        glon = round(round(lon / step) * step, 6)
        return f"{glat:.6f},{glon:.6f}", glat, glon

    async def elevation_m(self, lat: float, lon: float) -> float | None:
        """Return ground elevation (m MSL) at a point, or the fixed fallback."""
        if self._settings.elevation_provider == "fixed":
            return self._settings.default_ground_elevation_m

        key, glat, glon = self._grid_key(lat, lon)

        if key in self._memory:
            return self._memory[key]

        cached = self._load_cached(key)
        if cached is not None:
            self._memory[key] = cached
            return cached

        value = await self._fetch(glat, glon)
        if value is None:
            return self._settings.default_ground_elevation_m

        self._memory[key] = value
        self._store_cached(key, value)
        return value

    def _load_cached(self, key: str) -> float | None:
        with get_session() as session:
            row = session.get(ElevationCache, key)
            return row.elevation_m if row else None

    def _store_cached(self, key: str, value: float) -> None:
        with get_session() as session:
            if session.get(ElevationCache, key) is None:
                session.add(ElevationCache(key=key, elevation_m=value))
                session.commit()

    async def _fetch(self, lat: float, lon: float) -> float | None:
        try:
            resp = await self._client.get(
                self._settings.elevation_base_url,
                params={"latitude": lat, "longitude": lon},
            )
            resp.raise_for_status()
            elevations = resp.json().get("elevation") or []
            return float(elevations[0]) if elevations else None
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            log.warning("Elevation lookup failed for %s,%s: %s", lat, lon, exc)
            return None
