"""Application settings, loaded from environment / .env (prefix ``LD_``)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Airport(BaseModel):
    code: str
    lat: float
    lon: float


# Airports near Portland, OR. Aircraft on approach/departure here are legally
# exempt from the 91.119 altitude minimums, so we annotate (not hide) them.
DEFAULT_AIRPORTS: list[Airport] = [
    Airport(code="PDX", lat=45.5887, lon=-122.5975),  # Portland International
    Airport(code="TTD", lat=45.5494, lon=-122.4014),  # Portland-Troutdale
    Airport(code="HIO", lat=45.5404, lon=-122.9498),  # Hillsboro
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LD_", extra="ignore"
    )

    # Location to monitor (default: downtown Portland, OR).
    apartment_lat: float = 45.5152
    apartment_lon: float = -122.6784
    earshot_radius_m: float = 3000.0

    # Rule threshold (14 CFR 91.119(b), approximated as height above terrain).
    threshold_agl_ft: float = 1000.0
    obstacle_buffer_ft: float = 0.0

    # Polling / event grouping.
    poll_interval_s: float = 30.0
    event_gap_s: float = 300.0

    # Map viewport (on-demand) live queries. The map fetches whatever box the
    # user is looking at; these bound the OpenSky cost. ``view_max_area_deg2``
    # shrinks oversized boxes toward their center; ``view_cache_ttl_s`` reuses a
    # recent fetch for repeated polls and small pans.
    view_max_area_deg2: float = 16.0
    view_cache_ttl_s: float = 12.0

    # OpenSky Network.
    opensky_base_url: str = "https://opensky-network.org/api"
    opensky_token_url: str = (
        "https://auth.opensky-network.org/auth/realms/"
        "opensky-network/protocol/openid-connect/token"
    )
    opensky_client_id: str | None = None
    opensky_client_secret: str | None = None

    # Terrain elevation.
    elevation_provider: Literal["open-meteo", "fixed"] = "open-meteo"
    elevation_base_url: str = "https://api.open-meteo.com/v1/elevation"
    default_ground_elevation_m: float = 15.0
    elevation_grid_deg: float = 0.005

    # Approach/departure annotation.
    airport_proximity_km: float = 8.0
    vertical_rate_excepted_fpm: float = 500.0
    airports: list[Airport] = Field(default_factory=lambda: list(DEFAULT_AIRPORTS))

    # Storage / runtime.
    db_url: str = "sqlite:///data/lowdown.db"
    run_collector: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
