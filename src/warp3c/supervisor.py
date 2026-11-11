"""One writer loop. Research runs beside monitoring and cannot submit an order."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone

from .market import MarketDataError
from .schedule import Clocks, due_actions
from .telemetry import log_event


class ResearchGate:
    """Holds the latest research result. The worker thread never sees the broker."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._signals: list = []
        self._error: Exception | None = None
        self._finished_at: datetime | None = None
        self._consumed = True

    @property
    def error(self) -> Exception | None:
        with self._lock:
            return self._error

    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, fn) -> bool:
        if self.busy():
            return False
        with self._lock:
            self._consumed = True
            self._error = None

        def run() -> None:
            try:
                result = fn()
                error = None
            except Exception as exc:
                result = []
                error = exc
            with self._lock:
                self._signals = list(result)
                self._error = error
                self._finished_at = datetime.now(timezone.utc)
                self._consumed = error is not None

        self._thread = threading.Thread(target=run, name="warp3c-research", daemon=True)
        self._thread.start()
        return True

    def take(self, max_age_minutes: int, now: datetime) -> list | None:
        with self._lock:
            if self._consumed or self._finished_at is None or self._error is not None:
                return None
            age = (now - self._finished_at).total_seconds()
            if age < -60 or age > max_age_minutes * 60:
                return None
            self._consumed = True
            return list(self._signals)


def once(engine, gate: ResearchGate, clocks: Clocks, *, now: datetime | None = None) -> Clocks:
    """Monitor even when research is still running. Orders wait for a finished result."""
    moment = now or datetime.now(timezone.utc)
    actions = due_actions(clocks, moment, engine.cfg)
    if "monitor" in actions or "reconcile" in actions:
        engine.monitor()
        clocks = replace(
            clocks,
            monitor=moment if "monitor" in actions else clocks.monitor,
            reconcile=moment if "reconcile" in actions else clocks.reconcile,
        )
    if "scan" in actions:
        try:
            engine.refresh_market(moment)
            clocks = replace(clocks, scan=moment)
        except MarketDataError:
            engine.evidence = {}
            engine.status.market_data_fresh = False
            engine.status.reason = "market_data"
            log_event("market_data_blocked", mode=engine.cfg.mode)
    if "research" in actions and not gate.busy():
        gate.start(engine.collect_research)
        clocks = replace(clocks, research=moment)
    if gate.error is not None:
        log_event("research_blocked", error=type(gate.error).__name__)
    pending = gate.take(engine.cfg.max_signal_age, moment)
    if pending is not None and not engine._halted():
        engine.execute_cached(pending, moment)
    return clocks
