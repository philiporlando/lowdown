"""Minimal async OpenSky Network client.

Fetches ADS-B state vectors within a bounding box. Uses OAuth2 client
credentials when configured, otherwise falls back to (rate-limited) anonymous
access.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from .config import Settings

log = logging.getLogger(__name__)

# ADS-B emitter categories OpenSky reports as rotorcraft.
_ROTORCRAFT_CATEGORY = 7


@dataclass
class AircraftState:
    icao24: str
    callsign: str | None
    lon: float
    lat: float
    baro_altitude_m: float | None
    geo_altitude_m: float | None
    on_ground: bool
    velocity_ms: float | None
    heading: float | None
    vertical_rate_ms: float | None
    category: int | None

    @property
    def is_rotorcraft(self) -> bool:
        return self.category == _ROTORCRAFT_CATEGORY

    @property
    def altitude_m(self) -> float | None:
        """Best available MSL altitude (GPS/geometric preferred)."""
        return (
            self.geo_altitude_m
            if self.geo_altitude_m is not None
            else self.baro_altitude_m
        )


class OpenSkyClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._token: str | None = None
        self._token_expiry: float = 0.0

    @property
    def _has_credentials(self) -> bool:
        return bool(
            self._settings.opensky_client_id and self._settings.opensky_client_secret
        )

    async def _access_token(self) -> str | None:
        if not self._has_credentials:
            return None
        if self._token and time.monotonic() < self._token_expiry:
            return self._token

        resp = await self._client.post(
            self._settings.opensky_token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._settings.opensky_client_id,
                "client_secret": self._settings.opensky_client_secret,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        # Refresh a bit early.
        self._token_expiry = time.monotonic() + payload.get("expires_in", 1800) - 60
        return self._token

    async def states_in_box(
        self, lamin: float, lomin: float, lamax: float, lomax: float
    ) -> list[AircraftState]:
        headers = {}
        token = await self._access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = await self._client.get(
            f"{self._settings.opensky_base_url}/states/all",
            params={
                "lamin": lamin,
                "lomin": lomin,
                "lamax": lamax,
                "lomax": lomax,
            },
            headers=headers,
        )
        if resp.status_code == 429:
            log.warning("OpenSky rate limit hit (429); skipping this poll.")
            return []
        resp.raise_for_status()

        data = resp.json()
        raw_states = data.get("states") or []
        out: list[AircraftState] = []
        for s in raw_states:
            # Guard against missing position.
            if s[5] is None or s[6] is None:
                continue
            callsign = (s[1] or "").strip() or None
            out.append(
                AircraftState(
                    icao24=(s[0] or "").strip().lower(),
                    callsign=callsign,
                    lon=s[5],
                    lat=s[6],
                    baro_altitude_m=s[7],
                    geo_altitude_m=s[13],
                    on_ground=bool(s[8]),
                    velocity_ms=s[9],
                    heading=s[10],
                    vertical_rate_ms=s[11],
                    category=s[17] if len(s) > 17 else None,
                )
            )
        return out
