"""Scalp engine. Computed and journaled. Never sent in P0 (rulebook §6)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..candles import Bar
from ..gate import Setup
from ..structure import atr_last, fvgs, q_toward, swings

D = Decimal


def k1_stop(extreme: D, spread: D, direction: str) -> D | None:
    """K1. Sweep extreme ± $0.80, then the distance must sit in [$1.20 + S, $3.50]."""
    raw = extreme - D("0.80") if direction == "long" else extreme + D("0.80")
    # Distance is measured after the caller knows the entry. This is the raw stop.
    return raw


def k1_distance_ok(entry: D, sl: D, spread: D) -> bool:
    distance = abs(entry - sl)
    return D("1.20") + spread <= distance <= D("3.50")


def evaluate_scalp(
    *,
    m5: tuple[Bar, ...],
    m1: tuple[Bar, ...],
    direction: str,
    spread: D,
    risk_pct: D,
    window: str,
    valid_until: datetime | None,
    bias_text: str,
    or_high: D | None,
    or_low: D | None,
    atr_m5: D | None = None,
    attempts: int = 0,
    k1_forming: bool = False,
) -> Setup | None:
    """K1 first, else K2. The gate forces sent=false."""
    if or_high is None or or_low is None or not m5 or not m1:
        return None
    if atr_m5 is None:
        atr_m5 = atr_last(m5, 14)
    k1 = _k1(m5, m1, direction, spread, risk_pct, window, valid_until, bias_text, or_high, or_low)
    if k1 is not None:
        return k1
    if k1_forming or attempts >= 2:
        return None
    return _k2(m5, m1, direction, spread, risk_pct, window, valid_until, bias_text, or_high, or_low, atr_m5)


def _setup(model, direction, entry, sl, tp1, tp2, risk_pct, window, valid_until, bias, reason, spread) -> Setup | None:
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if direction == "long" and not (sl < entry):
        return None
    if direction == "short" and not (entry < sl):
        return None
    rr1 = abs(tp1 - entry) / risk
    if rr1 < D("1.2"):
        return None
    rr2 = None if tp2 is None else abs(tp2 - entry) / risk
    return Setup(
        engine="SCALP",
        model=model,
        direction=direction,
        grade="A" if rr1 >= D("1.5") else "B",
        confidence=60,
        entry_type="market",
        entry=entry,
        sl=sl,
        tp1=q_toward(tp1, entry),
        tp2=None if tp2 is None else q_toward(tp2, entry),
        r_usd=risk,
        rr_tp1=rr1.quantize(D("0.01")),
        rr_tp2=None if rr2 is None else rr2.quantize(D("0.01")),
        risk_pct=risk_pct,
        reason=reason,
        bias=bias[:120],
        dol=None,
        window=window,
        valid_until=valid_until,
        poi_ok=True,
        liquidity_ok=True,
        trigger_ok=True,
        displacement_ok=True,
        chase_ok=True,
        sl_ok=True,
        target_ok=rr1 >= D("1.2"),
        spread=spread,
    )


def _k1(m5, m1, direction, spread, risk_pct, window, valid_until, bias, or_high, or_low) -> Setup | None:
    level = or_low if direction == "long" else or_high
    side_low = direction == "long"
    sweep_at = None
    for index in range(len(m5) - 1, max(-1, len(m5) - 8), -1):
        bar = m5[index]
        pierced = bar.low <= level - D("0.10") if side_low else bar.high >= level + D("0.10")
        back = bar.close > level if side_low else bar.close < level
        if pierced and back:
            sweep_at = index
            break
    if sweep_at is None:
        return None
    extreme = m5[sweep_at].low if side_low else m5[sweep_at].high
    # M1 body CHoCH or M1 displacement inside 3 M1 bars after the sweep minute.
    after = m5[sweep_at].open_time
    m1_after = [bar for bar in m1 if bar.open_time >= after][:3]
    if len(m1_after) < 1:
        return None
    points = swings(m1, 1)
    confirmed = [point for point in points if point.confirmed_index < len(m1)]
    trigger = None
    if direction == "long":
        highs = [point.price for point in confirmed if point.kind == "high"]
        if highs and m1_after[-1].close > highs[-1]:
            trigger = m1_after[-1]
    else:
        lows = [point.price for point in confirmed if point.kind == "low"]
        if lows and m1_after[-1].close < lows[-1]:
            trigger = m1_after[-1]
    if trigger is None:
        return None
    entry = trigger.close
    sl = k1_stop(extreme, spread, direction)
    if sl is None or not k1_distance_ok(entry, sl, spread):
        return None
    risk = abs(entry - sl)
    mid = (or_high + or_low) / 2
    if direction == "long":
        tp_r = entry + D("1.2") * risk
        tp1 = min(tp_r, mid) if mid > entry else tp_r
        tp2 = max(or_high, entry + 2 * risk)
    else:
        tp_r = entry - D("1.2") * risk
        tp1 = max(tp_r, mid) if mid < entry else tp_r
        tp2 = min(or_low, entry - 2 * risk)
    return _setup("K1", direction, entry, sl, tp1, tp2, risk_pct, window, valid_until, bias, "K1 OR15 sweep", spread)


def _k2(m5, m1, direction, spread, risk_pct, window, valid_until, bias, or_high, or_low, atr_m5) -> Setup | None:
    if atr_m5 is None:
        return None
    broken = m5[-1].close > or_high if direction == "long" else m5[-1].close < or_low
    if not broken:
        return None
    recent = m5[-5:]
    if direction == "long" and m5[-1].high >= max(bar.high for bar in recent):
        return None
    if direction == "short" and m5[-1].low <= min(bar.low for bar in recent):
        return None
    zones = fvgs(m5, atr_m5)
    tapped = [zone for zone in zones if m5[-1].low <= zone.ce <= m5[-1].high]
    if not tapped:
        return None
    micro = swings(m1, 1)
    last = micro[-1] if micro else None
    if last is None:
        return None
    close = m1[-1].close
    broke = (last.kind == "high" and close > last.price) if direction == "long" else (last.kind == "low" and close < last.price)
    if not broke:
        return None
    entry = close
    sl = (tapped[-1].low - D("0.80")) if direction == "long" else (tapped[-1].high + D("0.80"))
    if not k1_distance_ok(entry, sl, spread):
        return None
    risk = abs(entry - sl)
    mid = (or_high + or_low) / 2
    if direction == "long":
        tp1 = min(entry + D("1.2") * risk, mid if mid > entry else entry + D("1.2") * risk)
        tp2 = or_high
    else:
        tp1 = max(entry - D("1.2") * risk, mid if mid < entry else entry - D("1.2") * risk)
        tp2 = or_low
    return _setup("K2", direction, entry, sl, tp1, tp2, risk_pct, window, valid_until, bias, "K2 continuation tap", spread)
