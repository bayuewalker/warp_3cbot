"""SMC engine, killzone only. S1 and S2. S3 is never generated (rulebook §4)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..candles import Bar, is_current
from ..clock import wib
from ..gate import Setup
from ..structure import (
    FVG,
    asia_extreme,
    atr_last,
    dealing_range,
    displacement,
    dol,
    fvgs,
    in_discount,
    order_block,
    q_away,
    q_toward,
    structure_events,
    swings,
    unmitigated,
)

D = Decimal


def sl_price(extreme: D, atr_h1: D, spread: D, direction: str, entry: D) -> D:
    """S1.8. SL = sweep extreme ∓ max(0.15 × ATR_H1, 1.50) ∓ S/2. Rounded away from entry."""
    buffer = max(D("0.15") * atr_h1, D("1.50"))
    half = spread / 2
    raw = extreme - buffer - half if direction == "long" else extreme + buffer + half
    return q_away(raw, entry)


def sl_within_cap(entry: D, sl: D, atr_h1: D) -> bool:
    """S1.9. Skip when the stop is wider than 1.3 × ATR_H1. Never tighten it."""
    return abs(entry - sl) <= D("1.3") * atr_h1


def reward_ok(entry: D, sl: D, l1: D | None, direction: str) -> bool:
    """S1.10. (L1 − entry) must be at least 2R. R already includes the spread buffer."""
    if l1 is None:
        return False
    distance = (l1 - entry) if direction == "long" else (entry - l1)
    return distance >= 2 * abs(entry - sl)


def targets(entry: D, sl: D, direction: str, dol_price: D | None) -> tuple[D, D | None, D, D | None]:
    """S1.11–12. TP1 is 2R. TP2 is the DOL unless it sits inside TP1 + 0.5R."""
    risk = abs(entry - sl)
    if direction == "long":
        tp1 = q_toward(entry + 2 * risk, entry)
        tp2_raw = dol_price
        rr1 = (tp1 - entry) / risk
    else:
        tp1 = q_toward(entry - 2 * risk, entry)
        tp2_raw = dol_price
        rr1 = (entry - tp1) / risk
    tp2: D | None = None
    rr2: D | None = None
    if tp2_raw is not None:
        far = (tp2_raw - tp1) if direction == "long" else (tp1 - tp2_raw)
        if far > D("0.5") * risk:
            tp2 = q_toward(tp2_raw, entry)
            rr2 = abs(tp2 - entry) / risk
    return tp1, tp2, rr1, rr2


def grade_for(*, base: str, ote: bool, clean_sweep: bool, vacuum: bool, fresh_reversal: bool) -> str:
    """S1.13. A fresh HTF reversal is capped at A."""
    if fresh_reversal:
        return "A"
    if base == "A" and (ote or clean_sweep or vacuum):
        return "A+"
    return base


def build_setup(
    *,
    model: str,
    direction: str,
    entry_type: str,
    entry: D,
    extreme: D,
    atr_h1: D,
    spread: D,
    l1: D | None,
    dol_price: D | None,
    ote: bool,
    clean_sweep: bool,
    vacuum: bool,
    fresh_reversal: bool,
    bias: str,
    window: str,
    valid_until: datetime | None,
    reason: str,
    poi_ok: bool,
    liquidity_ok: bool,
    trigger_ok: bool,
    displacement_ok: bool,
    risk_pct: D,
    inside_poi: bool = True,
) -> Setup | None:
    """Shared S1/S2 pricing. Returns None when the stop cap or the 2R gate fails."""
    sl = sl_price(extreme, atr_h1, spread, direction, entry)
    if not sl_within_cap(entry, sl, atr_h1):
        return None
    if not reward_ok(entry, sl, l1, direction):
        return None
    risk = abs(entry - sl)
    tp1, tp2, rr1, rr2 = targets(entry, sl, direction, dol_price)
    if direction == "long" and not (sl < entry < tp1):
        return None
    if direction == "short" and not (tp1 < entry < sl):
        return None
    grade = grade_for(
        base="A", ote=ote, clean_sweep=clean_sweep and model == "S1", vacuum=vacuum, fresh_reversal=fresh_reversal
    )
    return Setup(
        engine="SMC",
        model=model,
        direction=direction,
        grade=grade,
        confidence=85 if grade == "A+" else 70,
        entry_type=entry_type,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        r_usd=risk,
        rr_tp1=rr1.quantize(D("0.01")),
        rr_tp2=None if rr2 is None else rr2.quantize(D("0.01")),
        risk_pct=risk_pct,
        reason=reason[:180],
        bias=bias[:120],
        dol=dol_price,
        window=window,
        valid_until=valid_until,
        poi_ok=poi_ok,
        liquidity_ok=liquidity_ok,
        trigger_ok=trigger_ok,
        displacement_ok=displacement_ok,
        chase_ok=entry_type == "limit" or (entry_type == "market" and inside_poi and rr1 >= D("2")),
        sl_ok=True,
        target_ok=True,
        spread=spread,
        tags={
            "ote": ote,
            "clean_sweep": clean_sweep,
            "vacuum": vacuum,
            "fresh_reversal": fresh_reversal,
            "extreme": str(extreme),
        },
    )


def _overlap(bar: Bar, low: D, high: D) -> bool:
    return bar.high >= low and bar.low <= high


def _inside(price: D, low: D, high: D) -> bool:
    return low <= price <= high


def _pick_fvg(zones: list[FVG], direction: str, poi_low: D, poi_high: D, choch_close: D) -> FVG | None:
    wanted = "bullish" if direction == "long" else "bearish"
    pool = []
    for zone in zones:
        if zone.direction != wanted:
            continue
        between = min(poi_low, choch_close) <= zone.ce <= max(poi_high, choch_close)
        inside = poi_low <= zone.ce <= poi_high
        if between or inside:
            pool.append(zone)
    if not pool:
        return None
    return min(pool, key=lambda zone: abs(zone.ce - choch_close))


def _l1(entry: D, direction: str, swing_prices: list[D], eq: D | None) -> D | None:
    """Nearest internal swing beyond entry, or H4 EQ when entry is on the far side of it."""
    if direction == "long":
        ahead = [price for price in swing_prices if price > entry]
        if eq is not None and entry < eq:
            ahead.append(eq)
        return min(ahead) if ahead else None
    ahead = [price for price in swing_prices if price < entry]
    if eq is not None and entry > eq:
        ahead.append(eq)
    return max(ahead) if ahead else None


def _vacuum(h1: tuple[Bar, ...], direction: str, tp1: D, tp2: D | None) -> bool:
    if tp2 is None:
        return False
    points = swings(h1, 2)
    if direction == "long":
        return not any(tp1 < point.price < tp2 for point in points if point.kind == "high")
    return not any(tp2 < point.price < tp1 for point in points if point.kind == "low")


def evaluate_smc(
    *,
    m15: tuple[Bar, ...],
    h1: tuple[Bar, ...],
    h4: tuple[Bar, ...],
    direction: str,
    atr_h1: D | None,
    spread: D,
    fresh_reversal: bool,
    aligned: bool,
    bias_text: str,
    window: str,
    valid_until: datetime | None,
    risk_pct: D,
    in_kz: bool,
    s1_developing: bool = False,
    window_start: datetime | None = None,
) -> Setup | None:
    """S1, else S2 when S1 is not developing. S3 is not produced."""
    if not in_kz or atr_h1 is None or atr_h1 <= 0:
        return None
    if not aligned and not fresh_reversal:
        return None
    deal = dealing_range(h4)
    eq = deal[2] if deal else None
    external = dol(h4, direction)
    if external is None:
        external = dol(h1, direction)
    s1 = _s1(
        m15=m15, h1=h1, direction=direction, atr_h1=atr_h1, spread=spread, eq=eq,
        dol_price=external, fresh_reversal=fresh_reversal, bias_text=bias_text,
        window=window, valid_until=valid_until, risk_pct=risk_pct, window_start=window_start,
    )
    if s1 is not None:
        return s1
    if s1_developing:
        return None
    return _s2(
        m15=m15, h1=h1, h4=h4, direction=direction, atr_h1=atr_h1, spread=spread, eq=eq,
        dol_price=external, fresh_reversal=fresh_reversal, bias_text=bias_text,
        window=window, valid_until=valid_until, risk_pct=risk_pct, window_start=window_start,
    )


def _pois(h1: tuple[Bar, ...], direction: str, eq: D | None, atr: D) -> list[tuple[D, D]]:
    zones: list[tuple[D, D]] = []
    for zone in fvgs(h1, atr):
        if not unmitigated(h1, zone):
            continue
        wanted = "bullish" if direction == "long" else "bearish"
        if zone.direction != wanted:
            continue
        if eq is not None and not in_discount(zone.low, zone.high, eq, direction):
            continue
        zones.append((zone.low, zone.high))
    # Order blocks that precede a displacement in the trade direction.
    for index in range(len(h1) - 2):
        found = displacement(h1, index, "bullish" if direction == "long" else "bearish", atr)
        if found is None:
            continue
        block = order_block(h1, found[0], "bullish" if direction == "long" else "bearish")
        if block is None:
            continue
        if eq is not None and not in_discount(block.low, block.high, eq, direction):
            continue
        zones.append((block.low, block.high))
    if not zones or not h1:
        return []
    price = h1[-1].close
    zones.sort(key=lambda zone: abs(((zone[0] + zone[1]) / 2) - price))
    return zones[:2]


def _s1(
    *,
    m15: tuple[Bar, ...],
    h1: tuple[Bar, ...],
    direction: str,
    atr_h1: D,
    spread: D,
    eq: D | None,
    dol_price: D | None,
    fresh_reversal: bool,
    bias_text: str,
    window: str,
    valid_until: datetime | None,
    risk_pct: D,
    window_start: datetime | None = None,
) -> Setup | None:
    if dol_price is None or len(m15) < 5:
        return None
    atr_m15 = atr_last(m15) or atr_h1
    pois = _pois(h1, direction, eq, atr_h1)
    if not pois:
        return None
    points = swings(m15, 2)
    side = "ssl" if direction == "long" else "bsl"
    # Search the latest bars of the killzone for a sweep into a POI, then displacement and CHoCH.
    for sweep_at in range(len(m15) - 1, 2, -1):
        bar = m15[sweep_at]
        poi = next((zone for zone in pois if _overlap(bar, zone[0], zone[1])), None)
        if poi is None:
            continue
        level_pool = [point.price for point in points if point.kind == ("low" if side == "ssl" else "high") and point.confirmed_index < sweep_at]
        if not level_pool:
            continue
        level = max(level_pool) if side == "ssl" else min(level_pool)
        pierced = bar.low <= level - D("0.10") if side == "ssl" else bar.high >= level + D("0.10")
        reclaimed = bar.close > level if side == "ssl" else bar.close < level
        if not pierced or not reclaimed:
            continue
        extreme = bar.low if side == "ssl" else bar.high
        disp = None
        for start in range(sweep_at, min(len(m15) - 1, sweep_at + 5)):
            disp = displacement(m15, start, "bullish" if direction == "long" else "bearish", atr_m15)
            if disp is not None and disp[0] - sweep_at <= 4:
                break
            disp = None
        if disp is None:
            continue
        opposing = [
            point for point in points
            if point.kind == ("high" if direction == "long" else "low") and point.confirmed_index < sweep_at
        ]
        if not opposing:
            continue
        trigger_level = opposing[-1].price
        choch_at = None
        for index in range(sweep_at, min(len(m15), sweep_at + 7)):
            close = m15[index].close
            broke = close > trigger_level if direction == "long" else close < trigger_level
            if broke:
                choch_at = index
                break
        if choch_at is None:
            continue
        if window_start is not None and m15[sweep_at].open_time < window_start:
            continue
        if not is_current(choch_at, len(m15), m15[choch_at].open_time, window_start):
            continue
        close = m15[choch_at].close
        zones = [zone for zone in fvgs(m15, atr_m15) if disp[0] - 1 <= zone.index <= disp[1]]
        chosen = _pick_fvg(zones, direction, poi[0], poi[1], close)
        market = _inside(close, poi[0], poi[1])
        if market:
            entry = q_toward(close, close)
            entry_type = "market"
        elif chosen is not None:
            entry = q_toward(chosen.ce, close)
            entry_type = "limit"
        else:
            continue
        swing_prices = [
            point.price for point in list(points) + swings(h1, 2)
            if point.kind == ("high" if direction == "long" else "low")
        ]
        l1 = _l1(entry, direction, swing_prices, eq)
        clean = clean_session_sweep(level, direction, m15, m15[choch_at].open_time, window)
        ote = _ote(m15, disp, entry, direction)
        setup = build_setup(
            model="S1", direction=direction, entry_type=entry_type, entry=entry, extreme=extreme,
            atr_h1=atr_h1, spread=spread, l1=l1, dol_price=dol_price,
            ote=ote,
            clean_sweep=clean,
            vacuum=False,
            fresh_reversal=fresh_reversal,
            bias=bias_text, window=window, valid_until=valid_until,
            reason="S1 Judas sweep through a killzone POI",
            poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True,
            risk_pct=risk_pct,
            inside_poi=market,
        )
        if setup is None:
            continue
        vacuum = _vacuum(h1, direction, setup.tp1, setup.tp2)
        setup.tags["vacuum"] = vacuum
        setup.tags["clean_sweep"] = clean
        setup.grade = grade_for(
            base="A", ote=ote, clean_sweep=clean, vacuum=vacuum, fresh_reversal=fresh_reversal,
        )
        setup.confidence = 85 if setup.grade == "A+" else 70
        setup.tags["poi"] = [str(poi[0]), str(poi[1])]
        return setup
    return None


def clean_session_sweep(level: D, direction: str, m15: tuple[Bar, ...], moment: datetime, window: str) -> bool:
    """S1.13. The swept level is the Asia extreme, PDL/PDH, or the London KZ extreme in NY."""
    found: list[D] = []
    asia = asia_extreme(m15, moment, direction)
    if asia is not None:
        found.append(asia)
    prior = _prior_day_extreme(m15, moment, direction)
    if prior is not None:
        found.append(prior)
    if window.startswith("NY"):
        london = _minute_extreme(m15, moment, direction, 14 * 60, 17 * 60)
        if london is not None:
            found.append(london)
    return any(abs(level - item) <= D("0.50") for item in found)


def _prior_day_extreme(bars: tuple[Bar, ...], moment: datetime, direction: str) -> D | None:
    day = wib(moment).date()
    older = [bar for bar in bars if wib(bar.open_time).date() < day]
    if not older:
        return None
    previous = max(wib(bar.open_time).date() for bar in older)
    chosen = [bar for bar in older if wib(bar.open_time).date() == previous]
    return min(bar.low for bar in chosen) if direction == "long" else max(bar.high for bar in chosen)


def _minute_extreme(bars: tuple[Bar, ...], moment: datetime, direction: str, start: int, end: int) -> D | None:
    day = wib(moment).date()
    chosen = []
    for bar in bars:
        local = wib(bar.open_time)
        minute = local.hour * 60 + local.minute
        if local.date() == day and start <= minute < end:
            chosen.append(bar)
    if not chosen:
        return None
    return min(bar.low for bar in chosen) if direction == "long" else max(bar.high for bar in chosen)


def _ote(bars: tuple[Bar, ...], disp: tuple[int, int], entry: D, direction: str) -> bool:
    leg = bars[disp[0] : disp[1] + 1]
    if not leg:
        return False
    low = min(bar.low for bar in leg)
    high = max(bar.high for bar in leg)
    span = high - low
    if span <= 0:
        return False
    if direction == "long":
        retrace = (high - entry) / span
    else:
        retrace = (entry - low) / span
    return D("0.62") <= retrace <= D("0.705")


def _s2(
    *,
    m15: tuple[Bar, ...],
    h1: tuple[Bar, ...],
    h4: tuple[Bar, ...],
    direction: str,
    atr_h1: D,
    spread: D,
    eq: D | None,
    dol_price: D | None,
    fresh_reversal: bool,
    bias_text: str,
    window: str,
    valid_until: datetime | None,
    risk_pct: D,
    window_start: datetime | None = None,
) -> Setup | None:
    """S2. Fresh H4 BOS, pullback to an H1/H4 FVG CE, retrace ≤ 80%, then an M15 micro CHoCH."""
    events = [event for event in structure_events(h4, 2) if event.kind == "bos"]
    wanted = "bullish" if direction == "long" else "bearish"
    recent = [event for event in events if event.direction == wanted and event.index >= len(h4) - 8]
    if not recent or dol_price is None:
        return None
    bos = recent[-1]
    leg_start = max(0, bos.index - 6)
    leg = h4[leg_start : bos.index + 1]
    if not leg:
        return None
    high = max(bar.high for bar in leg)
    low = min(bar.low for bar in leg)
    span = high - low
    if span <= 0:
        return None
    atr_h4 = atr_last(h4) or atr_h1
    zones = [zone for zone in fvgs(h1, atr_h1) if unmitigated(h1, zone)]
    zones += [zone for zone in fvgs(h4, atr_h4) if unmitigated(h4, zone)]
    price = m15[-1].close if m15 else None
    if price is None:
        return None
    if not is_current(len(m15) - 1, len(m15), m15[-1].open_time, window_start):
        return None
    touched = None
    for zone in zones:
        if zone.direction != wanted:
            continue
        if eq is not None and not in_discount(zone.low, zone.high, eq, direction):
            continue
        if m15[-1].low <= zone.ce <= m15[-1].high:
            touched = zone
            break
    if touched is None:
        return None
    retrace = (high - price) / span if direction == "long" else (price - low) / span
    if retrace > D("0.80"):
        return None
    reaction = m15[-1]
    body_ok = reaction.range > 0 and reaction.body >= D("0.50") * reaction.range
    with_trade = reaction.bullish() if direction == "long" else reaction.bearish()
    if not body_ok or not with_trade:
        return None
    micro = swings(m15, 1)
    opposing = [
        point for point in micro
        if point.kind == ("high" if direction == "long" else "low") and point.confirmed_index < len(m15) - 1
    ]
    if not opposing:
        return None
    level = opposing[-1].price
    broke = reaction.close > level if direction == "long" else reaction.close < level
    if not broke:
        return None
    entry = q_toward(touched.ce, reaction.close)
    extreme = min(bar.low for bar in m15[-4:]) if direction == "long" else max(bar.high for bar in m15[-4:])
    swing_prices = [point.price for point in swings(m15, 2) + swings(h1, 2) if point.kind == ("high" if direction == "long" else "low")]
    return build_setup(
        model="S2", direction=direction, entry_type="limit", entry=entry, extreme=extreme,
        atr_h1=atr_h1, spread=spread, l1=_l1(entry, direction, swing_prices, eq),
        dol_price=dol_price, ote=False, clean_sweep=False, vacuum=_vacuum(h1, direction, entry, dol_price),
        fresh_reversal=fresh_reversal, bias=bias_text, window=window, valid_until=valid_until,
        reason="S2 pullback to an H1/H4 FVG after an H4 BOS",
        poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=risk_pct,
    )


def s3_signal() -> Setup | None:
    """S3 breaker is research only and is never generated."""
    return None
