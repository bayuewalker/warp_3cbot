"""Immutable status snapshot. Readers swap a reference and take no lock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

Clock = Callable[[], datetime]

_DISABLED = {"enabled": False, "state": "disabled"}


class StatusHub:
    """Published dicts are never mutated. ``snapshot`` only reads the current reference."""

    def __init__(self) -> None:
        self._current: dict = dict(_DISABLED)
        self.clock: Clock = lambda: datetime.now(timezone.utc)
        self.cycle_seconds = 30

    def publish(self, snap: dict) -> None:
        self._current = snap

    def snapshot(self) -> dict:
        snap = self._current
        state = snap.get("state")
        heartbeat = snap.get("heartbeat_at")
        if state not in {"running", "degraded", "starting"} or not heartbeat:
            return snap
        try:
            stamped = datetime.fromisoformat(heartbeat)
        except ValueError:
            return snap
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=timezone.utc)
        limit = max(timedelta(minutes=5), timedelta(seconds=4 * int(snap.get("cycle_seconds") or self.cycle_seconds)))
        age = self.clock() - stamped
        if age > limit:
            stalled = dict(snap)
            stalled["state"] = "stalled"
            return stalled
        return snap


HUB = StatusHub()


def snapshot() -> dict:
    return HUB.snapshot()


def publish(snap: dict) -> None:
    HUB.publish(snap)
