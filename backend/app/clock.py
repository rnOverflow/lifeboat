"""Injectable clock so the DR engine is deterministic under test."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import overload


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock(Clock):
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> datetime:
        with self._lock:
            self._now += timedelta(seconds=seconds)
            return self._now


@overload
def aware(dt: datetime) -> datetime: ...
@overload
def aware(dt: None) -> None: ...
def aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; normalise to UTC-aware."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
