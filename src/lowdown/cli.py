"""Command-line entrypoint: ``lowdown {serve,collect,initdb}``."""

from __future__ import annotations

import argparse
import asyncio
import logging

from .config import get_settings
from .db import init_db


def main() -> None:
    parser = argparse.ArgumentParser(prog="lowdown")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="Run the dashboard/API (default).")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)

    sub.add_parser("collect", help="Run only the polling collector.")
    sub.add_parser("initdb", help="Create database tables and exit.")

    args = parser.parse_args()
    command = args.command or "serve"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if command == "initdb":
        init_db()
        print("Database initialized.")
        return

    if command == "collect":
        from .collector import Collector

        init_db()
        asyncio.run(Collector(get_settings()).run())
        return

    # serve
    import uvicorn

    uvicorn.run(
        "lowdown.api:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
