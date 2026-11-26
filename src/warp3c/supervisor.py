"""One writer loop. Research runs beside monitoring and cannot submit an order."""

from __future__ import annotations

import math
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .llm import LlmError
from .market import MarketDataError
from .notify import channel_configured, enqueue_incident
from .schedule import Clocks, due_actions
from .telemetry import log_event
from .vibe import ResearchError

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
                _queue_research_alert(engine, code, error, self._consecutive, now, cfg)
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

    def note_budget_wait(self, engine, now: datetime) -> None:
        """Not enough calls left for one run. Wait for the UTC day. No cap error and no alert."""
        day = now.astimezone(timezone.utc).date().isoformat()
        tomorrow = (now.astimezone(timezone.utc) + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        with self._lock:
            self._state = "budget_wait"
            self._error = None
            self._error_code = None
            self._consecutive = 0
            self._next = tomorrow
            view = self._status_locked()
        journal = getattr(engine, "journal", None)
        logged = None if journal is None else journal.get_setting("budget_wait_day")
        if logged != day:
            log_event("budget_wait", day=day)
            if journal is not None:
                journal.set_setting("budget_wait_day", day)
        engine.research_status = view

    def _status_locked(self) -> dict:
        return {
            "state": self._state,
            "error_code": self._error_code,
            "last_ok_at": None if self._last_ok is None else self._last_ok.isoformat(),
            "last_failed_at": None if self._last_failed is None else self._last_failed.isoformat(),
            "next_attempt_at": None if self._next is None else self._next.isoformat(),
            "consecutive_failures": self._consecutive,
        }


def _queue_research_alert(engine, code: str, error: BaseException, consecutive: int, now: datetime, cfg) -> None:
    """First failure of a streak, and the daily cap whenever it is reached."""
    journal = getattr(engine, "journal", None)
    channel = channel_configured(cfg)
    if consecutive == 1:
        enqueue_incident(
            journal,
            f"research_failed:{code}",
            f"{code} {type(error).__name__}"[:400],
            now=now,
            channel=channel,
        )
    if code == "llm_cap":
        enqueue_incident(journal, "llm_cap", "llm daily cap reached", now=now, channel=channel)


def _next_from_clock(clocks: Clocks, research_seconds: int) -> datetime | None:
    if clocks.research is None:
        return None
    return clocks.research + timedelta(seconds=research_seconds)


def clocks_from_journal(journal) -> Clocks:
    """Seed the research clock from the last start, so a restart does not run again."""
    started = None
    reader = getattr(journal, "research_started_at", None)
    if callable(reader):
        started = reader()
    return Clocks(research=started)


def _calls_per_run(analyst) -> int:
    """Screen plus final on the happy path. Screen runs only when nvidia is configured."""
    nvidia = bool(getattr(analyst, "nvidia_key", ""))
    router = bool(getattr(analyst, "openrouter_key", ""))
    if not nvidia and not router:
        return 0
    return (1 if nvidia else 0) + 1


def _research_budget(engine) -> tuple[int, int] | None:
    """Remaining daily calls and the calls one healthy run spends. None when this is not the LLM path."""
    # Vibe never spends the local budget. auto and llm do, and only when the analyst is configured.
    analysis = getattr(getattr(engine, "cfg", None), "analysis", "auto")
    if analysis not in {"llm", "auto"}:
        return None
    analyst = getattr(engine, "analyst", None)
    budget = getattr(analyst, "budget", None)
    if analyst is None or budget is None:
        return None
    configured = getattr(analyst, "configured", None)
    if callable(configured) and not configured():
        return None
    per_run = _calls_per_run(analyst)
    if per_run < 1:
        return None
    try:
        snap = budget.snapshot()
        remaining = int(snap["calls_cap"]) - int(snap["calls_used"])
    except (ResearchError, TypeError, ValueError, KeyError, ArithmeticError):
        # An unreadable budget still fails inside the research thread. Do not raise into the loop.
        return None
    return remaining, per_run


def paced_research_seconds(cfg, now: datetime, remaining: int, per_run: int) -> int:
    """Spread the runs still affordable across the rest of the UTC day. Never faster than the setting."""
    base = cfg.research_seconds
    if per_run < 1 or remaining < per_run:
        return base
    runs_left = remaining // per_run
    if runs_left < 1:
        return base
    moment = now.astimezone(timezone.utc)
    midnight = (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    seconds_left = (midnight - moment).total_seconds()
    if seconds_left <= 0:
        return base
    paced = seconds_left / runs_left
    if paced <= base:
        return base
    return max(base, math.ceil(paced))


def _show_paced_next(gate: ResearchGate, engine, clocks: Clocks, interval: int) -> None:
    """Publish the time the loop will actually wait. Backoff and budget_wait keep their own clock."""
    if clocks.research is None:
        return
    with gate._lock:
        if gate._state != "ok":
            return
        gate._next = clocks.research + timedelta(seconds=interval)
        view = gate._status_locked()
    engine.research_status = view


def _score_forward(engine, moment: datetime) -> None:
    """Grade due forward rows at most once per scan interval. A failure stays inside the loop."""
    last = getattr(engine, "_forward_scored_at", None)
    interval = engine.cfg.scan_seconds
    if last is not None and (moment - last).total_seconds() < interval:
        return
    engine._forward_scored_at = moment
    try:
        engine._evaluate(moment)
    except Exception as exc:
        if not getattr(engine, "_forward_failing", False):
            log_event("forward_eval_failed", error=type(exc).__name__)
            engine._forward_failing = True
        return
    engine._forward_failing = False


def once(engine, gate: ResearchGate, clocks: Clocks, *, now: datetime | None = None) -> Clocks:
    """Monitor even when research is still running. Orders wait for a finished result."""
    moment = now or datetime.now(timezone.utc)
    # A run that outlasts the research interval is already due. Settle it before
    # start() clears the error, then decide whether the backoff still says due.
    if not gate.busy():
        clocks = gate.settle(engine, clocks, moment, engine.cfg)
    budget = None if gate.busy() else _research_budget(engine)
    interval = engine.cfg.research_seconds
    hold_for_budget = False
    if budget is not None:
        remaining, per_run = budget
        if remaining < per_run:
            hold_for_budget = True
        else:
            interval = paced_research_seconds(engine.cfg, moment, remaining, per_run)
    actions = due_actions(clocks, moment, engine.cfg, research_seconds=interval)
    if hold_for_budget:
        actions = frozenset(action for action in actions if action != "research")
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
        _score_forward(engine, moment)
    if "research" in actions and not gate.busy():
        if gate.start(engine.collect_research):
            clocks = replace(clocks, research=moment)
            journal = getattr(engine, "journal", None)
            if journal is not None and hasattr(journal, "note_research_started"):
                journal.note_research_started(moment)
    if not gate.busy():
        # Monitor may have replaced the reason. Settle again so the overlay
        # returns, and so a run that finished inside this call is recorded.
        # The generation was already handled, so this does not log a second time.
        clocks = gate.settle(engine, clocks, moment, engine.cfg)
    if hold_for_budget and not gate.busy():
        gate.note_budget_wait(engine, moment)
    _show_paced_next(gate, engine, clocks, interval)
    if engine._halted():
        return clocks
    pending = gate.take(engine.cfg.max_signal_age, moment)
    if pending is not None:
        engine.execute_cached(pending, moment)
    return clocks
