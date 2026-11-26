"""Independent cadences. A slow research interval does not delay risk monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import Settings


@dataclass(frozen=True)
class Clocks:
    scan: datetime | None = None
    research: datetime | None = None
    monitor: datetime | None = None
    reconcile: datetime | None = None


def _ready(last: datetime | None, now: datetime, seconds: int) -> bool:
    if last is None:
        return True
    return (now - last).total_seconds() >= seconds


def due_actions(
    clocks: Clocks,
    now: datetime,
    cfg: Settings,
    *,
    research_seconds: int | None = None,
) -> frozenset[str]:
    actions: set[str] = set()
    if _ready(clocks.scan, now, cfg.scan_seconds):
        actions.add("scan")
    # The configured cadence is the floor. Budget pacing may only slow research.
    interval = cfg.research_seconds if research_seconds is None else max(cfg.research_seconds, research_seconds)
    if _ready(clocks.research, now, interval):
        actions.add("research")
    if _ready(clocks.monitor, now, cfg.monitor_seconds):
        actions.add("monitor")
    if _ready(clocks.reconcile, now, cfg.reconcile_seconds):
        actions.add("reconcile")
    return frozenset(actions)
