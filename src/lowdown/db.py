"""Database engine and session management."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

_settings = get_settings()

_connect_args = {}
if _settings.db_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(_settings.db_url, connect_args=_connect_args)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    """Enable WAL so the collector and API can read/write concurrently."""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
    except Exception:  # noqa: BLE001 — non-sqlite backends ignore this
        pass


# Columns added to lowaltitudeevent after its first release. SQLModel's
# create_all only creates missing *tables*, never missing columns, so we add
# them by hand for pre-existing SQLite databases.
_EVENT_COLUMN_ADDITIONS: dict[str, str] = {
    "near_helipad": "VARCHAR",
    "aircraft_type": "VARCHAR",
    "aircraft_model": "VARCHAR",
    "is_exempt": "BOOLEAN NOT NULL DEFAULT 0",
    "exempt_reason": "VARCHAR",
}


def _migrate_sqlite() -> None:
    """Add columns introduced after the initial schema to an existing DB."""
    if not _settings.db_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        existing = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(lowaltitudeevent)")
        }
        if not existing:
            return  # table doesn't exist yet; create_all will build it fresh
        for column, ddl in _EVENT_COLUMN_ADDITIONS.items():
            if column not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE lowaltitudeevent ADD COLUMN {column} {ddl}"
                )


def init_db() -> None:
    """Create the SQLite parent directory (if any) and all tables."""
    if _settings.db_url.startswith("sqlite:///"):
        db_path = Path(_settings.db_url.removeprefix("sqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
    # Import models so their tables are registered on SQLModel.metadata.
    from . import models  # noqa: F401

    _migrate_sqlite()
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
