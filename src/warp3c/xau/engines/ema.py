"""EMA 20/50 triple-retest. K3 windows only, M15. Rulebook §5 M1–M19."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..candles import Bar, is_current
from ..gate import Setup
from ..structure import atr_last, ema_series, q_away, q_toward

D = Decimal


def ema_entry(confirm_extreme: D, direction: str) -> D:
    """M11. Buy stop at the confirmation high plus $0.20. Short is the mirror."""
    if direction == "long":
        return confirm_extreme + D("0.20")
    return confirm_extreme - D("0.20")


def ema_stop(entry: D, ema50: D, retest_extreme: D, spread: D, direction: str) -> D:
    """M13. The farther of EMA50 ∓ $2 and the retest extreme ∓ S. Never inside the retest."""
    if direction == "long":
        raw = min(ema50 - D("2.00"), retest_extreme - spread)
    else:
        raw = max(ema50 + D("2.00"), retest_extreme + spread)
    return q_away(raw, entry)


@dataclass
class _Retest:
    start: int
    end: int
    low: D
    high: D

    @property
    def candles(self) -> int:
        return self.end - self.start + 1


def evaluate_ema(
    bars: tuple[Bar, ...],
    *,
    spread: D,
    risk_pct: D,
    window: str,
    valid_until: datetime | None,
    in_window: bool,
    bias_text: str,
    swing_highs: list[D] | None = None,
    swing_lows: list[D] | None = None,
    window_start: datetime | None = None,
    ema20: list[D | None] | None = None,
    ema50: list[D | None] | None = None,
    atr: D | None = None,
) -> Setup | None:
    """Walk M15. Production computes EMA and requires 200 warm-up bars (R0.5, M2)."""
    if not in_window or len(bars) < 12:
        return None
    if ema20 is None or ema50 is None:
        if len(bars) < 200:
            return None
        closes = [bar.close for bar in bars]
        ema20 = ema_series(closes, 20)
        ema50 = ema_series(closes, 50)
    if atr is None:
        atr = atr_last(bars)
    if atr is None or atr <= 0:
        return None
    long = _walk(bars, ema20, ema50, atr, "long", window_start)
    short = _walk(bars, ema20, ema50, atr, "short", window_start)
    chosen = long or short
    if chosen is None:
        return None
    return _to_setup(
        bars, chosen, atr, spread, risk_pct, window, valid_until, bias_text,
        swing_highs or [], swing_lows or [], ema20, ema50,
    )


def _walk(bars: tuple[Bar, ...], ema20, ema50, atr: D, direction: str, window_start: datetime | None) -> dict | None:
    cross_at: int | None = None
    separated = False
    armed = False
    active: dict | None = None
    retests: list[_Retest] = []
    for index in range(1, len(bars)):
        e20, e50 = ema20[index], ema50[index]
        p20, p50 = ema20[index - 1], ema50[index - 1]
        if e20 is None or e50 is None or p20 is None or p50 is None:
            continue
        bar = bars[index]
        bullish_cross = p20 <= p50 and e20 > e50
        bearish_cross = p20 >= p50 and e20 < e50
        if direction == "long" and bearish_cross and cross_at is not None:
            cross_at = None
            active = None
            retests = []
            continue
        if direction == "short" and bullish_cross and cross_at is not None:
            cross_at = None
            active = None
            retests = []
            continue
        if direction == "long" and bullish_cross:
            cross_at = index
            separated = False
            armed = False
            active = None
            retests = []
            continue
        if direction == "short" and bearish_cross:
            cross_at = index
            separated = False
            armed = False
            active = None
            retests = []
            continue
        if cross_at is None:
            continue
        if index - cross_at > 60:
            cross_at = None
            active = None
            retests = []
            continue
        upper = max(e20, e50)
        lower = min(e20, e50)
        if not separated:
            cleared = bar.low > upper if direction == "long" else bar.high < lower
            if cleared:
                separated = True
                armed = True
            continue
        if direction == "long" and bar.close < e50:
            cross_at = None
            active = None
            retests = []
            continue
        if direction == "short" and bar.close > e50:
            cross_at = None
            active = None
            retests = []
            continue
        touched = bar.low <= e20 if direction == "long" else bar.high >= e20
        left = bar.close > e20 if direction == "long" else bar.close < e20
        if active is None:
            if not armed and (bar.low > e20 if direction == "long" else bar.high < e20):
                armed = True
            if not (armed and touched):
                continue
            active = {"start": index, "low": bar.low, "high": bar.high}
            if not left:
                continue
        else:
            active["low"] = min(active["low"], bar.low)
            active["high"] = max(active["high"], bar.high)
            if not left:
                continue
        retests.append(_Retest(active["start"], index, active["low"], active["high"]))
        active = None
        armed = bar.low > e20 if direction == "long" else bar.high < e20
        if len(retests) == 3:
            current = is_current(index, len(bars), bar.open_time, window_start)
            if not _confirmation(bar, e20, direction) or not current:
                if current:
                    return None
                cross_at = None
                separated = False
                armed = False
                active = None
                retests = []
                continue
            return {
                "direction": direction,
                "cross": cross_at,
                "confirm": index,
                "retests": retests,
                "e20": e20,
                "e50": e50,
            }
        if len(retests) > 3:
            return None
    return None


def _confirmation(bar: Bar, ema20: D, direction: str) -> bool:
    """M10. Close beyond EMA20, in the outer half of the range, and in the trade direction."""
    if bar.range <= 0:
        return False
    if direction == "long":
        upper = bar.close >= bar.low + bar.range / 2
        return bar.close > bar.open and bar.close > ema20 and upper
    lower = bar.close <= bar.high - bar.range / 2
    return bar.close < bar.open and bar.close < ema20 and lower


def _slope(series, index: int, direction: str) -> D | None:
    if index < 5 or series[index] is None or series[index - 5] is None:
        return None
    delta = series[index] - series[index - 5]
    if direction == "long" and delta <= 0:
        return delta
    if direction == "short" and delta >= 0:
        return delta
    return abs(delta)


def _to_setup(bars, found, atr, spread, risk_pct, window, valid_until, bias_text, swing_highs, swing_lows, ema20, ema50) -> Setup | None:
    direction = found["direction"]
    index = found["confirm"]
    bar = bars[index]
    e20 = found["e20"]
    e50 = found["e50"]
    if not _trend_ok(bars, ema20, ema50, atr, index, direction):
        return None
    if _exhausted(bars, ema20, found, atr, direction):
        return None
    extension = (bar.close - e20) if direction == "long" else (e20 - bar.close)
    chase_ok = extension <= atr
    entry = ema_entry(bar.high if direction == "long" else bar.low, direction)
    retest = found["retests"][2]
    extreme = retest.low if direction == "long" else retest.high
    sl = ema_stop(entry, e50, extreme, spread, direction)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if direction == "long":
        tp1 = q_toward(entry + D("1.5") * risk, entry)
        room_level = _nearest(swing_highs, entry, "above")
        room_ok = room_level is None or room_level >= entry + D("1.5") * risk
    else:
        tp1 = q_toward(entry - D("1.5") * risk, entry)
        room_level = _nearest(swing_lows, entry, "below")
        room_ok = room_level is None or room_level <= entry - D("1.5") * risk
    if not room_ok:
        return None
    clean = _clean(found["retests"][:2], bars, ema50, direction)
    gap = abs(e20 - e50) >= D("0.25") * atr
    grade = "A" if clean and gap else "B"
    return Setup(
        engine="EMA",
        model="EMA_TR",
        direction=direction,
        grade=grade,
        confidence=75 if grade == "A" else 55,
        entry_type="stop",
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=None,
        r_usd=risk,
        rr_tp1=D("1.50"),
        rr_tp2=None,
        risk_pct=risk_pct,
        reason="EMA20/50 third retest",
        bias=bias_text[:120],
        dol=None,
        window=window,
        valid_until=valid_until,
        poi_ok=True,
        liquidity_ok=True,
        trigger_ok=True,
        displacement_ok=True,
        chase_ok=chase_ok,
        sl_ok=True,
        target_ok=True,
        spread=spread,
        tags={"confirm": index, "retests": [item.candles for item in found["retests"]]},
    )


def _nearest(levels: list[D], entry: D, side: str) -> D | None:
    if side == "above":
        ahead = [level for level in levels if level > entry]
        return min(ahead) if ahead else None
    ahead = [level for level in levels if level < entry]
    return max(ahead) if ahead else None


def _clean(retests: list[_Retest], bars, ema50, direction: str) -> bool:
    """M16. Each of the first two retests lasts ≤ 4 candles and never closes through EMA50."""
    for retest in retests:
        if retest.candles > 4:
            return False
        for index in range(retest.start, retest.end + 1):
            e50 = ema50[index]
            if e50 is None:
                return False
            if direction == "long" and bars[index].close < e50:
                return False
            if direction == "short" and bars[index].close > e50:
                return False
    return True


def _trend_ok(bars, ema20, ema50, atr, index, direction: str) -> bool:
    """M3, M4, M8."""
    if index < 10:
        return False
    e20, e50 = ema20[index], ema50[index]
    if e20 is None or e50 is None or ema20[index - 10] is None or ema50[index - 10] is None:
        return False
    if direction == "long" and not (e20 > e50):
        return False
    if direction == "short" and not (e20 < e50):
        return False
    need = D("0.10") * atr
    d20 = ema20[index] - ema20[index - 10]
    d50 = ema50[index] - ema50[index - 10]
    if direction == "long" and (d20 < need or d50 < need):
        return False
    if direction == "short" and (d20 > -need or d50 > -need):
        return False
    crosses = 0
    start = max(1, index - 19)
    for cursor in range(start, index + 1):
        if None in (ema20[cursor], ema50[cursor], ema20[cursor - 1], ema50[cursor - 1]):
            continue
        prev = ema20[cursor - 1] - ema50[cursor - 1]
        now = ema20[cursor] - ema50[cursor - 0]
        if prev == 0 or now == 0 or (prev > 0) != (now > 0):
            crosses += 1
    if crosses >= 2:
        return False
    bar = bars[index]
    if direction == "long" and not (bar.close > e20 and bar.close > e50):
        return False
    if direction == "short" and not (bar.close < e20 and bar.close < e50):
        return False
    return True


def _exhausted(bars, ema20, found, atr, direction: str) -> bool:
    """M9. A close more than 3 × ATR from EMA20 since retest 2, and a flattened slope."""
    end2 = found["retests"][1].end
    confirm = found["confirm"]
    far = False
    for index in range(end2, confirm + 1):
        e20 = ema20[index]
        if e20 is None:
            continue
        distance = (bars[index].close - e20) if direction == "long" else (e20 - bars[index].close)
        if distance > D("3.0") * atr:
            far = True
    if not far:
        return False
    slope_now = _slope(ema20, confirm, direction)
    slope_then = _slope(ema20, end2, direction)
    if slope_now is None or slope_then is None or slope_then == 0:
        return False
    return slope_now < D("0.5") * slope_then
