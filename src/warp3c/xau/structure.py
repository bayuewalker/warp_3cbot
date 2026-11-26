"""Structure primitives. Rulebook R0.5 and D1–D13. Closed bars only; no look-ahead."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from .candles import Bar
from .clock import WIB, wib

D = Decimal
Q = D("0.01")


def q_away(price: D, entry: D) -> D:
    """R0.4. Stop-loss rounds away from entry so rounding never improves R."""
    if price >= entry:
        return price.quantize(Q, rounding=ROUND_UP)
    return price.quantize(Q, rounding=ROUND_DOWN)


def q_toward(price: D, entry: D) -> D:
    """R0.4. Take-profit rounds toward entry so rounding never improves R."""
    if price >= entry:
        return price.quantize(Q, rounding=ROUND_DOWN)
    return price.quantize(Q, rounding=ROUND_UP)


def ema_series(values: list[D], n: int) -> list[D | None]:
    """R0.5. α = 2/(n+1), seeded with SMA(n)."""
    if n < 1 or len(values) < n:
        return [None] * len(values)
    alpha = D(2) / D(n + 1)
    seed = sum(values[:n], D("0")) / D(n)
    out: list[D | None] = [None] * (n - 1)
    out.append(seed)
    for value in values[n:]:
        previous = out[-1]
        assert previous is not None
        out.append(alpha * value + (D(1) - alpha) * previous)
    return out


def true_ranges(bars: list[Bar] | tuple[Bar, ...]) -> list[D]:
    ranges: list[D] = []
    for index, bar in enumerate(bars):
        if index == 0:
            ranges.append(bar.high - bar.low)
            continue
        prev = bars[index - 1].close
        ranges.append(max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev)))
    return ranges


def atr_series(bars: list[Bar] | tuple[Bar, ...], n: int = 14) -> list[D | None]:
    """R0.5. Wilder ATR. The first value is the SMA of the first n true ranges."""
    if len(bars) < n:
        return [None] * len(bars)
    trs = true_ranges(bars)
    seed = sum(trs[:n], D("0")) / D(n)
    out: list[D | None] = [None] * (n - 1)
    out.append(seed)
    for tr in trs[n:]:
        previous = out[-1]
        assert previous is not None
        out.append((previous * D(n - 1) + tr) / D(n))
    return out


def atr_last(bars: list[Bar] | tuple[Bar, ...], n: int = 14) -> D | None:
    series = atr_series(bars, n)
    return series[-1] if series else None


@dataclass(frozen=True)
class Swing:
    index: int
    kind: str  # high or low
    price: D
    confirmed_index: int


def swings(bars: list[Bar] | tuple[Bar, ...], wing: int = 2) -> list[Swing]:
    """D1 (wing 2) and D4 (wing 1). Confirmed only once the right-hand candle closes."""
    found: list[Swing] = []
    last = len(bars) - wing - 1
    for index in range(wing, last + 1):
        high = bars[index].high
        low = bars[index].low
        left = bars[index - wing : index]
        right = bars[index + 1 : index + wing + 1]
        if all(high > bar.high for bar in left) and all(high > bar.high for bar in right):
            found.append(Swing(index, "high", high, index + wing))
        if all(low < bar.low for bar in left) and all(low < bar.low for bar in right):
            found.append(Swing(index, "low", low, index + wing))
    return found


@dataclass(frozen=True)
class StructEvent:
    kind: str  # bos or choch
    direction: str  # bullish or bearish
    index: int
    price: D


def structure_events(bars: list[Bar] | tuple[Bar, ...], wing: int = 2) -> list[StructEvent]:
    """D2 BOS and D3/D4 CHoCH. A wick that does not close through is ignored."""
    points = swings(bars, wing)
    events: list[StructEvent] = []
    trend: str | None = None
    for index, bar in enumerate(bars):
        known = [point for point in points if point.confirmed_index < index]
        highs = [point for point in known if point.kind == "high"]
        lows = [point for point in known if point.kind == "low"]
        last_high = highs[-1] if highs else None
        last_low = lows[-1] if lows else None
        if last_high is not None and bar.close > last_high.price:
            kind = "choch" if trend == "bearish" else "bos"
            events.append(StructEvent(kind, "bullish", index, last_high.price))
            trend = "bullish"
        elif last_low is not None and bar.close < last_low.price:
            kind = "choch" if trend == "bullish" else "bos"
            events.append(StructEvent(kind, "bearish", index, last_low.price))
            trend = "bearish"
    return events


def bias(bars: list[Bar] | tuple[Bar, ...], wing: int = 2) -> str:
    """D11. Bullish, bearish, or range."""
    events = [event for event in structure_events(bars, wing) if event.kind == "bos"]
    if not events or not bars:
        return "range"
    last = events[-1]
    points = [point for point in swings(bars, wing) if point.confirmed_index < len(bars)]
    close = bars[-1].close
    if last.direction == "bullish":
        lows = [point for point in points if point.kind == "low"]
        if lows and close > lows[-1].price:
            return "bullish"
        return "range"
    highs = [point for point in points if point.kind == "high"]
    if highs and close < highs[-1].price:
        return "bearish"
    return "range"


@dataclass(frozen=True)
class FVG:
    index: int  # index of the middle candle
    direction: str
    low: D
    high: D

    @property
    def ce(self) -> D:
        return (self.low + self.high) / 2

    @property
    def size(self) -> D:
        return self.high - self.low


def fvgs(bars: list[Bar] | tuple[Bar, ...], atr: D | None) -> list[FVG]:
    """D6. Minimum size is max($0.30, 0.10 × ATR). A close through the far edge kills it."""
    if atr is None:
        return []
    minimum = max(D("0.30"), D("0.10") * atr)
    found: list[FVG] = []
    for index in range(2, len(bars)):
        first, _mid, third = bars[index - 2], bars[index - 1], bars[index]
        if first.high < third.low:
            zone_low, zone_high = first.high, third.low
            direction = "bullish"
        elif first.low > third.high:
            zone_low, zone_high = third.high, first.low
            direction = "bearish"
        else:
            continue
        if zone_high - zone_low < minimum:
            continue
        found.append(FVG(index - 1, direction, zone_low, zone_high))
    return found


def unmitigated(bars: list[Bar] | tuple[Bar, ...], zone: FVG) -> bool:
    """D6. Dead once a later candle closes beyond the far edge."""
    start = zone.index + 2
    for bar in bars[start:]:
        if zone.direction == "bullish" and bar.close < zone.low:
            return False
        if zone.direction == "bearish" and bar.close > zone.high:
            return False
    return True


def displacement(bars: list[Bar] | tuple[Bar, ...], start: int, direction: str, atr: D | None) -> tuple[int, int] | None:
    """D5. Two or more bodies, each ≥ 60% of its range, combined range ≥ 1 × ATR, plus an FVG."""
    if atr is None or atr <= 0 or start < 0 or start >= len(bars):
        return None
    index = start
    while index < len(bars):
        bar = bars[index]
        going = bar.close > bar.open if direction == "bullish" else bar.close < bar.open
        span = bar.range
        if not going or span <= 0 or bar.body < D("0.60") * span:
            break
        index += 1
    if index - start < 2:
        return None
    block = bars[start:index]
    combined = max(bar.high for bar in block) - min(bar.low for bar in block)
    if combined < atr:
        return None
    window = bars[max(0, start - 1) : index]
    if not any(zone.direction == direction for zone in fvgs(window, atr)):
        return None
    return start, index - 1


@dataclass(frozen=True)
class OrderBlock:
    index: int
    direction: str
    low: D
    high: D

    @property
    def mid(self) -> D:
        return (self.low + self.high) / 2


def order_block(bars: list[Bar] | tuple[Bar, ...], disp_start: int, direction: str) -> OrderBlock | None:
    """D7. The last opposite-colour candle before a displacement."""
    for index in range(disp_start - 1, -1, -1):
        bar = bars[index]
        opposite = bar.bearish() if direction == "bullish" else bar.bullish()
        if opposite:
            return OrderBlock(index, direction, bar.low, bar.high)
    return None


def dealing_range(bars: list[Bar] | tuple[Bar, ...]) -> tuple[D, D, D] | None:
    """D10. H4 leg around the latest BOS. Returns (low, high, eq)."""
    events = [event for event in structure_events(bars, 2) if event.kind == "bos"]
    points = swings(bars, 2)
    if not events or not points:
        return None
    last = events[-1]
    if last.direction == "bullish":
        highs = [point for point in points if point.kind == "high" and point.index <= last.index]
        lows = [point for point in points if point.kind == "low" and point.index <= last.index]
        if not highs or not lows:
            return None
        low, high = lows[-1].price, highs[-1].price
    else:
        highs = [point for point in points if point.kind == "high" and point.index <= last.index]
        lows = [point for point in points if point.kind == "low" and point.index <= last.index]
        if not highs or not lows:
            return None
        low, high = lows[-1].price, highs[-1].price
    if high <= low:
        return None
    return low, high, (low + high) / 2


def in_discount(low: D, high: D, eq: D, side: str) -> bool:
    """D10. Longs want ≥ 50% of the zone below EQ. Shorts want the mirror."""
    span = high - low
    if span <= 0:
        return False
    if side == "long":
        below = max(D("0"), min(high, eq) - low)
        return below >= D("0.50") * span
    above = max(D("0"), high - max(low, eq))
    return above >= D("0.50") * span


@dataclass(frozen=True)
class Sweep:
    index: int
    side: str  # ssl (swept a low) or bsl
    level: D
    extreme: D


def find_sweeps(bars: list[Bar] | tuple[Bar, ...], level: D, side: str) -> list[Sweep]:
    """D9. Trade beyond the level by ≥ $0.10 and close back inside within two candles.

    Two consecutive closes beyond the level are a break (D2), not a sweep.
    """
    found: list[Sweep] = []
    index = 0
    while index < len(bars):
        bar = bars[index]
        if side == "ssl":
            pierced = bar.low <= level - D("0.10")
            back = bar.close > level
            held = bar.close <= level
        else:
            pierced = bar.high >= level + D("0.10")
            back = bar.close < level
            held = bar.close >= level
        if not pierced:
            index += 1
            continue
        nxt = bars[index + 1] if index + 1 < len(bars) else None
        if nxt is not None:
            nxt_held = nxt.close <= level if side == "ssl" else nxt.close >= level
            if held and nxt_held:
                index += 2
                continue
        if back:
            extreme = bar.low if side == "ssl" else bar.high
            found.append(Sweep(index, side, level, extreme))
            index += 1
            continue
        if nxt is not None:
            nxt_back = nxt.close > level if side == "ssl" else nxt.close < level
            if nxt_back:
                extreme = min(bar.low, nxt.low) if side == "ssl" else max(bar.high, nxt.high)
                found.append(Sweep(index, side, level, extreme))
                index += 2
                continue
        index += 1
    return found


def m15_state(bars: list[Bar] | tuple[Bar, ...], atr: D | None, m5: tuple[Bar, ...] = ()) -> str:
    """D12. directional or chop."""
    if len(bars) >= 8 and atr is not None and atr > 0:
        recent = bars[-8:]
        span = max(bar.high for bar in recent) - min(bar.low for bar in recent)
        if span < D("1.5") * atr:
            return "chop"
    events = structure_events(bars, 2)
    bos = [event for event in events if event.kind == "bos"]
    if m5:
        chochs = [event for event in structure_events(m5, 1) if event.kind == "choch"]
        last_bos_time = bars[bos[-1].index].open_time if bos else None
        recent_choch = [
            event
            for event in chochs
            if last_bos_time is None or m5[event.index].open_time > last_bos_time
        ]
        if len(recent_choch) >= 3:
            return "chop"
    if not bos:
        return "chop"
    last = bos[-1]
    points = swings(bars, 2)
    close = bars[-1].close
    if last.direction == "bullish":
        lows = [point for point in points if point.kind == "low"]
        if lows and close > lows[-1].price:
            return "bullish"
    else:
        highs = [point for point in points if point.kind == "high"]
        if highs and close < highs[-1].price:
            return "bearish"
    return "chop"


def asia_extreme(bars: tuple[Bar, ...], moment, side: str) -> D | None:
    """D8. Asia range 07:00–14:00 WIB of the current WIB date."""
    local = wib(moment)
    start = local.replace(hour=7, minute=0, second=0, microsecond=0)
    end = local.replace(hour=14, minute=0, second=0, microsecond=0)
    if local < start:
        start -= timedelta(days=1)
        end -= timedelta(days=1)
    chosen = [bar for bar in bars if start <= bar.open_time.astimezone(WIB) < end]
    if not chosen:
        return None
    return min(bar.low for bar in chosen) if side == "long" else max(bar.high for bar in chosen)


def equal_levels(points: list[Swing], kind: str, bars: tuple[Bar, ...]) -> list[D]:
    """D8. Two confirmed swings within $0.50 over the last 48 bars."""
    if not bars:
        return []
    cutoff = len(bars) - 48
    pool = [point for point in points if point.kind == kind and point.index >= max(0, cutoff)]
    levels: list[D] = []
    for left in range(len(pool)):
        for right in range(left + 1, len(pool)):
            if abs(pool[left].price - pool[right].price) <= D("0.50"):
                levels.append((pool[left].price + pool[right].price) / 2)
    return levels


def dol(bars: tuple[Bar, ...], side: str) -> D | None:
    """D13. Nearest unswept D1/H4 swing beyond price. The caller passes that timeframe."""
    if not bars:
        return None
    price = bars[-1].close
    points = [point for point in swings(bars, 2) if point.confirmed_index < len(bars)]
    if side == "long":
        highs = [point.price for point in points if point.kind == "high" and point.price > price]
        return min(highs) if highs else None
    lows = [point.price for point in points if point.kind == "low" and point.price < price]
    return max(lows) if lows else None
