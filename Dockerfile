# syntax=docker/dockerfile:1

# Pinned base + uv for a reproducible supply chain. For the strongest guarantee,
# pin the base image by digest (python:3.12-slim-bookworm@sha256:...) and let
# Renovate/Dependabot bump it; Python deps are already pinned via uv.lock.
FROM python:3.12-slim-bookworm

# uv: fast, reproducible Python dependency management (pinned, not :latest).
COPY --from=ghcr.io/astral-sh/uv:0.8.12 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    # Behind a trusted reverse proxy (Pangolin/Traefik) on a shared Docker
    # network, honour its X-Forwarded-* headers. "*" is safe only because this
    # container is never published to the host — the proxy is the only client.
    # Restrict to the proxy's subnet for defence-in-depth if you prefer.
    FORWARDED_ALLOW_IPS="*"

WORKDIR /app

# Install dependencies from the locked set first (better layer caching).
# --frozen fails the build if pyproject.toml and uv.lock disagree, so the image
# can never drift from the committed lockfile.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Run as an unprivileged user. UID 1000 matches a typical host user so a
# bind-mounted ./data volume is writable; use `chown 1000:1000 ./data` if not.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

EXPOSE 8000

# In-container liveness check (localhost, not through the proxy). /healthz is
# unauthenticated at the app layer; allow it in Pangolin if you want external
# uptime checks.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"]

# Serves the dashboard/API and (by default) runs the collector in-process.
# --proxy-headers trusts X-Forwarded-* from FORWARDED_ALLOW_IPS (set above).
CMD ["uvicorn", "lowdown.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
