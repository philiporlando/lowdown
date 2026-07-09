"""In-process runtime state shared between the collector and the API.

Holds the most recent snapshot of aircraft currently within the watch radius so
the live map can render without each request hitting OpenSky.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any


@dataclass
class RuntimeState:
    last_poll: datetime | None = None
    last_error: str | None = None
    snapshot: list[dict[str, Any]] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def update(self, snapshot: list[dict[str, Any]], when: datetime) -> None:
        with self._lock:
            self.snapshot = snapshot
            self.last_poll = when
            self.last_error = None

    def set_error(self, message: str) -> None:
        with self._lock:
            self.last_error = message

    def read(self) -> tuple[list[dict[str, Any]], datetime | None, str | None]:
        with self._lock:
            return list(self.snapshot), self.last_poll, self.last_error


runtime_state = RuntimeState()
