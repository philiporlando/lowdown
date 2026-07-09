"""Shared per-aircraft processing and the on-demand viewport live service.

Two callers share :func:`process_aircraft`:

* the :class:`~lowdown.collector.Collector`, which polls the fixed *earshot* box
  continuously to build the persistent violation record, and
* :class:`LiveService`, which answers viewport-bounded map queries on demand as
  the user pans/zooms.

Flagging (``is_low`` and event recording) is always gated to the earshot radius;
aircraft farther out are returned for display only. To protect the OpenSky
quota, viewport fetches are area-clamped and cached with a short TTL.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .config import Settings
from .elevation import ElevationProvider
from .faa import AircraftTypeProvider
from .geo import M_TO_FT, MS_TO_FPM, MS_TO_KT, haversine_m
from .nnumber import icao_to_n
from .opensky import AircraftState, OpenSkyClient
from .rules import Evaluation, evaluate

log = logging.getLogger(__name__)


@dataclass
class ProcessedAircraft:
    """One aircraft state evaluated relative to the watch center.

    ``ev`` is ``None`` for aircraft outside the earshot radius — those are shown
    on the map but never flagged and skip the terrain-elevation lookup.
    """

    st: AircraftState
    distance_m: float
    in_earshot: bool
    msl_ft: float
    ground_ft: float | None
    vertical_fpm: float | None
    n_number: str | None
    ev: Evaluation | None

    @property
    def is_low(self) -> bool:
        return bool(self.ev is not None and self.ev.is_low)

    def to_dict(self) -> dict:
        st, ev = self.st, self.ev
        return {
            "icao24": st.icao24,
            "n_number": self.n_number,
            "callsign": st.callsign,
            "lat": st.lat,
            "lon": st.lon,
            "distance_m": round(self.distance_m),
            "msl_ft": round(self.msl_ft),
            "agl_ft": None if ev is None or ev.agl_ft is None else round(ev.agl_ft),
            "heading": st.heading,
            "velocity_kt": (
                None if st.velocity_ms is None else round(st.velocity_ms * MS_TO_KT)
            ),
            "vertical_rate_fpm": (
                None if self.vertical_fpm is None else round(self.vertical_fpm)
            ),
            "in_earshot": self.in_earshot,
            "is_low": self.is_low,
            "near_airport": ev.near_airport if ev else None,
            "near_helipad": ev.near_helipad if ev else None,
            "likely_approach_departure": bool(ev and ev.likely_approach_departure),
            "is_rotorcraft": bool(ev.is_rotorcraft) if ev else st.is_rotorcraft,
            "aircraft_type": ev.aircraft_type if ev else None,
            "aircraft_model": ev.aircraft_model if ev else None,
            "is_exempt": bool(ev and ev.is_exempt),
            "exempt_reason": ev.exempt_reason if ev else None,
        }


async def process_aircraft(
    settings: Settings,
    elevation: ElevationProvider,
    aircraft_types: AircraftTypeProvider,
    st: AircraftState,
) -> ProcessedAircraft | None:
    """Evaluate one state relative to the watch center.

    Returns ``None`` for aircraft on the ground or without an altitude (nothing
    to plot or flag). Aircraft outside the earshot radius are returned but never
    flagged; the terrain and FAA-registry lookups run only inside earshot.
    """
    if st.on_ground or st.altitude_m is None:
        return None

    s = settings
    distance_m = haversine_m(s.apartment_lat, s.apartment_lon, st.lat, st.lon)
    in_earshot = distance_m <= s.earshot_radius_m
    msl_ft = st.altitude_m * M_TO_FT
    vertical_fpm = (
        None if st.vertical_rate_ms is None else st.vertical_rate_ms * MS_TO_FPM
    )
    n_number = icao_to_n(st.icao24)

    ground_ft: float | None = None
    ev: Evaluation | None = None
    if in_earshot:
        ground_m = await elevation.elevation_m(st.lat, st.lon)
        ground_ft = None if ground_m is None else ground_m * M_TO_FT
        category, model = aircraft_types.lookup(n_number)
        ev = evaluate(
            s,
            lat=st.lat,
            lon=st.lon,
            msl_ft=msl_ft,
            ground_elev_ft=ground_ft,
            vertical_rate_fpm=vertical_fpm,
            is_rotorcraft=st.is_rotorcraft,
            aircraft_category=category,
            aircraft_model=model,
        )

    return ProcessedAircraft(
        st=st,
        distance_m=distance_m,
        in_earshot=in_earshot,
        msl_ft=msl_ft,
        ground_ft=ground_ft,
        vertical_fpm=vertical_fpm,
        n_number=n_number,
        ev=ev,
    )


class LiveService:
    """On-demand OpenSky fetches bounded by the map's current viewport.

    Independent of the recording collector: it exists purely to render whatever
    the user is currently looking at. Results are cached per (rounded) bounding
    box for a short TTL so repeated polls and small pans don't each cost an
    OpenSky request, and oversized boxes are shrunk toward their center to keep
    a single request within the cheap quota tier.
    """

    def __init__(
        self,
        settings: Settings,
        opensky: OpenSkyClient,
        elevation: ElevationProvider,
        aircraft_types: AircraftTypeProvider | None = None,
    ) -> None:
        self._settings = settings
        self._opensky = opensky
        self._elevation = elevation
        self._aircraft_types = aircraft_types or AircraftTypeProvider(settings)
        self._cache: dict[
            tuple[float, float, float, float], tuple[float, list[dict]]
        ] = {}

    def _clamp(
        self, lamin: float, lomin: float, lamax: float, lomax: float
    ) -> tuple[float, float, float, float]:
        max_area = self._settings.view_max_area_deg2
        area = max(lamax - lamin, 0.0) * max(lomax - lomin, 0.0)
        if area <= max_area or area == 0.0:
            return lamin, lomin, lamax, lomax
        scale = (max_area / area) ** 0.5
        clat, clon = (lamin + lamax) / 2, (lomin + lomax) / 2
        hlat, hlon = (lamax - lamin) / 2 * scale, (lomax - lomin) / 2 * scale
        return clat - hlat, clon - hlon, clat + hlat, clon + hlon

    async def aircraft_in_box(
        self, lamin: float, lomin: float, lamax: float, lomax: float
    ) -> list[dict]:
        s = self._settings
        lamin, lomin, lamax, lomax = self._clamp(lamin, lomin, lamax, lomax)

        # Round the key so small pans reuse a recent fetch (~1 km granularity).
        key = (round(lamin, 2), round(lomin, 2), round(lamax, 2), round(lomax, 2))
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < s.view_cache_ttl_s:
            return hit[1]

        states = await self._opensky.states_in_box(lamin, lomin, lamax, lomax)
        result: list[dict] = []
        for st in states:
            pa = await process_aircraft(s, self._elevation, self._aircraft_types, st)
            if pa is not None:
                result.append(pa.to_dict())

        self._cache[key] = (now, result)
        self._prune(now)
        return result

    def _prune(self, now: float) -> None:
        ttl = self._settings.view_cache_ttl_s
        stale = [k for k, (ts, _) in self._cache.items() if now - ts >= ttl]
        for k in stale:
            del self._cache[k]
