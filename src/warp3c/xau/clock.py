"""Session clock. Rulebook §0 (R0.1, R0.2) and §2 (K1–K5).

Window ends are exclusive: 16:15, 21:45 Friday, 22:15, 22:30 Friday, 23:00, 02:00, and 17:00.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
WIB = ZoneInfo("Asia/Jakarta")


def as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=ZoneInfo("UTC"))
    return moment.astimezone(ZoneInfo("UTC"))


def wib(moment: datetime) -> datetime:
    return as_utc(moment).astimezone(WIB)


def ny(moment: datetime) -> datetime:
    return as_utc(moment).astimezone(NY)


def _minutes(local: datetime) -> int:
    return local.hour * 60 + local.minute


def market_closed(moment: datetime) -> bool:
    """K4. Friday 17:00 New York through Sunday 18:00, plus the daily 17:00–18:00 break."""
    local = ny(moment)
    weekday = local.weekday()
    minute = _minutes(local)
    if weekday == 4 and minute >= 17 * 60:
        return True
    if weekday == 5:
        return True
    if weekday == 6 and minute < 18 * 60:
        return True
    if weekday not in {4, 5, 6} and 17 * 60 <= minute < 18 * 60:
        return True
    return False


def trading_day_start(moment: datetime) -> datetime:
    """R0.2. The 17:00 America/New_York open that contains ``moment``."""
    local = ny(moment)
    start = local.replace(hour=17, minute=0, second=0, microsecond=0)
    if local < start:
        start -= timedelta(days=1)
    return start.astimezone(ZoneInfo("UTC"))


def week_start(moment: datetime) -> datetime:
    """R0.2. First trading-day open after Friday 17:00 New York, which is Sunday 18:00."""
    local = ny(moment)
    # Sunday 18:00 of this NY week. Monday is 0, Sunday is 6.
    days_since_sunday = (local.weekday() + 1) % 7
    sunday = (local - timedelta(days=days_since_sunday)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )
    if local < sunday:
        sunday -= timedelta(days=7)
    return sunday.astimezone(ZoneInfo("UTC"))


@dataclass(frozen=True)
class Window:
    name: str
    kind: str  # kz, ema, off, closed
    engine_slot: str  # SMC, EMA, or ""
    start_wib: datetime
    end_wib: datetime
    key: str

    def contains(self, moment: datetime) -> bool:
        local = wib(moment)
        return self.start_wib <= local < self.end_wib


def _span(local: datetime, start_min: int, end_min: int) -> tuple[datetime, datetime]:
    """Return [start, end) in WIB. end_min may wrap past midnight."""
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = day + timedelta(minutes=start_min)
    end = day + timedelta(minutes=end_min)
    if end_min <= start_min:
        if _minutes(local) < end_min:
            start -= timedelta(days=1)
        else:
            end += timedelta(days=1)
    return start, end


def window_at(moment: datetime) -> Window:
    """K1 and K3. Off-hours and the closed market are not entry windows."""
    local = wib(moment)
    minute = _minutes(local)
    if market_closed(moment):
        start, end = _span(local, 0, 24 * 60)
        return Window("closed", "closed", "", start, end, f"{local.date().isoformat()} closed")
    if 14 * 60 <= minute < 17 * 60:
        start, end = _span(local, 14 * 60, 17 * 60)
        return Window("London KZ", "kz", "SMC", start, end, f"{start.date().isoformat()} London KZ")
    if 20 * 60 <= minute < 23 * 60:
        start, end = _span(local, 20 * 60, 23 * 60)
        return Window("NY KZ", "kz", "SMC", start, end, f"{start.date().isoformat()} NY KZ")
    if 17 * 60 <= minute < 20 * 60:
        start, end = _span(local, 17 * 60, 20 * 60)
        return Window("London EMA", "ema", "EMA", start, end, f"{start.date().isoformat()} London EMA")
    if minute >= 23 * 60 or minute < 2 * 60:
        start, end = _span(local, 23 * 60, 2 * 60)
        return Window("NY EMA", "ema", "EMA", start, end, f"{start.date().isoformat()} NY EMA")
    start, end = _span(local, 0, 24 * 60)
    return Window("off", "off", "", start, end, f"{local.date().isoformat()} off")


def next_trade_window(moment: datetime) -> Window | None:
    """The next London or New York killzone or EMA window that has not started."""
    local = wib(moment)
    current = window_at(moment).key
    cursor = local
    for _ in range(8 * 24 * 60):
        cursor += timedelta(minutes=1)
        window = window_at(cursor)
        if window.kind in {"kz", "ema"} and window.key != current and window.start_wib > local:
            return window
    return None


def scalp_entry_open(moment: datetime) -> bool:
    """K2. Execution is 14:15–16:15 and 20:15–22:15. Friday NY closes at 21:45."""
    if market_closed(moment):
        return False
    local = wib(moment)
    minute = _minutes(local)
    if 14 * 60 + 15 <= minute < 16 * 60 + 15:
        return True
    if local.weekday() == 4:
        return 20 * 60 + 15 <= minute < 21 * 60 + 45
    return 20 * 60 + 15 <= minute < 22 * 60 + 15


def smc_entry_open(moment: datetime) -> bool:
    """K1 plus K5. No new SMC after Friday 22:30 WIB."""
    if market_closed(moment):
        return False
    slot = window_at(moment)
    if slot.kind != "kz":
        return False
    local = wib(moment)
    if local.weekday() == 4 and _minutes(local) >= 22 * 60 + 30:
        return False
    return True


def ema_entry_open(moment: datetime) -> bool:
    """K3. Only the two active bands, and nothing after Friday 23:00 WIB."""
    if market_closed(moment):
        return False
    if window_at(moment).kind != "ema":
        return False
    local = wib(moment)
    minute = _minutes(local)
    if minute >= 23 * 60 and local.weekday() == 4:
        return False
    if minute < 2 * 60 and (local.weekday() - 1) % 7 == 4:
        return False
    return True


def format_wib(moment: datetime, fmt: str = "%H:%M") -> str:
    return wib(moment).strftime(fmt)


def wib_date(moment: datetime):
    return wib(moment).date()
