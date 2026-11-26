"""Closed bars only. Drops the open bar, the K4 closed-market bars, and reports gaps (U8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .clock import market_closed

TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass(frozen=True)
class Bar:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")
    timeframe: str = ""

    @property
    def body(self) -> Decimal:
        return abs(self.close - self.open)

    @property
    def range(self) -> Decimal:
        return self.high - self.low

    def bullish(self) -> bool:
        return self.close > self.open

    def bearish(self) -> bool:
        return self.close < self.open


@dataclass(frozen=True)
class CleanBook:
    bars: dict[str, tuple[Bar, ...]]
    gaps: tuple[str, ...]
    stale_m5: bool


def bar_is_closed(bar: Bar, now: datetime, timeframe: str) -> bool:
    """R0.1. A bar is closed only after its full interval has ended."""
    seconds = TF_SECONDS[timeframe]
    close_at = bar.open_time + timedelta(seconds=seconds)
    return close_at <= now


def prepare(raw: dict[str, list[Bar]], now: datetime) -> CleanBook:
    """Keep closed, in-session bars. K4 discards the weekend and the daily break."""
    cleaned: dict[str, tuple[Bar, ...]] = {}
    gaps: list[str] = []
    for timeframe, bars in raw.items():
        seconds = TF_SECONDS.get(timeframe)
        if seconds is None:
            continue
        kept = [
            bar
            for bar in bars
            if bar_is_closed(bar, now, timeframe) and not market_closed(bar.open_time)
        ]
        kept.sort(key=lambda bar: bar.open_time)
        unique: list[Bar] = []
        for bar in kept:
            if unique and unique[-1].open_time == bar.open_time:
                unique[-1] = bar
            else:
                unique.append(bar)
        if timeframe != "1d":
            limit = timedelta(seconds=seconds + seconds // 2)
            for prev, nxt in zip(unique, unique[1:], strict=False):
                delta = nxt.open_time - prev.open_time
                # The daily 17:00–18:00 break removes one hour. That hole is not a gap.
                if delta > limit and not _break_hole(prev.open_time, nxt.open_time):
                    gaps.append(timeframe)
                    break
        cleaned[timeframe] = tuple(unique)
    m5 = cleaned.get("5m", ())
    stale = True
    if m5 and not market_closed(now):
        age = now - (m5[-1].open_time + timedelta(seconds=TF_SECONDS["5m"]))
        stale = age > timedelta(minutes=7)
    elif market_closed(now):
        stale = False
    return CleanBook(cleaned, tuple(gaps), stale)


def _break_hole(left: datetime, right: datetime) -> bool:
    """True when the missing span is only the scheduled New York hour break."""
    return market_closed(left + timedelta(minutes=30)) and (right - left) <= timedelta(hours=2)


def shift(bars: tuple[Bar, ...] | list[Bar], basis: Decimal) -> tuple[Bar, ...]:
    """R0.3. Published and tracked prices are perp minus the basis."""
    if basis == 0:
        return tuple(bars)
    return tuple(
        Bar(
            bar.open_time,
            bar.open - basis,
            bar.high - basis,
            bar.low - basis,
            bar.close - basis,
            bar.volume,
            bar.timeframe,
        )
        for bar in bars
    )
