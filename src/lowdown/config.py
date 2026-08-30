"""Application settings, loaded from environment / .env (prefix ``LD_``)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Airport(BaseModel):
    code: str
    lat: float
    lon: float


class Helipad(BaseModel):
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

# Hospital (and other) helipads near Portland, OR. Medevac helicopters
# (e.g. Life Flight) legally descend to land here; low passes within a short
# radius are annotated as likely approach/departure rather than counted as
# apparent violations.
DEFAULT_HELIPADS: list[Helipad] = [
    Helipad(code="OHSU", lat=45.4993, lon=-122.6855),  # OHSU Hospital
    Helipad(code="EMANUEL", lat=45.5435, lon=-122.6668),  # Legacy Emanuel (trauma)
    Helipad(code="GOOD-SAM", lat=45.5308, lon=-122.6875),  # Legacy Good Samaritan
    Helipad(code="PROVIDENCE", lat=45.5432, lon=-122.5905),  # Providence Portland
    Helipad(code="ADVENTIST", lat=45.5140, lon=-122.5490),  # Adventist Health
    Helipad(code="TUALITY", lat=45.5219, lon=-122.9847),  # Hillsboro Medical
    Helipad(code="SALMON-CREEK", lat=45.7075, lon=-122.6535),  # Legacy Salmon Creek
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="LD_", extra="ignore")

    # Point of interest to monitor (default: downtown Portland, OR).
    poi_lat: float = 45.5152
    poi_lon: float = -122.6784
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
    # An aircraft is treated as approaching an airport only if its ground track
    # points within this many degrees of the bearing to it (otherwise it's only
    # a departure if also climbing). Guards against exempting mere fly-bys.
    airport_toward_cone_deg: float = 90.0
    # Helipads are small point facilities, so use a tight radius.
    helipad_proximity_km: float = 1.0
    vertical_rate_excepted_fpm: float = 500.0
    airports: list[Airport] = Field(default_factory=lambda: list(DEFAULT_AIRPORTS))
    helipads: list[Helipad] = Field(default_factory=lambda: list(DEFAULT_HELIPADS))

    # FAA aircraft registry (for authoritative aircraft type by N-number).
    # Populate the local cache with ``lowdown faa-sync``; flagging works without
    # it, just without registry-derived type exemptions.
    faa_registry_url: str = "https://registry.faa.gov/database/ReleasableAircraft.zip"

    # Basemap tiles for the dashboard map. CARTO now stamps "API KEY REQUIRED"
    # across every raster tile unless a key is supplied; a free one (non-commercial,
    # 5M tiles/month) comes from https://carto.com/basemaps/apikey/. Without a key
    # the map still works, just watermarked. Styles: light_all, dark_all,
    # rastertiles/voyager. The key is served to the browser (it has to be, the
    # tiles are fetched client-side), so restrict it by domain in CARTO's console.
    basemap_url_template: str = (
        "https://{s}.basemaps.cartocdn.com/{style}/{z}/{x}/{y}{r}.png"
    )
    basemap_style: str = "light_all"
    basemap_api_key: str | None = None
    basemap_attribution: str = "© OpenStreetMap contributors © CARTO"

    # Storage / runtime.
    db_url: str = "sqlite:///data/lowdown.db"
    run_collector: bool = True

    @property
    def basemap_url(self) -> str:
        """Tile URL for Leaflet: ``{style}`` resolved and the API key appended.

        Leaflet fills in ``{s}``/``{z}``/``{x}``/``{y}``/``{r}`` itself, so
        substitute with ``replace`` rather than ``str.format`` (which would choke
        on those placeholders).
        """
        url = self.basemap_url_template.replace("{style}", self.basemap_style)
        if self.basemap_api_key:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}key={quote(self.basemap_api_key, safe='')}"
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
