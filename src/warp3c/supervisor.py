"""One writer loop. Research runs beside monitoring and cannot submit an order."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .llm import LlmError
from .market import MarketDataError
from .schedule import Clocks, due_actions
from .telemetry import log_event

_NO_RETRY = frozenset({"llm_cap", "unconfigured"})
_REMINDER_SECONDS = 300


def retry_delay(failures: int, base: int, cap: int) -> int:
    """60, 120, 240, … capped at the research interval. ``failures`` starts at 1."""
    if failures < 1:
        return min(base, cap)
    delay = base
    for _ in range(failures - 1):
        if delay >= cap:
            return cap
        delay *= 2
    return min(delay, cap)


def research_error_code(exc: BaseException) -> str:
    text = str(exc)
    if text == "LLM daily cap reached":
        return "llm_cap"
    if text in {"LLM providers are not configured", "LLM analysis is not configured"}:
        return "unconfigured"
    if text == "LLM providers failed":
        return "providers_failed"
    if any(part in text for part in ("JSON", "decisions", "Duplicate", "not an object", "not a bounded")):
        return "bad_json"
    return "research_error"


def _failure_fields(exc: BaseException) -> dict:
    cause = exc.__cause__
    fields: dict = {
        "error_class": type(exc).__name__,
        "error": str(exc)[:200],
        "cause_class": type(cause).__name__ if cause is not None else None,
    }
    if isinstance(cause, LlmError):
        provider = getattr(cause, "provider", None)
        status = getattr(cause, "http_status", None)
        if provider in {"nvidia", "openrouter"}:
            fields["provider"] = provider
        if isinstance(status, int):
            fields["http_status"] = status
    return fields


class ResearchGate:
    """Holds the latest research result. The worker thread never sees the broker."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._signals: list = []
        self._error: Exception | None = None
        self._finished_at: datetime | None = None
        self._consumed = True
        self._generation = 0
        self._handled = 0
        self._logged_id: int | None = None
        self._consecutive = 0
        self._last_ok: datetime | None = None
        self._last_failed: datetime | None = None
        self._next: datetime | None = None
        self._error_code: str | None = None
        self._state = "ok"
        self._blocked_at: datetime | None = None

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
                self._generation += 1

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

    def status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def settle(self, engine, clocks: Clocks, now: datetime, cfg) -> Clocks:
        """Record one failure, schedule the backoff, and surface it on the engine."""
        with self._lock:
            error = self._error
            finished = self._finished_at
            fresh = self._generation != self._handled
            if fresh:
                self._handled = self._generation
            elif error is not None and id(error) != self._logged_id:
                fresh = True
            if fresh and error is None and finished is not None:
                self._consecutive = 0
                self._last_ok = finished
                self._error_code = None
                self._state = "ok"
                self._logged_id = None
                self._next = _next_from_clock(clocks, cfg.research_seconds)
            elif fresh and error is not None:
                self._consecutive += 1
                self._last_failed = finished or now
                code = research_error_code(error)
                self._error_code = code
                self._logged_id = id(error)
                self._blocked_at = now
                log_event("research_failed", **_failure_fields(error))
                if code in _NO_RETRY:
                    self._state = "capped" if code == "llm_cap" else "failed"
                    self._next = _next_from_clock(clocks, cfg.research_seconds)
                else:
                    delay = retry_delay(self._consecutive, cfg.research_retry_seconds, cfg.research_seconds)
                    self._state = "backoff"
                    self._next = now + timedelta(seconds=delay)
                    clocks = replace(
                        clocks,
                        research=self._next - timedelta(seconds=cfg.research_seconds),
                    )
            elif (
                error is not None
                and self._blocked_at is not None
                and (now - self._blocked_at).total_seconds() >= _REMINDER_SECONDS
            ):
                log_event(
                    "research_blocked",
                    error=type(error).__name__,
                    error_code=self._error_code,
                )
                self._blocked_at = now
            view = self._status_locked()
            failing = self._state in {"failed", "backoff", "capped"} and error is not None
            code = self._error_code
        engine.research_status = view
        reason = engine.status.reason
        if failing and reason == "ok" and code:
            engine.status.reason = f"research_blocked:{code}"
        elif not failing and isinstance(reason, str) and reason.startswith("research_blocked:"):
            engine.status.reason = "ok"
        return clocks

    def _status_locked(self) -> dict:
        return {
            "state": self._state,
            "error_code": self._error_code,
            "last_ok_at": None if self._last_ok is None else self._last_ok.isoformat(),
            "last_failed_at": None if self._last_failed is None else self._last_failed.isoformat(),
            "next_attempt_at": None if self._next is None else self._next.isoformat(),
            "consecutive_failures": self._consecutive,
        }


def _next_from_clock(clocks: Clocks, research_seconds: int) -> datetime | None:
    if clocks.research is None:
        return None
    return clocks.research + timedelta(seconds=research_seconds)


def once(engine, gate: ResearchGate, clocks: Clocks, *, now: datetime | None = None) -> Clocks:
    """Monitor even when research is still running. Orders wait for a finished result."""
    moment = now or datetime.now(timezone.utc)
    # A run that outlasts the research interval is already due. Settle it before
    # start() clears the error, then decide whether the backoff still says due.
    if not gate.busy():
        clocks = gate.settle(engine, clocks, moment, engine.cfg)
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
    if not gate.busy():
        # Monitor may have replaced the reason. Settle again so the overlay
        # returns, and so a run that finished inside this call is recorded.
        # The generation was already handled, so this does not log a second time.
        clocks = gate.settle(engine, clocks, moment, engine.cfg)
    if engine._halted():
        return clocks
    pending = gate.take(engine.cfg.max_signal_age, moment)
    if pending is not None:
        engine.execute_cached(pending, moment)
    return clocks
