"""Spot DCA capital, ladder, and addendum gates. Amounts come from a fresh snapshot.

E is a sleeve, not the whole exchange account. Other bots' future safety orders
are removed before WARP is sized. 1,200 USDT in the tradebook is an example only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal

from .models import positive
from .risk import dca_ladder_factor

MAJORS = {"BTCUSDT", "ETHUSDT"}


@dataclass(frozen=True)
class Policy:
    sleeve_usdt: Decimal = Decimal("0")
    sleeve_pct: Decimal = Decimal("0.10")
    allocation_ceiling: Decimal = Decimal("0.75")
    reserve_floor: Decimal = Decimal("0.25")
    per_deal_ceiling: Decimal = Decimal("0.25")
    max_deals: int = 3
    deal_stop_risk: Decimal = Decimal("0.025")
    aggregate_stop_risk: Decimal = Decimal("0.065")
    daily_loss_halt: Decimal = Decimal("0.03")
    drawdown_halt: Decimal = Decimal("0.08")
    base_share: Decimal = Decimal("0.15")
    order_cushion: Decimal = Decimal("0.02")
    step_min: Decimal = Decimal("0.02")
    step_max: Decimal = Decimal("0.05")
    atr_factor: Decimal = Decimal("0.9")
    stop_slippage: Decimal = Decimal("0.005")
    stress_shock: Decimal = Decimal("0.30")
    stress_slippage: Decimal = Decimal("0.01")
    stress_cap: Decimal = Decimal("0.08")
    corr_min: Decimal = Decimal("0.85")
    max_alts: int = 2
    min_score: int = 80
    tp_min: Decimal = Decimal("0.012")
    tp_fee_multiple: Decimal = Decimal("3")
    min_quote_volume: Decimal = Decimal("10000000")
    max_spread: Decimal = Decimal("0.0025")
    max_surge: Decimal = Decimal("0.20")
    cooldown_stop_hours: int = 24
    cooldown_tp_hours: int = 2
    streak_pause_hours: int = 12
    stop_streak: int = 2
    review_days: int = 10
    paper_min_days: int = 30
    paper_min_deals: int = 30
    fee_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0")
    virtual_drop: Decimal = Decimal("0.35")
    min_order: Decimal = Decimal("5")

    @classmethod
    def from_settings(cls, cfg) -> Policy:
        return cls(
            sleeve_usdt=cfg.sleeve_usdt,
            sleeve_pct=cfg.sleeve_pct,
            allocation_ceiling=cfg.allocation_ceiling,
            reserve_floor=cfg.min_reserve,
            per_deal_ceiling=cfg.per_deal_ceiling,
            max_deals=cfg.max_deals,
            deal_stop_risk=cfg.deal_stop_risk,
            aggregate_stop_risk=cfg.aggregate_stop_risk,
            daily_loss_halt=cfg.trade_daily_loss,
            drawdown_halt=cfg.trade_drawdown,
            stop_slippage=cfg.stop_slippage,
            stress_shock=cfg.stress_shock,
            stress_slippage=cfg.stress_slippage,
            stress_cap=cfg.stress_cap,
            corr_min=cfg.corr_min,
            max_alts=cfg.max_alts,
            min_score=cfg.min_score,
            tp_min=cfg.tp_min,
            tp_fee_multiple=cfg.tp_fee_multiple,
            min_quote_volume=cfg.trade_min_volume,
            max_spread=cfg.trade_max_spread,
            max_surge=cfg.trade_max_surge,
            cooldown_stop_hours=cfg.cooldown_stop_hours,
            cooldown_tp_hours=cfg.cooldown_tp_hours,
            streak_pause_hours=cfg.streak_pause_hours,
            stop_streak=cfg.stop_streak,
            review_days=cfg.review_days,
            paper_min_days=cfg.paper_min_days,
            paper_min_deals=cfg.paper_min_deals,
            fee_rate=cfg.fee_rate,
            slippage_rate=cfg.slippage_rate,
            virtual_drop=cfg.virtual_drop,
            min_order=cfg.min_order_usdt,
        )


@dataclass(frozen=True)
class Sleeve:
    account_usdt: Decimal
    sleeve: Decimal
    eligible: Decimal
    free_for_warp: Decimal
    non_warp_liability: Decimal


def sleeve_nav(
    *,
    account_usdt: Decimal,
    free_usdt: Decimal,
    warp_owned: Decimal,
    non_warp_spent: Decimal,
    non_warp_future_so: Decimal,
    policy: Policy,
) -> Sleeve:
    """E = min(sleeve, WARP-owned value + free USDT not reserved for other bots)."""
    parts: list[Decimal] = []
    if policy.sleeve_usdt > 0:
        parts.append(policy.sleeve_usdt)
    if policy.sleeve_pct > 0:
        parts.append(policy.sleeve_pct * account_usdt)
    if not parts:
        raise ValueError("Strategy sleeve is unset")
    non_warp = non_warp_spent + non_warp_future_so
    free_for_warp = max(Decimal("0"), free_usdt - non_warp_future_so)
    raw = warp_owned + free_for_warp
    # Spent non-WARP funds are not part of the sleeve even if they still sit in used USDT.
    raw = max(Decimal("0"), raw)
    return Sleeve(account_usdt, min(parts), min(min(parts), raw), free_for_warp, non_warp)


@dataclass(frozen=True)
class Caps:
    nav: Decimal
    reserve_floor: Decimal
    portfolio_ceiling: Decimal
    per_deal_ceiling: Decimal
    remaining_portfolio: Decimal
    remaining_cash: Decimal
    new_deal_cap: Decimal


def capital_caps(
    *,
    nav: Decimal,
    free_usdt: Decimal,
    warp_committed: Decimal,
    warp_pending_so: Decimal,
    policy: Policy,
) -> Caps:
    reserve = policy.reserve_floor * nav
    ceiling = policy.allocation_ceiling * nav
    per_deal = policy.per_deal_ceiling * nav
    remaining_portfolio = max(Decimal("0"), ceiling - warp_committed)
    remaining_cash = max(Decimal("0"), free_usdt - reserve - warp_pending_so)
    cap = min(per_deal, remaining_portfolio, remaining_cash)
    return Caps(nav, reserve, ceiling, per_deal, remaining_portfolio, remaining_cash, max(Decimal("0"), cap))


@dataclass(frozen=True)
class Ladder:
    commitment: Decimal
    base: Decimal
    safety_orders: int
    scale: Decimal
    step: Decimal
    orders: tuple[Decimal, ...]
    stop_price: Decimal
    stop_fraction: Decimal
    take_profit: Decimal
    average_entry: Decimal
    coins: Decimal
    stop_loss: Decimal

    @property
    def safety_count(self) -> int:
        return self.safety_orders


def step_pct(atr_1h_pct: Decimal | None, policy: Policy) -> Decimal:
    raw = policy.step_min if atr_1h_pct is None else policy.atr_factor * atr_1h_pct
    return min(policy.step_max, max(policy.step_min, raw))


def take_profit_floor(policy: Policy) -> Decimal:
    round_trip = (policy.fee_rate * 2) + policy.slippage_rate
    return max(policy.tp_min, policy.tp_fee_multiple * round_trip)


def build_ladder(
    *,
    price: Decimal,
    commitment: Decimal,
    safety_orders: int,
    scale: Decimal,
    step: Decimal,
    policy: Policy,
    stop_enabled: bool = True,
    min_qty: Decimal | None = None,
    step_size: Decimal | None = None,
) -> Ladder | None:
    if price <= 0 or commitment <= 0 or not 5 <= safety_orders <= 8:
        return None
    if not Decimal("1") <= scale <= Decimal("1.2"):
        return None
    # 3Commas repeats the base on the first safety order, then applies the multiplier.
    # The ladder has to be that shape or the live order is larger than the one we checked.
    capital = (Decimal("1") - policy.order_cushion) * commitment
    factor = dca_ladder_factor(safety_orders + 1, scale, order_averaging=False)
    base = capital / factor
    if base <= 0:
        return None
    orders = [base]
    for index in range(1, safety_orders + 1):
        orders.append(base * (scale ** (index - 1)))
    buys: list[Decimal] = []
    coins = base / price
    for index in range(1, safety_orders + 1):
        level = price * (Decimal("1") - step * index)
        if level <= 0:
            return None
        buys.append(level)
        coins += orders[index] / level
    all_cost = sum(orders, Decimal("0"))
    if coins <= 0:
        return None
    average = all_cost / coins
    book_stop = price * (Decimal("1") - (Decimal(safety_orders) + Decimal("0.5")) * step)
    stop_price = book_stop if stop_enabled else min(book_stop, price * (Decimal("1") - policy.virtual_drop))
    if stop_price <= 0:
        return None
    fill = stop_price * (Decimal("1") - policy.stop_slippage)
    gross = all_cost - coins * fill
    fees = policy.fee_rate * all_cost + policy.fee_rate * coins * fill
    stop_loss = max(Decimal("0"), gross) + fees
    if min_qty is not None and step_size is not None:
        if not lot_ok(base, price, min_qty, step_size):
            return None
        for index, level in enumerate(buys, start=1):
            if not lot_ok(orders[index], level, min_qty, step_size):
                return None
    tp = take_profit_floor(policy)
    return Ladder(
        commitment,
        base,
        safety_orders,
        scale,
        step,
        tuple(orders),
        stop_price,
        (price - stop_price) / price,
        tp,
        average,
        coins,
        stop_loss,
    )


def orders_clear_minimum(ladder: Ladder, minimum: Decimal) -> bool:
    return all(order >= minimum for order in ladder.orders)


def order_minimum(min_notional: Decimal, policy: Policy) -> Decimal:
    """Exchange minimum, plus the 2% cushion and one fee, and never below WARP_MIN_ORDER_USDT."""
    padded = min_notional * (Decimal("1") + policy.order_cushion) + (min_notional * policy.fee_rate)
    return max(padded, policy.min_order)


def lot_ok(quote: Decimal, price: Decimal, min_qty: Decimal, step: Decimal) -> bool:
    """Base quantity at this level, rounded down to stepSize, still clears minQty."""
    if quote <= 0 or price <= 0 or min_qty <= 0 or step <= 0:
        return False
    steps = (quote / price / step).to_integral_value(rounding=ROUND_DOWN)
    return steps * step >= min_qty


def search_ladder(
    *,
    price: Decimal,
    cap: Decimal,
    nav: Decimal,
    open_stop_risk: Decimal,
    open_commitment: Decimal,
    minimum: Decimal,
    atr_1h_pct: Decimal | None,
    policy: Policy,
    stop_enabled: bool = True,
    max_base: Decimal | None = None,
    min_qty: Decimal | None = None,
    step_size: Decimal | None = None,
    open_book: tuple[tuple[Decimal, Decimal], ...] = (),
) -> Ladder | None:
    if cap <= 0 or nav <= 0 or price <= 0:
        return None
    floor = order_minimum(minimum, policy)
    step = step_pct(atr_1h_pct, policy)
    best: Ladder | None = None
    amount = cap
    for _ in range(18):
        if amount < floor:
            break
        for count in (8, 7, 6, 5):
            for scale in (Decimal("1.2"), Decimal("1.1"), Decimal("1")):
                ladder = build_ladder(
                    price=price,
                    commitment=amount,
                    safety_orders=count,
                    scale=scale,
                    step=step,
                    policy=policy,
                    stop_enabled=stop_enabled,
                    min_qty=min_qty,
                    step_size=step_size,
                )
                if ladder is None or not orders_clear_minimum(ladder, floor):
                    continue
                if max_base is not None and ladder.base > max_base:
                    continue
                if ladder.stop_loss > policy.deal_stop_risk * nav:
                    continue
                if open_stop_risk + ladder.stop_loss > policy.aggregate_stop_risk * nav:
                    continue
                if open_book:
                    stressed = scenario_stress([*open_book, (ladder.commitment, ladder.stop_loss)], policy)
                else:
                    stressed = stress_loss([open_commitment, ladder.commitment], policy)
                if stressed > policy.stress_cap * nav:
                    continue
                if ladder.take_profit < take_profit_floor(policy):
                    continue
                if best is None or ladder.commitment > best.commitment:
                    best = ladder
        if best is not None:
            return best
        amount = (amount * Decimal("0.8")).quantize(Decimal("0.01"))
    return best


def stress_loss(commitments: list[Decimal], policy: Policy) -> Decimal:
    """Flat shock kept for callers that do not know each deal's stop. Prefer scenario_stress."""
    keep = (Decimal("1") - policy.stress_shock) * (Decimal("1") - policy.stress_slippage)
    return sum((item * (Decimal("1") - keep) for item in commitments), Decimal("0"))


def scenario_stress(
    deals: list[tuple[Decimal, Decimal]], policy: Policy
) -> Decimal:
    """Worst of 10/20/30/40% moves, all deals together.

    Each deal is (commitment, stop_loss). A shock inside the stop is the shocked
    loss plus slippage. Past the stop, the loss is the stop plus slippage on the
    commitment: the stop does not fill until price is worse than the trigger.
    Slippage is WARP_STRESS_SLIPPAGE (default 1%, the top of the 0.3–1% band).
    """
    shocks = (Decimal("0.10"), Decimal("0.20"), Decimal("0.30"), Decimal("0.40"))
    worst = Decimal("0")
    for shock in shocks:
        total = Decimal("0")
        for commitment, stop in deals:
            shocked = commitment * shock
            if shocked <= stop:
                total += shocked + commitment * policy.stress_slippage
            else:
                total += stop + commitment * policy.stress_slippage
        if total > worst:
            worst = total
    return worst


def gap_through_stop(stop_price: Decimal, fill_price: Decimal) -> Decimal:
    """Extra adverse distance when a stop fills through a gap. Never a reason to add a safety order."""
    if stop_price <= 0 or fill_price <= 0:
        raise ValueError("Stop fill prices must be positive")
    return max(Decimal("0"), stop_price - fill_price)


def stop_configured(payload: dict | None) -> bool:
    if not isinstance(payload, dict) or "stopLoss" not in payload:
        return False
    try:
        return positive(payload.get("stopLoss"), "stop", allow_zero=False) > 0
    except ValueError:
        return False


@dataclass(frozen=True)
class BtcTape:
    close_4h: Decimal
    ema200_4h: Decimal
    ema50_1h_slope: Decimal
    change_24h: Decimal
    atr_1h_pct: Decimal
    atr_median_30d: Decimal


def btc_risk_off(tape: BtcTape | None) -> bool:
    if tape is None:
        return False
    trend_break = tape.close_4h < tape.ema200_4h and tape.ema50_1h_slope < 0
    crash = tape.change_24h <= Decimal("-0.05")
    atr_spike = tape.atr_median_30d > 0 and tape.atr_1h_pct > (Decimal("2") * tape.atr_median_30d)
    return trend_break or crash or atr_spike


@dataclass(frozen=True)
class Breakdown:
    trend: int
    reversal: int
    liquidity: int
    structure: int
    event: int

    @property
    def total(self) -> int:
        return self.trend + self.reversal + self.liquidity + self.structure + self.event

    def to_dict(self) -> dict:
        return {
            "trend": self.trend,
            "reversal": self.reversal,
            "liquidity": self.liquidity,
            "structure": self.structure,
            "event": self.event,
            "total": self.total,
        }


DEFAULT_WEIGHTS = {
    "trend": 25,
    "reversal": 25,
    "liquidity": 20,
    "structure": 20,
    "event": 10,
}


def parse_min_score(value: object, fallback: int = 80) -> int:
    if value is None:
        return fallback
    if type(value) is bool:
        raise ValueError("Score threshold must be an integer from 50 to 100")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("Score threshold must be an integer from 50 to 100") from exc
    if not 50 <= number <= 100:
        raise ValueError("Score threshold must be an integer from 50 to 100")
    return number


def parse_weights(raw: object) -> dict[str, int]:
    if raw is None:
        return dict(DEFAULT_WEIGHTS)
    if not isinstance(raw, dict) or set(raw) != set(DEFAULT_WEIGHTS):
        raise ValueError("Rubric weights must include trend, reversal, liquidity, structure, and event")
    weights: dict[str, int] = {}
    for key in DEFAULT_WEIGHTS:
        value = raw[key]
        if type(value) is bool:
            raise ValueError("Rubric weights must be whole numbers that sum to 100")
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Rubric weights must be whole numbers that sum to 100") from exc
        if number < 0:
            raise ValueError("Rubric weights must be whole numbers that sum to 100")
        weights[key] = number
    if sum(weights.values()) != 100:
        raise ValueError("Rubric weights must sum to 100")
    return weights


def _share(points: int, default: int, weight: int) -> int:
    if default <= 0 or weight <= 0:
        return 0
    return int(Decimal(points) / Decimal(default) * Decimal(weight))


def score_candidate(
    *,
    regime: str,
    rsi: Decimal,
    quote_volume: Decimal,
    spread: Decimal | None,
    change_24h: Decimal | None,
    tradable: bool,
    volatility: Decimal,
    policy: Policy,
    weights: dict[str, int] | None = None,
) -> Breakdown:
    scale = parse_weights(weights)
    trend_points = 25 if regime == "trend_up" else 8 if regime == "range" else 0
    if Decimal("40") <= rsi <= Decimal("70"):
        reversal_points = 25
    elif Decimal("30") <= rsi < Decimal("40"):
        reversal_points = 18
    else:
        reversal_points = 6
    liquid = quote_volume >= policy.min_quote_volume and (spread is None or spread <= policy.max_spread)
    liquidity_points = 20 if liquid else 4
    structure_points = 20 if tradable and volatility <= policy.step_max else 6
    pumped = change_24h is not None and change_24h > policy.max_surge
    event_points = 0 if pumped or not tradable else 10
    return Breakdown(
        _share(trend_points, 25, scale["trend"]),
        _share(reversal_points, 25, scale["reversal"]),
        _share(liquidity_points, 20, scale["liquidity"]),
        _share(structure_points, 20, scale["structure"]),
        _share(event_points, 10, scale["event"]),
    )


@dataclass(frozen=True)
class Exits:
    stop: bool = True
    take_profit: bool = True
    trailing: bool = False


def exit_warnings(exits: Exits) -> list[str]:
    warnings: list[str] = []
    if not exits.stop:
        warnings.append("no_sl")
    if not exits.take_profit and not exits.trailing:
        warnings.append("manual_exit")
    return warnings


def correlation_block(
    open_symbols: list[str],
    symbol: str,
    corr,
    policy: Policy,
) -> str | None:
    alts = [item for item in open_symbols if item not in MAJORS]
    if symbol not in MAJORS and len(alts) >= policy.max_alts:
        return "alt_cap"
    if corr is None:
        return None
    for other in open_symbols:
        value = corr(symbol, other)
        if value is None:
            return "correlation_unknown"
        if Decimal(str(value)) > policy.corr_min:
            return "correlation_cluster"
    return None


def cooldown_block(events: list[dict], symbol: str, now: datetime, policy: Policy) -> str | None:
    moment = now.astimezone(timezone.utc)
    ordered = sorted(events, key=lambda row: _moment(row["at"]), reverse=True)
    tail = ordered[: policy.stop_streak]
    if len(tail) >= policy.stop_streak and all(row.get("kind") == "stop" for row in tail):
        latest = max(_moment(row["at"]) for row in tail)
        if moment - latest < timedelta(hours=policy.streak_pause_hours):
            return "stop_streak"
    for row in ordered:
        if row.get("symbol") != symbol:
            continue
        age = moment - _moment(row["at"])
        if row.get("kind") == "stop" and age < timedelta(hours=policy.cooldown_stop_hours):
            return "stop_cooldown"
        if row.get("kind") == "tp" and age < timedelta(hours=policy.cooldown_tp_hours):
            return "tp_cooldown"
    return None


def needs_review(opened_at: datetime, now: datetime, in_profit: bool, policy: Policy) -> bool:
    if in_profit:
        return False
    return now.astimezone(timezone.utc) - opened_at.astimezone(timezone.utc) > timedelta(days=policy.review_days)


def go_no_go(
    *,
    paper_days: int,
    closed_deals: int,
    net_expectancy: Decimal,
    max_drawdown: Decimal,
    policy: Policy,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if paper_days < policy.paper_min_days:
        reasons.append("paper_days")
    if closed_deals < policy.paper_min_deals:
        reasons.append("closed_deals")
    if net_expectancy <= 0:
        reasons.append("net_expectancy")
    if max_drawdown >= policy.drawdown_halt:
        reasons.append("drawdown")
    return (not reasons, reasons)


def daily_loss_halt(day_start: Decimal, equity: Decimal, policy: Policy) -> bool:
    return day_start > 0 and equity < day_start * (Decimal("1") - policy.daily_loss_halt)


def drawdown_halt(peak: Decimal, equity: Decimal, policy: Policy) -> bool:
    return peak > 0 and equity < peak * (Decimal("1") - policy.drawdown_halt)


def universe_block(
    *,
    tradable: bool,
    on_binance_spot: bool,
    quote_volume: Decimal,
    spread: Decimal | None,
    change_24h: Decimal | None,
    policy: Policy,
    listed_days: int | None = None,
    require_market: bool = False,
) -> str | None:
    if not on_binance_spot:
        return "not_binance_spot"
    if not tradable:
        return "not_trading"
    if require_market:
        if listed_days is None:
            return "listing_unknown"
        if listed_days < 90:
            return "listing_young"
        if spread is None or spread > policy.max_spread:
            return "spread"
    elif spread is not None and spread > policy.max_spread:
        return "spread"
    if listed_days is not None and listed_days < 90:
        return "listing_young"
    if quote_volume < policy.min_quote_volume:
        return "thin_book"
    if change_24h is not None and change_24h > policy.max_surge:
        return "surge"
    return None


@dataclass
class Decision:
    decision: str
    reasons: list[str] = field(default_factory=list)
    nav: Decimal = Decimal("0")
    cap: Decimal = Decimal("0")
    ladder: Ladder | None = None
    score: Breakdown | None = None
    warnings: list[str] = field(default_factory=list)


def approve(
    *,
    policy: Policy,
    account_usdt: Decimal,
    free_usdt: Decimal,
    warp_owned: Decimal,
    warp_committed: Decimal,
    warp_pending_so: Decimal,
    non_warp_spent: Decimal,
    non_warp_future_so: Decimal,
    open_deals: int,
    open_stop_risk: Decimal,
    open_symbols: list[str],
    price: Decimal,
    minimum: Decimal,
    model_buy: bool,
    score: Breakdown | None,
    atr_1h_pct: Decimal | None = None,
    max_commitment: Decimal | None = None,
    max_base: Decimal | None = None,
    tape: BtcTape | None = None,
    events: list[dict] | None = None,
    symbol: str = "",
    now: datetime | None = None,
    min_score: int | None = None,
    exits: Exits | None = None,
    corr=None,
    tradable: bool = True,
    on_binance_spot: bool = True,
    quote_volume: Decimal = Decimal("0"),
    spread: Decimal | None = None,
    change_24h: Decimal | None = None,
    listed_days: int | None = None,
    require_listing: bool = False,
    stale: bool = False,
    mismatched_account: bool = False,
    day_start: Decimal | None = None,
    equity: Decimal | None = None,
    peak: Decimal | None = None,
    min_qty: Decimal | None = None,
    step_size: Decimal | None = None,
    open_book: tuple[tuple[Decimal, Decimal], ...] = (),
) -> Decision:
    """One AEGIS decision. A BUY and a score of at least 80 are both required."""
    if mismatched_account:
        return Decision("VETO", ["wrong_account"])
    if stale:
        return Decision("WAIT_DATA_INCOMPLETE", ["stale"])
    if btc_risk_off(tape):
        return Decision("VETO", ["risk_off"])
    if day_start is not None and equity is not None and daily_loss_halt(day_start, equity, policy):
        return Decision("VETO", ["daily_loss"])
    if peak is not None and equity is not None and drawdown_halt(peak, equity, policy):
        return Decision("VETO", ["drawdown"])
    blocked = universe_block(
        tradable=tradable,
        on_binance_spot=on_binance_spot,
        quote_volume=quote_volume,
        spread=spread,
        change_24h=change_24h,
        policy=policy,
        listed_days=listed_days,
        require_market=require_listing,
    )
    if blocked:
        return Decision("VETO", [blocked])
    if not model_buy:
        return Decision("VETO", ["model_not_buy"])
    chosen = exits or Exits()
    threshold = policy.min_score if min_score is None else min_score
    if score is None or score.total < threshold:
        return Decision("VETO", ["score"], score=score)
    moment = now or datetime.now(timezone.utc)
    cooling = cooldown_block(events or [], symbol, moment, policy)
    if cooling:
        return Decision("VETO", [cooling], score=score)
    if open_deals >= policy.max_deals:
        return Decision("VETO", ["max_deals"], score=score)
    if symbol in open_symbols:
        return Decision("VETO", ["duplicate_symbol"], score=score)
    clustered = correlation_block(open_symbols, symbol, corr, policy)
    if clustered == "correlation_unknown":
        return Decision("WAIT_DATA_INCOMPLETE", [clustered], score=score)
    if clustered:
        return Decision("VETO", [clustered], score=score)
    book = sleeve_nav(
        account_usdt=account_usdt,
        free_usdt=free_usdt,
        warp_owned=warp_owned,
        non_warp_spent=non_warp_spent,
        non_warp_future_so=non_warp_future_so,
        policy=policy,
    )
    caps = capital_caps(
        nav=book.eligible,
        free_usdt=book.free_for_warp,
        warp_committed=warp_committed,
        warp_pending_so=warp_pending_so,
        policy=policy,
    )
    deal_cap = caps.new_deal_cap if max_commitment is None else min(caps.new_deal_cap, max_commitment)
    if deal_cap <= 0:
        return Decision("VETO", ["no_capital"], nav=book.eligible, cap=deal_cap, score=score)
    ladder = search_ladder(
        price=price,
        cap=deal_cap,
        nav=book.eligible,
        open_stop_risk=open_stop_risk,
        open_commitment=warp_committed,
        minimum=minimum,
        atr_1h_pct=atr_1h_pct,
        policy=policy,
        stop_enabled=chosen.stop,
        max_base=max_base,
        min_qty=min_qty,
        step_size=step_size,
        open_book=open_book,
    )
    if ladder is None:
        return Decision("VETO", ["no_feasible_ladder"], nav=book.eligible, cap=caps.new_deal_cap, score=score)
    if not ladder.stop_fraction > 0:
        return Decision("VETO", ["missing_stop"], nav=book.eligible, score=score)
    warnings = exit_warnings(chosen)
    if threshold < 80:
        warnings.append("score_below_80")
    return Decision("APPROVED", [], book.eligible, caps.new_deal_cap, ladder, score, warnings)


def apply_ladder(payload: dict, ladder: Ladder, exits: Exits | None = None) -> dict:
    """Copy a 3Commas payload. Only exits that are switched on are sent."""
    chosen = exits or Exits()
    updated = dict(payload)
    updated["amountPerTrade"] = ladder.base
    # extraOrderCount includes the entry. 5 means the entry plus four safety orders
    # (3Commas UpsertDcaBot and EditDcaStrategy schemas; URL in docs/risk/TRADEBOOK_MAPPING.md).
    updated["extraOrderCount"] = ladder.safety_orders + 1
    updated["extraOrderDeviation"] = ladder.step
    updated["extraOrderVolumeMultiplier"] = ladder.scale
    if chosen.stop:
        # Anchor to the entry order so the broker stop is the price the model checked.
        updated["stopLoss"] = ladder.stop_fraction
        updated["stopLossBaseOn"] = "entry_order"
    else:
        updated.pop("stopLoss", None)
        updated.pop("stopLossBaseOn", None)
    if chosen.take_profit:
        updated["takeProfits"] = [{"priceDeviation": ladder.take_profit, "portfolio": Decimal("1")}]
    else:
        updated.pop("takeProfits", None)
    if chosen.trailing:
        updated["trailingStopActivation"] = ladder.take_profit
        updated["trailingStopExecute"] = max(ladder.take_profit / 2, Decimal("0.004"))
    else:
        updated.pop("trailingStopActivation", None)
        updated.pop("trailingStopExecute", None)
    return updated


def _take_profit_on(payload: dict) -> bool:
    rows = payload.get("takeProfits")
    return isinstance(rows, list) and len(rows) > 0


def outbound_matches(payload: dict, exits: Exits) -> bool:
    if exits.stop != stop_configured(payload):
        return False
    if exits.take_profit != _take_profit_on(payload):
        return False
    trailing = payload.get("trailingStopActivation")
    if exits.trailing != (trailing is not None and Decimal(str(trailing)) > 0):
        return False
    return True


def readback_ok(remote: dict, exits: Exits, ladder: Ladder | None = None) -> bool:
    """Check a 3Commas echo. An id-only body is accepted; echoed exits must match the toggles."""
    if not isinstance(remote, dict):
        return False
    echoed = [key for key in ("stopLoss", "takeProfits", "trailingStopActivation") if key in remote]
    if echoed:
        if "stopLoss" in remote and exits.stop != stop_configured(remote):
            return False
        if "takeProfits" in remote and exits.take_profit != _take_profit_on(remote):
            return False
        if "trailingStopActivation" in remote:
            trailing = remote.get("trailingStopActivation")
            enabled = trailing is not None and Decimal(str(trailing)) > 0
            if exits.trailing != enabled:
                return False
    if ladder is not None:
        # Echoed ladder fields must match the ladder that was risk-checked. Absent fields stay id-only.
        expected: dict[str, Decimal | int] = {
            "amountPerTrade": ladder.base,
            "extraOrderCount": ladder.safety_orders + 1,
            "extraOrderVolumeMultiplier": ladder.scale,
            "extraOrderDeviation": ladder.step,
        }
        if exits.stop:
            expected["stopLoss"] = ladder.stop_fraction
        for key, want in expected.items():
            if key not in remote:
                continue
            if isinstance(want, int):
                if remote[key] != want:
                    return False
                continue
            try:
                if Decimal(str(remote[key])) != want:
                    return False
            except (ValueError, ArithmeticError):
                return False
    return True


RULE_FIELDS = (
    "sleeve_usdt",
    "sleeve_pct",
    "allocation_ceiling",
    "reserve_floor",
    "per_deal_ceiling",
    "max_deals",
    "deal_stop_risk",
    "aggregate_stop_risk",
    "daily_loss_halt",
    "drawdown_halt",
    "stop_slippage",
    "virtual_drop",
    "stress_shock",
    "stress_slippage",
    "stress_cap",
    "corr_min",
    "max_alts",
    "min_score",
    "tp_min",
    "tp_fee_multiple",
    "min_quote_volume",
    "max_spread",
    "max_surge",
    "cooldown_stop_hours",
    "cooldown_tp_hours",
    "streak_pause_hours",
    "stop_streak",
    "review_days",
    "paper_min_days",
    "paper_min_deals",
)


def rules_document(policy: Policy) -> dict:
    values = {}
    for name in RULE_FIELDS:
        value = getattr(policy, name)
        values[name] = value if isinstance(value, int) and not isinstance(value, bool) else format(value, "f")
    return {"values": values, "places_orders": False}


def apply_rule_edits(policy: Policy, raw: dict) -> Policy:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Rules must be an object")
    unknown = set(raw) - set(RULE_FIELDS)
    if unknown:
        raise ValueError("Unknown rule")
    updates: dict = {}
    for name, value in raw.items():
        current = getattr(policy, name)
        if isinstance(current, int) and not isinstance(current, bool):
            if type(value) is bool:
                raise ValueError(name)
            number = int(value)
            if name == "min_score" and not 50 <= number <= 100:
                raise ValueError(name)
            if name == "max_deals" and not 1 <= number <= 3:
                raise ValueError(name)
            if number < 0:
                raise ValueError(name)
            updates[name] = number
        else:
            amount = positive(value, name, allow_zero=name == "sleeve_usdt")
            if name == "sleeve_usdt":
                if amount < 0:
                    raise ValueError(name)
            elif name in {"min_quote_volume", "tp_fee_multiple"}:
                if amount <= 0 or (name == "tp_fee_multiple" and amount < 1):
                    raise ValueError(name)
            elif not Decimal("0") < amount <= Decimal("1"):
                raise ValueError(name)
            updates[name] = amount
    from dataclasses import replace

    updated = replace(policy, **updates)
    if updated.sleeve_usdt == 0 and updated.sleeve_pct == 0:
        raise ValueError("sleeve")
    return updated


def net_move(move: Decimal, policy: Policy) -> Decimal:
    return move - ((policy.fee_rate * 2) + policy.slippage_rate)


def _moment(value: object) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
