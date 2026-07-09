"""FastAPI application: dashboard + JSON API. Optionally runs the collector."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from sqlmodel import select

from .collector import Collector
from .config import get_settings
from .db import get_session, init_db
from .elevation import ElevationProvider
from .faa import AircraftTypeProvider
from .live import LiveService
from .models import LowAltitudeEvent, Observation
from .opensky import OpenSkyClient
from .state import runtime_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()

    # A long-lived client for on-demand viewport map queries (separate from the
    # collector's own client, which owns the continuous earshot record).
    client = httpx.AsyncClient(timeout=30.0)
    app.state.live_service = LiveService(
        settings,
        OpenSkyClient(settings, client),
        ElevationProvider(settings, client),
        AircraftTypeProvider(settings),
    )

    task: asyncio.Task | None = None
    if settings.run_collector:
        task = asyncio.create_task(Collector(settings).run())
        log.info("In-process collector enabled.")
    else:
        log.info("Collector disabled (LD_RUN_COLLECTOR=false).")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await client.aclose()


app = FastAPI(title="lowdown", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    _, last_poll, last_error = runtime_state.read()
    return {
        "status": "ok",
        "last_poll": last_poll.isoformat() if last_poll else None,
        "last_error": last_error,
    }


@app.get("/api/config")
def config() -> dict:
    s = get_settings()
    return {
        "apartment_lat": s.apartment_lat,
        "apartment_lon": s.apartment_lon,
        "earshot_radius_m": s.earshot_radius_m,
        "threshold_agl_ft": s.threshold_agl_ft,
        "obstacle_buffer_ft": s.obstacle_buffer_ft,
        "airports": [ap.model_dump() for ap in s.airports],
    }


@app.get("/api/live")
async def live(
    request: Request,
    lamin: float | None = Query(None, ge=-90, le=90),
    lomin: float | None = Query(None, ge=-180, le=180),
    lamax: float | None = Query(None, ge=-90, le=90),
    lomax: float | None = Query(None, ge=-180, le=180),
) -> dict:
    """Aircraft to plot on the map.

    With a valid viewport bounding box, fetch that box from OpenSky on demand so
    the map shows everything in view; ``in_earshot`` marks which are close enough
    to be flagged. Without bounds (or on fetch failure) fall back to the
    collector's most recent earshot snapshot.
    """
    snapshot, last_poll, last_error = runtime_state.read()

    have_bounds = (
        None not in (lamin, lomin, lamax, lomax)
        and lamin < lamax
        and lomin < lomax
    )
    if have_bounds:
        try:
            aircraft = await request.app.state.live_service.aircraft_in_box(
                lamin, lomin, lamax, lomax
            )
            return {
                "last_poll": datetime.now(timezone.utc).isoformat(),
                "last_error": None,
                "aircraft": aircraft,
            }
        except Exception as exc:  # noqa: BLE001 — degrade to the earshot snapshot
            log.exception("Viewport live fetch failed: %s", exc)
            last_error = str(exc)

    return {
        "last_poll": last_poll.isoformat() if last_poll else None,
        "last_error": last_error,
        "aircraft": snapshot,
    }


@app.get("/api/events")
def events(
    status: str = Query("all", pattern="^(all|open|closed)$"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict]:
    with get_session() as session:
        stmt = select(LowAltitudeEvent).order_by(LowAltitudeEvent.last_seen.desc())
        if status == "open":
            stmt = stmt.where(LowAltitudeEvent.is_open == True)  # noqa: E712
        elif status == "closed":
            stmt = stmt.where(LowAltitudeEvent.is_open == False)  # noqa: E712
        rows = session.exec(stmt.limit(limit)).all()
        return [_event_dict(e) for e in rows]


@app.get("/api/events/{event_id}")
def event_detail(event_id: int) -> dict:
    with get_session() as session:
        event = session.get(LowAltitudeEvent, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        obs = session.exec(
            select(Observation)
            .where(Observation.event_id == event_id)
            .order_by(Observation.ts)
        ).all()
        data = _event_dict(event)
        data["observations"] = [
            {
                "ts": _iso_utc(o.ts),
                "lat": o.lat,
                "lon": o.lon,
                "msl_ft": round(o.msl_ft),
                "agl_ft": None if o.agl_ft is None else round(o.agl_ft),
                "distance_m": round(o.distance_m),
                "velocity_kt": None if o.velocity_kt is None else round(o.velocity_kt),
                "heading": o.heading,
                "vertical_rate_fpm": (
                    None if o.vertical_rate_fpm is None else round(o.vertical_rate_fpm)
                ),
            }
            for o in obs
        ]
        return data


@app.get("/api/tail/{n_number}")
def tail_history(n_number: str) -> list[dict]:
    with get_session() as session:
        rows = session.exec(
            select(LowAltitudeEvent)
            .where(LowAltitudeEvent.n_number == n_number.upper())
            .order_by(LowAltitudeEvent.last_seen.desc())
        ).all()
        return [_event_dict(e) for e in rows]


@app.get("/api/stats")
def stats() -> dict:
    with get_session() as session:
        events_all = session.exec(select(LowAltitudeEvent)).all()
    total = len(events_all)
    exempt = sum(1 for e in events_all if e.is_exempt)
    likely = sum(1 for e in events_all if e.likely_approach_departure)
    return {
        "total_events": total,
        "exempt": exempt,
        "likely_approach_departure": likely,
        "unexplained": total - exempt,
        "distinct_aircraft": len({e.icao24 for e in events_all}),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    s = get_settings()
    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "apartment_lat": s.apartment_lat,
            "apartment_lon": s.apartment_lon,
            "earshot_radius_m": s.earshot_radius_m,
            "threshold_ft": s.threshold_agl_ft + s.obstacle_buffer_ft,
        },
    )


def _iso_utc(dt: datetime) -> str:
    """Serialize as UTC-aware ISO so browsers render it in local time.

    SQLite returns naive datetimes; without an explicit offset ``new Date()``
    would misread these UTC values as local time.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _event_dict(e: LowAltitudeEvent) -> dict:
    return {
        "id": e.id,
        "icao24": e.icao24,
        "n_number": e.n_number,
        "callsign": e.callsign,
        "first_seen": _iso_utc(e.first_seen),
        "last_seen": _iso_utc(e.last_seen),
        "min_agl_ft": round(e.min_agl_ft),
        "min_msl_ft": round(e.min_msl_ft),
        "closest_distance_m": round(e.closest_distance_m),
        "sample_count": e.sample_count,
        "near_airport": e.near_airport,
        "near_helipad": e.near_helipad,
        "likely_approach_departure": e.likely_approach_departure,
        "is_rotorcraft": e.is_rotorcraft,
        "aircraft_type": e.aircraft_type,
        "aircraft_model": e.aircraft_model,
        "is_exempt": e.is_exempt,
        "exempt_reason": e.exempt_reason,
        "is_open": e.is_open,
    }
