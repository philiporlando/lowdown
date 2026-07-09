# syntax=docker/dockerfile:1
FROM python:3.12-slim

# uv: fast, reproducible Python dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --no-dev

EXPOSE 8000

# Serves the dashboard/API and (by default) runs the collector in-process.
CMD ["uv", "run", "uvicorn", "lowdown.api:app", "--host", "0.0.0.0", "--port", "8000"]
