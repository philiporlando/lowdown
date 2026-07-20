# lowdown 🛩️

Logs and flags **low-flying aircraft** over a point of interest (a Portland, OR
neighborhood by default) so you can build a documented record for a noise
complaint. It polls live ADS-B data from the
[OpenSky Network](https://opensky-network.org/), converts each aircraft's
altitude to **height above terrain (AGL)**, and flags anything below the
threshold — while honestly annotating the cases that are legally exempt.

![lowdown dashboard — live map of aircraft in the watch radius alongside a table of flagged low-altitude events, with helicopters annotated as exempt](docs/screenshot.png)

## What "violation" means here (read this)

The rule is [14 CFR § 91.119](https://www.ecfr.gov/current/title-14/section-91.119).
Over a **congested/urban area** an aircraft must stay **1,000 ft above the
highest obstacle within 2,000 ft** — *not* 1,000 ft above sea level. This tool
approximates that as height above terrain, so it produces **apparent
low-altitude events, not legal conclusions**. Honesty caveats are baked in:

- **Takeoff and landing are exempt.** PDX, Troutdale, and Hillsboro are nearby.
  Aircraft actually tracking toward (or climbing out of) an airport are
  annotated as `likely_approach_departure`, not hidden; a level fly-by that
  merely passes an airport stays flagged.
- **Medevac helicopters** legally descend to hospital helipads (OHSU, Emanuel,
  Life Flight, etc.). Low passes near a configured helipad are annotated as
  exempt, as are rotorcraft generally under 91.119(d).
- ADS-B altitude and terrain models carry error of a few hundred feet, and we
  can't see individual buildings.

That's still exactly what an airport noise office wants: tail number, time,
altitude, and track. To report, use the FAA hotline (1-866-835-5322) or your
airport's noise complaint line.

## Stack

Python 3.12 · [uv](https://docs.astral.sh/uv/) · FastAPI · SQLModel/SQLite ·
httpx · Leaflet · Docker.

## Quick start

```bash
cp .env.example .env    # set LD_POI_LAT/LON and (recommended) OpenSky creds
uv sync                 # create venv + install
uv run lowdown serve    # dashboard + collector in one process
# open http://localhost:8000
```

Or with Docker (`docker compose up --build`); the SQLite DB is persisted to
`./data` and the port binds to `127.0.0.1` only.

**OpenSky credentials** are optional but recommended — anonymous access is
heavily rate-limited. Create a free account and an **API client** at
opensky-network.org and set `LD_OPENSKY_CLIENT_ID` / `LD_OPENSKY_CLIENT_SECRET`.
At the default 30 s poll a small box stays within the free daily credit budget.

## Commands

```bash
lowdown serve        # dashboard/API (+ collector unless LD_RUN_COLLECTOR=false)
lowdown collect      # collector only
lowdown faa-sync     # download the FAA registry into the local cache
lowdown reclassify   # re-score stored events against current rules (no re-fetch)
lowdown initdb       # create tables and exit
```

`faa-sync` populates a type cache so helicopters are recognised even when they
don't broadcast a rotorcraft ADS-B category; flagging works without it.

## Deploy (GHCR + reverse proxy)

The app has **no built-in auth** — put it behind a reverse proxy/auth layer
(e.g. Pangolin/Traefik) and don't publish its port. The image runs as a
non-root user, ships a healthcheck, and honours `X-Forwarded-*`
(`uvicorn --proxy-headers`).

Push a tag to build a multi-arch image to `ghcr.io/<owner>/lowdown` via CI:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Then run it on your homelab attached to the proxy's network, **no published
ports**:

```yaml
services:
  lowdown:
    image: ghcr.io/<owner>/lowdown:latest
    restart: unless-stopped
    env_file: [.env]
    volumes:
      - ./data:/app/data      # chown 1000:1000 ./data  (image runs as UID 1000)
    networks: [pangolin]      # your existing proxy network; add routing labels
networks:
  pangolin:
    external: true
```

Notes:
- **Serve at a subdomain root** (`lowdown.example.com`), not a subpath — the
  dashboard uses absolute `/api/...` URLs.
- **Seed the FAA registry once** after first start (it lives in the data volume):
  `docker compose exec lowdown lowdown faa-sync`. Re-run periodically.
- Optionally allow `/healthz` unauthenticated for external uptime checks.
- The dashboard loads Leaflet + CARTO basemap tiles from public CDNs, so
  viewers' browsers need outbound internet.

## Configuration

All settings are environment variables prefixed `LD_` (see
[`.env.example`](.env.example) for the full list). The most useful:

| Variable | Default | Meaning |
| --- | --- | --- |
| `LD_POI_LAT` / `_LON` | downtown PDX | Center of the watch area |
| `LD_EARSHOT_RADIUS_M` | `3000` | Watch/flagging radius in meters |
| `LD_THRESHOLD_AGL_FT` | `1000` | Flag aircraft below this AGL |
| `LD_OBSTACLE_BUFFER_FT` | `0` | Pad added to the threshold for buildings |
| `LD_POLL_INTERVAL_S` | `30` | Seconds between OpenSky polls |
| `LD_EVENT_GAP_S` | `300` | Silence before an ongoing event is closed |
| `LD_ELEVATION_PROVIDER` | `open-meteo` | `open-meteo` (per-point terrain) or `fixed` |

## API

- `GET /` — dashboard (map + tables)
- `GET /api/live` — aircraft to plot: fetches the map's current viewport box on
  demand (`?lamin&lomin&lamax&lomax`), marking which are `in_earshot`; without
  bounds, falls back to the collector's latest earshot snapshot
- `GET /api/events` — flagged events (`?status=open|closed|all`, `?limit=`)
- `GET /api/events/{id}` — one event with its full track (observations)
- `GET /api/tail/{n_number}` — event history for a tail number
- `GET /api/stats` — counts (total, exempt, approach/departure, distinct aircraft)
- `GET /api/config` — the watch center, radius, and threshold the UI renders
- `GET /healthz` — liveness + last poll time

## How it works

```
OpenSky /states/all (bbox)
      │  every LD_POLL_INTERVAL_S
      ▼
collector ──► filter to earshot radius (haversine) ──► terrain elevation
      │                                                 (Open-Meteo, cached)
      │                                                        │
      │                                          rules.evaluate() → AGL < threshold?
      ▼                                                        │
group consecutive hits per aircraft into  ◄──────────────────┘
LowAltitudeEvent + Observation rows (SQLite)
      │
      ▼
FastAPI dashboard / JSON API + Leaflet map (viewport-driven, on-demand)
```

US tail numbers are derived directly from the ICAO24 address (no lookup needed)
via the deterministic FAA N-number algorithm in
[`nnumber.py`](src/lowdown/nnumber.py).

## Tests & quality gate

```bash
uv run python -m pytest       # unit tests
uv run ruff check src tests   # lint  (ruff format … to auto-format)
uv run mypy                   # static types (a blocking gate)
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs all of the above
plus a Dockerfile lint (hadolint) and an image build + `/healthz` smoke test on
every PR; the [publish workflow](.github/workflows/publish-image.yml)
re-validates before pushing to GHCR.

## Limitations / ideas

- OpenSky coverage of very low aircraft can be spotty; a local RTL-SDR +
  dump1090 feed would catch more (swap `opensky.py`).
- No obstacle/building database — `LD_OBSTACLE_BUFFER_FT` is a blunt stand-in.
- Congested-vs-non-congested is approximated as "everything in the radius is
  urban" (true for central Portland).
</content>
</invoke>
