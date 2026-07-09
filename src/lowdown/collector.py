"""The polling collector: fetch states, flag low ones, group into events."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlmodel import select

from .config import Settings, get_settings
from .db import get_session
from .elevation import ElevationProvider
from .faa import AircraftTypeProvider
from .geo import MS_TO_KT, bounding_box
from .live import process_aircraft
from .models import LowAltitudeEvent, Observation
from .opensky import AircraftState, OpenSkyClient
from .state import runtime_state

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Collector:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def poll_once(
        self,
        opensky: OpenSkyClient,
        elevation: ElevationProvider,
        aircraft_types: AircraftTypeProvider,
    ) -> None:
        s = self.settings
        box = bounding_box(s.apartment_lat, s.apartment_lon, s.earshot_radius_m)
        states = await opensky.states_in_box(*box)
        now = _utcnow()
        snapshot: list[dict] = []

        for st in states:
            pa = await process_aircraft(s, elevation, aircraft_types, st)
            if pa is None:
                continue
            snapshot.append(pa.to_dict())

            if pa.in_earshot and pa.is_low:
                self._record(
                    st,
                    pa.ev,
                    pa.distance_m,
                    pa.ground_ft,
                    pa.vertical_fpm,
                    pa.n_number,
                    now,
                )

        self._close_stale_events(now)
        runtime_state.update(snapshot, now)
        log.info(
            "Poll: %d in radius, %d flagged low.",
            len(snapshot),
            sum(1 for a in snapshot if a["is_low"]),
        )

    def _record(
        self,
        st: AircraftState,
        ev,
        distance_m: float,
        ground_ft: float | None,
        vertical_fpm: float | None,
        n_number: str | None,
        now: datetime,
    ) -> None:
        gap = timedelta(seconds=self.settings.event_gap_s)
        with get_session() as session:
            event = session.exec(
                select(LowAltitudeEvent)
                .where(LowAltitudeEvent.icao24 == st.icao24)
                .where(LowAltitudeEvent.is_open == True)  # noqa: E712
                .order_by(LowAltitudeEvent.last_seen.desc())  # type: ignore[attr-defined]
            ).first()

            if event is not None and _aware(event.last_seen) < now - gap:
                event.is_open = False
                session.add(event)
                session.commit()
                event = None

            if event is None:
                event = LowAltitudeEvent(
                    icao24=st.icao24,
                    n_number=n_number,
                    callsign=st.callsign,
                    first_seen=now,
                    last_seen=now,
                    min_agl_ft=ev.agl_ft,
                    min_msl_ft=ev.msl_ft,
                    closest_distance_m=distance_m,
                    sample_count=0,
                    near_airport=ev.near_airport,
                    near_helipad=ev.near_helipad,
                    likely_approach_departure=ev.likely_approach_departure,
                    is_rotorcraft=ev.is_rotorcraft,
                    aircraft_type=ev.aircraft_type,
                    aircraft_model=ev.aircraft_model,
                    is_exempt=ev.is_exempt,
                    exempt_reason=ev.exempt_reason,
                )

            event.last_seen = now
            event.sample_count += 1
            if ev.agl_ft is not None and ev.agl_ft < event.min_agl_ft:
                event.min_agl_ft = ev.agl_ft
            event.min_msl_ft = min(event.min_msl_ft, ev.msl_ft)
            event.closest_distance_m = min(event.closest_distance_m, distance_m)
            if not event.callsign and st.callsign:
                event.callsign = st.callsign
            # Exemptions are sticky: once an event looks legal, keep it that way.
            event.likely_approach_departure = (
                event.likely_approach_departure or ev.likely_approach_departure
            )
            if ev.is_exempt and not event.is_exempt:
                event.is_exempt = True
                event.exempt_reason = ev.exempt_reason
            event.is_rotorcraft = event.is_rotorcraft or ev.is_rotorcraft
            if ev.near_airport and not event.near_airport:
                event.near_airport = ev.near_airport
            if ev.near_helipad and not event.near_helipad:
                event.near_helipad = ev.near_helipad
            if ev.aircraft_type and not event.aircraft_type:
                event.aircraft_type = ev.aircraft_type
            if ev.aircraft_model and not event.aircraft_model:
                event.aircraft_model = ev.aircraft_model
            session.add(event)
            session.commit()
            session.refresh(event)

            session.add(
                Observation(
                    event_id=event.id,
                    ts=now,
                    lat=st.lat,
                    lon=st.lon,
                    msl_ft=ev.msl_ft,
                    agl_ft=ev.agl_ft,
                    ground_elev_ft=ground_ft,
                    distance_m=distance_m,
                    velocity_kt=(
                        None if st.velocity_ms is None else st.velocity_ms * MS_TO_KT
                    ),
                    heading=st.heading,
                    vertical_rate_fpm=vertical_fpm,
                )
            )
            session.commit()

    def _close_stale_events(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.settings.event_gap_s)
        with get_session() as session:
            stale = session.exec(
                select(LowAltitudeEvent).where(
                    LowAltitudeEvent.is_open == True  # noqa: E712
                )
            ).all()
            for event in stale:
                if _aware(event.last_seen) < cutoff:
                    event.is_open = False
                    session.add(event)
            session.commit()

    async def run(self) -> None:
        """Run the polling loop forever."""
        s = self.settings
        async with httpx.AsyncClient(timeout=30.0) as client:
            opensky = OpenSkyClient(s, client)
            elevation = ElevationProvider(s, client)
            aircraft_types = AircraftTypeProvider(s)
            log.info(
                "Collector started: (%.4f, %.4f) r=%.0fm every %.0fs.",
                s.apartment_lat,
                s.apartment_lon,
                s.earshot_radius_m,
                s.poll_interval_s,
            )
            while True:
                try:
                    await self.poll_once(opensky, elevation, aircraft_types)
                except Exception as exc:
                    log.exception("Poll failed: %s", exc)
                    runtime_state.set_error(str(exc))
                await asyncio.sleep(s.poll_interval_s)


def _aware(dt: datetime) -> datetime:
    """SQLite may return naive datetimes; treat them as UTC for comparisons."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
