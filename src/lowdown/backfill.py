"""Re-score already-recorded events against the current rules.

Useful after tuning the exemption logic (e.g. the direction-aware
approach/departure rule): each event is re-evaluated from its *stored*
observations — no OpenSky/elevation calls — and its exemption/annotation fields
are recomputed with the same aggregation the collector uses.
"""

from __future__ import annotations

import logging

from sqlmodel import select

from .config import Settings, get_settings
from .db import get_session
from .faa import AircraftTypeProvider
from .models import LowAltitudeEvent, Observation
from .rules import evaluate

log = logging.getLogger(__name__)


def reclassify_events(settings: Settings | None = None) -> tuple[int, int]:
    """Recompute exemption/annotation for every stored event.

    Returns ``(total_events, changed_events)``. Only the classification fields
    are touched — ``is_low``, min-AGL, tracks, and sample counts are left as-is.
    """
    s = settings or get_settings()
    types = AircraftTypeProvider(s)
    total = 0
    changed = 0

    with get_session() as session:
        events = session.exec(select(LowAltitudeEvent)).all()
        for event in events:
            total += 1
            obs = session.exec(
                select(Observation)
                .where(Observation.event_id == event.id)
                .order_by(Observation.ts)  # type: ignore[arg-type]
            ).all()
            if not obs:
                continue

            category, model = types.lookup(event.n_number)
            near_airport: str | None = None
            near_helipad: str | None = None
            likely_ad = False
            # Preserve a prior rotorcraft finding (ADS-B category isn't stored
            # per-observation) and feed it back so those stay exempt.
            rotor = event.is_rotorcraft
            is_exempt = False
            exempt_reason: str | None = None

            for o in obs:
                r = evaluate(
                    s,
                    lat=o.lat,
                    lon=o.lon,
                    msl_ft=o.msl_ft,
                    ground_elev_ft=o.ground_elev_ft,
                    vertical_rate_fpm=o.vertical_rate_fpm,
                    is_rotorcraft=event.is_rotorcraft,
                    heading=o.heading,
                    aircraft_category=category,
                    aircraft_model=model,
                )
                likely_ad = likely_ad or r.likely_approach_departure
                rotor = rotor or r.is_rotorcraft
                if r.near_airport and not near_airport:
                    near_airport = r.near_airport
                if r.near_helipad and not near_helipad:
                    near_helipad = r.near_helipad
                if r.is_exempt and not is_exempt:
                    is_exempt = True
                    exempt_reason = r.exempt_reason

            before = (
                event.is_exempt,
                event.near_airport,
                event.likely_approach_departure,
            )
            after = (is_exempt, near_airport, likely_ad)

            event.near_airport = near_airport
            event.near_helipad = near_helipad
            event.likely_approach_departure = likely_ad
            event.is_rotorcraft = rotor
            event.is_exempt = is_exempt
            event.exempt_reason = exempt_reason
            if category and not event.aircraft_type:
                event.aircraft_type = category
            if model and not event.aircraft_model:
                event.aircraft_model = model
            session.add(event)

            if before != after:
                changed += 1
                log.info(
                    "Event %s (%s): exempt %s -> %s",
                    event.id,
                    event.n_number or event.icao24,
                    before[0],
                    after[0],
                )

        session.commit()

    return total, changed
