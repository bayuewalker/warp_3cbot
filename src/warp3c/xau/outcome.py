"""Fill and outcome model. F1–F6, SR2–SR4, M18, N3, N4. Idempotent per processed bar."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .candles import Bar

D = Decimal
TERMINAL = {"EXPIRED", "INVALIDATED", "CANCELLED_NEWS", "TP2", "SL", "BE", "TIME", "RUNNER", "RUNNER_EMA50", "NEWS_CUT"}


@dataclass
class Position:
    direction: str
    engine: str
    entry: D
    sl: D
    tp1: D
    tp2: D | None
    spread: D
    entry_type: str = "limit"
    status: str = "PENDING"
    remaining: D = D("1")
    realized_r: D = D("0")
    mae_r: D = D("0")
    mfe_r: D = D("0")
    fill_price: D | None = None
    filled_at: datetime | None = None
    tp1_done: bool = False
    trail: bool = False
    events: list[str] = field(default_factory=list)
    poi_low: D | None = None
    poi_high: D | None = None
    valid_until: datetime | None = None
    risk: D | None = None

    def __post_init__(self) -> None:
        # 1R is the stop distance at the signal. Later SL moves must not rewrite it.
        if self.risk is None:
            self.risk = abs(self.entry - self.sl) or D("0.01")

    @property
    def r(self) -> D:
        return self.risk or D("0.01")

    def signed(self, price: D) -> D:
        move = (price - self.fill_price) if self.direction == "long" else (self.fill_price - price)
        if self.fill_price is None:
            return D("0")
        return move / self.r


def on_bar(
    pos: Position,
    bar: Bar,
    *,
    m15_close: Bar | None = None,
    swing_extreme: D | None = None,
    ema50: D | None = None,
    ema_recross: bool = False,
    news_cut: bool = False,
    news_cancel: bool = False,
    window_ended: bool = False,
    lost_delivery: bool = False,
) -> Position:
    """Advance one closed bar. Same-bar ambiguity takes the worst path (F3)."""
    if pos.status in TERMINAL:
        return pos
    if pos.status == "PENDING":
        if news_cancel:
            return _close(pos, "CANCELLED_NEWS", bar, bar.close)
        if window_ended:
            return _close(pos, "EXPIRED", bar, bar.close)
        if _missed(pos, bar):
            return _close(pos, "INVALIDATED", bar, bar.close)
        if pos.poi_low is not None and pos.poi_high is not None and m15_close is not None:
            if pos.direction == "long" and m15_close.close < pos.poi_low:
                return _close(pos, "INVALIDATED", m15_close, m15_close.close)
            if pos.direction == "short" and m15_close.close > pos.poi_high:
                return _close(pos, "INVALIDATED", m15_close, m15_close.close)
        filled = _try_fill(pos, bar)
        if filled is None:
            return pos
        pos = filled
        if _sl_hit(pos, bar):
            return _finish(pos, "SL", bar, _sl_price(pos, bar))
    if pos.status in TERMINAL:
        return pos
    _mark_excursions(pos, bar)
    if news_cut and not pos.tp1_done:
        if pos.engine == "SMC":
            pos = _partial(pos, D("0.5"), bar.close, "NEWS_CUT")
            if pos.remaining <= 0:
                return _finish(pos, "NEWS_CUT", bar, bar.close)
        else:
            return _finish(pos, "NEWS_CUT", bar, bar.close)
    if _sl_hit(pos, bar) and _tp_hit(pos, bar, pos.tp1):
        return _finish(pos, "SL", bar, _sl_price(pos, bar))
    if _sl_hit(pos, bar):
        return _finish(pos, "SL", bar, _sl_price(pos, bar))
    if not pos.tp1_done and _tp_hit(pos, bar, pos.tp1):
        pos = _partial(pos, D("0.5") if pos.engine != "SCALP" else D("0.7"), pos.tp1, "TP1")
        pos.tp1_done = True
        pos.status = "TP1"
        if pos.engine == "SMC":
            pos.sl = pos.entry + D("1") if pos.direction == "long" else pos.entry - D("1")
        pos.events.append("TP1")
    if pos.tp2 is not None and pos.tp1_done and _tp_hit(pos, bar, pos.tp2):
        return _finish(pos, "TP2", bar, pos.tp2)
    if m15_close is not None and pos.status not in TERMINAL:
        pos = _manage(pos, m15_close, swing_extreme, ema50, ema_recross, lost_delivery)
    return pos


def _try_fill(pos: Position, bar: Bar) -> Position | None:
    if pos.entry_type == "market":
        return _filled(pos, bar, pos.entry)
    half = pos.spread / 2
    if pos.direction == "long":
        limit = bar.low <= pos.entry - half
        stop = bar.high >= pos.entry - half
        if pos.entry and _is_stop(pos) and stop:
            worse = bar.open >= pos.entry - half
            price = bar.open if worse and bar.open > pos.entry else pos.entry
            return _filled(pos, bar, price)
        if not _is_stop(pos) and limit:
            return _filled(pos, bar, pos.entry)
        return None
    limit = bar.high >= pos.entry + half
    stop = bar.low <= pos.entry + half
    if _is_stop(pos) and stop:
        worse = bar.open <= pos.entry + half
        price = bar.open if worse and bar.open < pos.entry else pos.entry
        return _filled(pos, bar, price)
    if not _is_stop(pos) and limit:
        return _filled(pos, bar, pos.entry)
    return None


def _is_stop(pos: Position) -> bool:
    return pos.entry_type == "stop"


def _filled(pos: Position, bar: Bar, price: D) -> Position:
    pos.status = "FILLED"
    pos.fill_price = price
    pos.filled_at = bar.open_time
    pos.events.append("FILLED")
    return pos


def _sl_hit(pos: Position, bar: Bar) -> bool:
    if pos.fill_price is None:
        return False
    half = pos.spread / 2
    if pos.direction == "long":
        return bar.low <= pos.sl + half
    return bar.high >= pos.sl - half


def _sl_price(pos: Position, bar: Bar) -> D:
    half = pos.spread / 2
    if pos.direction == "long" and bar.open <= pos.sl + half:
        return min(pos.sl, bar.open)
    if pos.direction == "short" and bar.open >= pos.sl - half:
        return max(pos.sl, bar.open)
    return pos.sl


def _tp_hit(pos: Position, bar: Bar, target: D | None) -> bool:
    if target is None or pos.fill_price is None:
        return False
    half = pos.spread / 2
    if pos.direction == "long":
        return bar.high >= target + half
    return bar.low <= target - half


def _missed(pos: Position, bar: Bar) -> bool:
    """Price reached TP1 before a limit filled."""
    if _is_stop(pos):
        return False
    half = pos.spread / 2
    reached = bar.high >= pos.tp1 + half if pos.direction == "long" else bar.low <= pos.tp1 - half
    filled = bar.low <= pos.entry - half if pos.direction == "long" else bar.high >= pos.entry + half
    return reached and not filled


def _mark_excursions(pos: Position, bar: Bar) -> None:
    if pos.fill_price is None:
        return
    if pos.direction == "long":
        pos.mfe_r = max(pos.mfe_r, (bar.high - pos.fill_price) / pos.r)
        pos.mae_r = min(pos.mae_r, (bar.low - pos.fill_price) / pos.r)
    else:
        pos.mfe_r = max(pos.mfe_r, (pos.fill_price - bar.low) / pos.r)
        pos.mae_r = min(pos.mae_r, (pos.fill_price - bar.high) / pos.r)


def _partial(pos: Position, fraction: D, price: D, _kind: str) -> Position:
    take = min(pos.remaining, fraction)
    pos.realized_r += take * pos.signed(price)
    pos.remaining -= take
    return pos


def _finish(pos: Position, status: str, bar: Bar, price: D) -> Position:
    if status == "SL" and pos.tp1_done and (
        (pos.direction == "long" and pos.sl >= pos.entry) or (pos.direction == "short" and pos.sl <= pos.entry)
    ):
        status = "BE"
    if pos.remaining > 0 and pos.fill_price is not None:
        pos.realized_r += pos.remaining * pos.signed(price)
    pos.remaining = D("0")
    pos.status = status
    pos.events.append(status)
    pos.exit_at = bar.open_time
    pos.exit_price = price
    return pos


def _close(pos: Position, status: str, bar: Bar, price: D) -> Position:
    pos.status = status
    pos.events.append(status)
    pos.exit_at = bar.open_time
    pos.exit_price = price
    pos.remaining = D("0")
    return pos


def _manage(pos: Position, bar: Bar, swing: D | None, ema50: D | None, recross: bool, lost_delivery: bool) -> Position:
    if pos.fill_price is None:
        return pos
    if pos.engine == "EMA" and recross and not pos.tp1_done:
        return _finish(pos, "SL", bar, bar.close)
    if pos.engine == "EMA" and pos.tp1_done and ema50 is not None:
        crossed = bar.close < ema50 if pos.direction == "long" else bar.close > ema50
        if crossed:
            exit_px = bar.close - pos.spread / 2 if pos.direction == "long" else bar.close + pos.spread / 2
            return _finish(pos, "RUNNER_EMA50", bar, exit_px)
        if swing is not None:
            beyond = swing > pos.tp1 if pos.direction == "long" else swing < pos.tp1
            if beyond:
                pos.sl = pos.entry
    if pos.engine == "SMC" and pos.tp1_done:
        unrealized = pos.signed(bar.close)
        if unrealized >= D("2.5") and swing is not None:
            trailed = swing - pos.spread / 2 if pos.direction == "long" else swing + pos.spread / 2
            better = trailed > pos.sl if pos.direction == "long" else trailed < pos.sl
            if better:
                pos.sl = trailed
                pos.trail = True
                pos.status = "TRAIL"
        if (
            lost_delivery
            and pos.filled_at is not None
            and bar.open_time - pos.filled_at >= timedelta(hours=3)
            and pos.signed(bar.close) < D("0.7")
        ):
            return _finish(pos, "TIME", bar, bar.close)
    if pos.engine == "SCALP" and pos.filled_at is not None:
        elapsed = bar.open_time - pos.filled_at
        if elapsed >= timedelta(minutes=12):
            return _finish(pos, "TIME", bar, bar.close)
        if elapsed >= timedelta(minutes=8) and pos.signed(bar.close) < D("0.6"):
            return _finish(pos, "TIME", bar, bar.close)
    return pos


def position_from_row(row: dict) -> Position:
    tags = {}
    try:
        tags = json.loads(row.get("tags_json") or "{}")
    except json.JSONDecodeError:
        tags = {}
    pos = Position(
        direction=str(row["direction"]),
        engine=str(row["engine"]),
        entry=D(str(row["entry"])),
        sl=D(str(row["sl"])),
        tp1=D(str(row["tp1"])),
        tp2=None if not row.get("tp2") else D(str(row["tp2"])),
        spread=D(str(row.get("spread_assumed") or "0.30")),
        status=str(row.get("status") or "PENDING"),
        remaining=D(str(tags.get("remaining", "1"))),
        realized_r=D(str(row.get("realized_r") or "0")),
        risk=None if tags.get("risk") in (None, "") else D(str(tags["risk"])),
        mae_r=D(str(row.get("mae_r") or "0")),
        mfe_r=D(str(row.get("mfe_r") or "0")),
        fill_price=None if not row.get("fill_price") else D(str(row["fill_price"])),
        tp1_done=bool(tags.get("tp1_done")),
        entry_type=str(row.get("entry_type") or "limit"),
    )
    if row.get("filled_at"):
        pos.filled_at = _stamp(row["filled_at"])
    if row.get("valid_until"):
        pos.valid_until = _stamp(row["valid_until"])
    poi = tags.get("poi")
    if isinstance(poi, list) and len(poi) == 2:
        pos.poi_low, pos.poi_high = D(str(poi[0])), D(str(poi[1]))
    return pos


def _stamp(value) -> datetime:
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def dump_tags(pos: Position, previous: dict) -> str:
    previous = dict(previous)
    previous["remaining"] = str(pos.remaining)
    previous["tp1_done"] = pos.tp1_done
    previous["risk"] = str(pos.r)
    return json.dumps(previous, sort_keys=True)
