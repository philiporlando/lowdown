"""FAA aircraft registry: authoritative aircraft type by N-number.

The ADS-B emitter category is frequently unset, so a helicopter often doesn't
announce itself as one. The FAA publishes a downloadable registry of all US
civil aircraft; ``sync_registry`` loads it into a local table keyed by
N-number, and :class:`AircraftTypeProvider` reads that cache at flag time.

The registry is optional — without it, flagging still works using helipad
proximity and the ADS-B category; it just can't recognise helicopters that
neither broadcast a rotorcraft category nor fly near a configured helipad.
"""

from __future__ import annotations

import csv
import io
import logging
import tempfile
import zipfile
from collections.abc import Iterator

import httpx

from .config import Settings
from .db import engine, get_session
from .models import AircraftRegistration

log = logging.getLogger(__name__)

# FAA ACFTREF "TYPE-ACFT" codes -> our category vocabulary.
_TYPE_ACFT_CATEGORY = {
    "1": "glider",
    "2": "balloon",
    "3": "blimp",
    "4": "fixed-wing",
    "5": "fixed-wing",
    "6": "rotorcraft",
    "7": "weight-shift",
    "8": "powered-parachute",
    "9": "gyroplane",
    "H": "hybrid-lift",
    "O": "other",
}

_INSERT_CHUNK = 20_000


class AircraftTypeProvider:
    """Reads the local FAA registry cache, with an in-memory layer on top."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._memory: dict[str, tuple[str | None, str | None]] = {}

    def lookup(self, n_number: str | None) -> tuple[str | None, str | None]:
        """Return ``(category, mfr_model)`` for a tail, or ``(None, None)``."""
        if not n_number:
            return (None, None)
        if n_number in self._memory:
            return self._memory[n_number]
        with get_session() as session:
            row = session.get(AircraftRegistration, n_number)
        value = (row.category, row.mfr_model) if row else (None, None)
        self._memory[n_number] = value
        return value

    def category(self, n_number: str | None) -> str | None:
        return self.lookup(n_number)[0]


def _header_index(reader: Iterator[list[str]]) -> dict[str, int]:
    header = next(reader)
    return {name.strip().upper(): i for i, name in enumerate(header)}


def _open_member(zf: zipfile.ZipFile, name: str) -> io.TextIOWrapper:
    # utf-8-sig strips the BOM on the header's first column; errors="replace"
    # tolerates stray bytes in name/address fields we don't read. newline=""
    # lets csv handle line endings.
    actual = next(n for n in zf.namelist() if n.upper().endswith(name.upper()))
    return io.TextIOWrapper(
        zf.open(actual), encoding="utf-8-sig", errors="replace", newline=""
    )


def _load_acftref(zf: zipfile.ZipFile) -> dict[str, tuple[str | None, str]]:
    """Map ACFTREF CODE -> (category, "MFR MODEL")."""
    out: dict[str, tuple[str | None, str]] = {}
    with _open_member(zf, "ACFTREF.txt") as fh:
        reader = csv.reader(fh)
        idx = _header_index(reader)
        c_code = idx["CODE"]
        c_mfr = idx["MFR"]
        c_model = idx["MODEL"]
        c_type = idx["TYPE-ACFT"]
        for row in reader:
            if len(row) <= c_type:
                continue
            code = row[c_code].strip()
            if not code:
                continue
            category = _TYPE_ACFT_CATEGORY.get(row[c_type].strip())
            mfr_model = f"{row[c_mfr].strip()} {row[c_model].strip()}".strip()
            out[code] = (category, mfr_model)
    return out


def sync_registry(settings: Settings | None = None) -> int:
    """Download the FAA registry and (re)populate the local cache table.

    Returns the number of aircraft written.
    """
    from .config import get_settings

    s = settings or get_settings()
    url = s.faa_registry_url
    log.info("Downloading FAA registry from %s …", url)

    # The FAA server 403s requests without a browser-like User-Agent.
    headers = {"User-Agent": "Mozilla/5.0 (lowdown aircraft monitor)"}
    with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
        with (
            httpx.Client(
                timeout=180.0, follow_redirects=True, headers=headers
            ) as client,
            client.stream("GET", url) as resp,
        ):
            resp.raise_for_status()
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                tmp.write(chunk)
        tmp.flush()

        with zipfile.ZipFile(tmp.name) as zf:
            acftref = _load_acftref(zf)
            log.info("Loaded %d aircraft reference models.", len(acftref))
            count = _load_master(zf, acftref)

    log.info("FAA registry sync complete: %d aircraft cached.", count)
    return count


def _load_master(
    zf: zipfile.ZipFile, acftref: dict[str, tuple[str | None, str]]
) -> int:
    """Stream MASTER.txt, joining to ACFTREF, into the registry table."""
    table = AircraftRegistration.__table__  # type: ignore[attr-defined]
    with engine.begin() as conn:
        conn.execute(table.delete())

    buffer: list[dict] = []
    count = 0
    with _open_member(zf, "MASTER.txt") as fh:
        reader = csv.reader(fh)
        idx = _header_index(reader)
        c_n = idx["N-NUMBER"]
        c_code = idx["MFR MDL CODE"]
        for row in reader:
            if len(row) <= max(c_n, c_code):
                continue
            raw_n = row[c_n].strip()
            if not raw_n:
                continue
            category, mfr_model = acftref.get(row[c_code].strip(), (None, None))
            buffer.append(
                {
                    "n_number": f"N{raw_n}",
                    "category": category,
                    "mfr_model": mfr_model or None,
                }
            )
            if len(buffer) >= _INSERT_CHUNK:
                count += _flush(table, buffer)
                buffer.clear()
    if buffer:
        count += _flush(table, buffer)
    return count


def _flush(table, rows: list[dict]) -> int:
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)
    return len(rows)
