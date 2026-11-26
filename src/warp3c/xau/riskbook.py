"""Unified virtual book. U1–U10 and M19. Size never depends on prior P&L (U9)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from .clock import trading_day_start, week_start
from .profiles import Profile
from .stats import paused_models

D = Decimal
OPEN = {"PENDING", "FILLED", "TP1", "TRAIL"}
CLOSED = {"TP2", "SL", "BE", "TIME", "RUNNER", "RUNNER_EMA50", "NEWS_CUT"}


class RiskBook:
    def __init__(self, rows: list[dict], profile: Profile, now: datetime, *, ema_resumed: bool = False):
        self.rows = rows
        self.profile = profile
        self.now = now
        self.day_start = trading_day_start(now)
        self.week_start = week_start(now)
        self.equity = self._equity()
        self.paused = paused_models(self._closed(rows))
        self.ema_killed = (not ema_resumed) and self._ema_week_killed()

    def allow(self, engine: str, model: str, window_key: str) -> tuple[bool, str]:
        """Return whether a new signal may be sent. Journal-only signals are decided by the caller."""
        if self._open():
            return False, "E4"
        if self._pause_until() is not None and self.now < self._pause_until():
            return False, "U5"
        if self._attempts(window_key, self.day_start, None) >= 4:
            return False, "U1"
        if self._attempts(None, self.day_start, None) >= 6:
            return False, "U1"
        if engine == "EMA" and self._sent(None, self.day_start, "EMA") >= 2:
            return False, "M19"
        if self._losses(window_key, None, None) >= 2:
            return False, "U2"
        if self._stopped_day():
            return False, "U3"
        if self._day_r() >= D("2.5"):
            return False, "U4"
        if engine == "SMC" and self._r_since(self.week_start, "SMC") <= D("-5"):
            return False, "U6"
        if engine == "EMA" and (self.ema_killed or self._ema_day_stopped()):
            return False, "M19"
        if model in self.paused:
            return False, "U7"
        return True, ""

    def _equity(self) -> D:
        equity = self.profile.starting_balance
        for row in self._closed(self.rows):
            if str(row.get("sent")) not in {"1", "True"}:
                continue
            realized = _d(row.get("realized_r"))
            pct = _d(row.get("risk_pct")) or D("0")
            if realized is None:
                continue
            equity += realized * equity * pct / D("100")
        return equity

    def _closed(self, rows: list[dict]) -> list[dict]:
        return [row for row in rows if str(row.get("status")) in CLOSED]

    def _open(self) -> bool:
        """E4 counts sent signals only. Shadow and rejected rows stay in the journal."""
        return any(
            str(row.get("status")) in OPEN and str(row.get("sent")) in {"1", "True"}
            for row in self.rows
        )

    def _attempts(self, window_key: str | None, day_start: datetime | None, engine: str | None) -> int:
        count = 0
        for row in self.rows:
            if str(row.get("sent")) not in {"1", "True"}:
                continue
            if not row.get("filled_at"):
                continue
            if window_key and row.get("window") != window_key:
                continue
            if engine and row.get("engine") != engine:
                continue
            if day_start and _time(row.get("filled_at")) < day_start:
                continue
            count += 1
        return count

    def _sent(self, window_key, day_start, engine) -> int:
        count = 0
        for row in self.rows:
            if str(row.get("sent")) not in {"1", "True"}:
                continue
            if engine and row.get("engine") != engine:
                continue
            if day_start and _time(row.get("created_at_utc")) < day_start:
                continue
            count += 1
        return count

    def _losses(self, window_key, day_start, engine) -> int:
        count = 0
        for row in self._closed(self.rows):
            realized = _d(row.get("realized_r"))
            if realized is None or realized >= 0:
                continue
            if window_key and row.get("window") != window_key:
                continue
            if engine and row.get("engine") != engine:
                continue
            if day_start and _time(row.get("exit_at") or row.get("created_at_utc")) < day_start:
                continue
            count += 1
        return count

    def _day_r(self) -> D:
        return self._r_since(self.day_start, None)

    def _r_since(self, start: datetime, engine: str | None) -> D:
        total = D("0")
        for row in self._closed(self.rows):
            if engine and row.get("engine") != engine:
                continue
            moment = _time(row.get("exit_at") or row.get("created_at_utc"))
            if moment < start:
                continue
            realized = _d(row.get("realized_r"))
            if realized is not None:
                total += realized
        return total

    def _pnl_since(self, start: datetime, engine: str | None) -> D:
        total = D("0")
        equity = self.profile.starting_balance
        for row in self._closed(self.rows):
            realized = _d(row.get("realized_r"))
            pct = _d(row.get("risk_pct")) or D("0")
            if realized is None:
                continue
            moment = _time(row.get("exit_at") or row.get("created_at_utc"))
            delta = realized * equity * pct / D("100")
            if (engine is None or row.get("engine") == engine) and moment >= start:
                total += delta
            equity += delta
        return total

    def _stopped_day(self) -> bool:
        """U3. −1.5R or −0.8% of equity, whichever comes first."""
        if self._day_r() <= D("-1.5"):
            return True
        return self._pnl_since(self.day_start, None) <= -(D("0.008") * self.equity)

    def _ema_day_stopped(self) -> bool:
        losses = self._losses(None, self.day_start, "EMA")
        if losses >= 2:
            return True
        return self._pnl_since(self.day_start, "EMA") <= -(D("0.010") * self.equity)

    def _ema_week_killed(self) -> bool:
        return self._pnl_since(self.week_start, "EMA") <= -(D("0.030") * self.profile.starting_balance)

    def _pause_until(self) -> datetime | None:
        streak = 0
        last = None
        for row in self._closed(self.rows):
            realized = _d(row.get("realized_r"))
            if realized is None:
                continue
            if realized < 0:
                streak += 1
                last = _time(row.get("exit_at") or row.get("created_at_utc"))
            else:
                streak = 0
        if streak >= 4 and last is not None:
            return last + timedelta(hours=24)
        return None


def _d(value) -> D | None:
    if value in (None, ""):
        return None
    try:
        return D(str(value))
    except Exception:
        return None


def _time(value) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value or "1970-01-01T00:00:00+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        from datetime import timezone
        moment = moment.replace(tzinfo=timezone.utc)
    return moment
