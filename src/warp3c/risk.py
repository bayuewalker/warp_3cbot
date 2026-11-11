"""Deterministic portfolio allocation. The model never sizes an order."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .config import Settings
from .market import quantize_quote
from .models import (
    Account,
    EquityMark,
    MarketEvidence,
    PlannedOrder,
    RiskDecision,
    Signal,
    positive,
)
from .regime import profile_for
from .threecommas import IntegrationError

ACTIVE = {"new", "entered", "cancelling", "panic_exiting"}
TERMINAL = {"completed", "canceled", "panic_exited", "unlinked", "failed"}


def dca_ladder_factor(total_orders: int, multiplier: Decimal, *, order_averaging: bool = False) -> Decimal:
    """total_orders includes the entry. Position averaging repeats the base size once."""
    if not 1 <= total_orders <= 30 or not Decimal("0.1") <= multiplier <= Decimal("10"):
        raise ValueError("Invalid DCA ladder")
    total = Decimal("1")
    for i in range(1, total_orders):
        total += multiplier ** (i if order_averaging else i - 1)
    return total


def _exchange(strategy: dict) -> dict:
    exchange = strategy.get("exchange")
    if not isinstance(exchange, dict):
        raise IntegrationError("Strategy exchange block missing")
    return exchange


def pair_type(strategy: dict) -> str | None:
    if strategy.get("pairType"):
        return str(strategy["pairType"])
    return _exchange(strategy).get("pairType")


def pair_symbols(strategy: dict) -> dict:
    symbols = strategy.get("pairSymbols")
    if not isinstance(symbols, dict):
        nested = _exchange(strategy).get("pairSymbols")
        symbols = nested if isinstance(nested, dict) else {}
    return symbols


def net_base_qty(strategy: dict) -> Decimal:
    if strategy.get("totalEntryAmount") is None or strategy.get("totalExitAmount") is None:
        raise IntegrationError("Strategy base quantity missing")
    entry = positive(strategy["totalEntryAmount"], "entry amount")
    exited = positive(strategy["totalExitAmount"], "exit amount")
    if exited > entry:
        raise IntegrationError("Exit amount exceeds entry amount")
    return entry - exited


def managed_strategies(
    raw: list[dict], profile_code: str, *, demo: bool | None = None, exchange_code: str | None = None
) -> list[dict]:
    result = []
    for strategy in raw:
        profiles = strategy.get("profileStrategies")
        if not isinstance(profiles, list):
            raise IntegrationError("Strategy lacks profile association")
        owned = [p for p in profiles if isinstance(p, dict) and p.get("profile", {}).get("code") == profile_code]
        if not owned:
            continue
        if strategy.get("createdBy") != "open_api" or not str(strategy.get("clientId") or "").startswith("warp3c-"):
            raise IntegrationError("External strategy on dedicated account; require manual reconciliation")
        code = _exchange(strategy).get("code")
        if code is None or (exchange_code is not None and code != exchange_code):
            raise IntegrationError("Mixed or missing exchange profile: cannot size safely")
        if pair_type(strategy) != "spot":
            raise IntegrationError("Non-spot strategy on dedicated account")
        if pair_symbols(strategy).get("quote") != "USDT":
            raise IntegrationError("Non-USDT active strategy")
        if any(p.get("errorTransKey") for p in owned):
            raise IntegrationError("Profile strategy reports an exchange error")
        status = strategy.get("status")
        if demo is not None:
            if not isinstance(strategy.get("paperTrading"), bool) or strategy["paperTrading"] is not demo:
                raise IntegrationError("Strategy demo flag does not match the API profile")
        if status in ACTIVE:
            result.append(strategy)
            continue
        if status in TERMINAL:
            if net_base_qty(strategy) > 0:
                raise IntegrationError("Terminal strategy still holds base asset")
            continue
        raise IntegrationError("Strategy status missing or unknown")
    return result


def committed_quote(strategy: dict) -> Decimal:
    """Reserve the original ladder, not only the filled cost."""
    if strategy.get("initFixedAmountSymbol") != "quote":
        raise IntegrationError("Strategy amount not denominated in quote asset")
    base = positive(strategy.get("initFixedAmount"), "initial strategy amount", allow_zero=False)
    count = strategy.get("extraOrderCount")
    if not isinstance(count, int):
        raise IntegrationError("Strategy DCA order count missing")
    multiplier = positive(strategy.get("extraOrderVolumeMultiplier"), "DCA multiplier", allow_zero=False)
    factor = dca_ladder_factor(
        count,
        multiplier,
        order_averaging=strategy.get("applyDcaForFirstSafetyOrder") is True,
    )
    filled_cost = positive(strategy.get("totalEntryCost"), "filled entry cost")
    return max(base * factor, filled_cost)


def reserved_quote(strategy: dict, fee_rate: Decimal) -> Decimal:
    return committed_quote(strategy) * (Decimal("1") + fee_rate)


def unfilled_quote(strategy: dict, fee_rate: Decimal) -> Decimal:
    filled = positive(strategy.get("totalEntryCost"), "filled entry cost")
    return max(Decimal("0"), reserved_quote(strategy, fee_rate) - filled)


def signal_executable(signal: Signal, cfg: Settings, now: datetime) -> bool:
    if signal.action != "BUY" or signal.confidence < cfg.min_confidence:
        return False
    evidence = signal.evidence
    if evidence is None or not evidence.passed or evidence.symbol != signal.symbol:
        return False
    if evidence.as_of.tzinfo is None or signal.as_of.tzinfo is None:
        return False
    evidence_age = (now - evidence.as_of.astimezone(timezone.utc)).total_seconds()
    if evidence_age < -60 or evidence_age > cfg.max_signal_age * 60:
        return False
    if abs((signal.as_of - evidence.as_of).total_seconds()) > cfg.max_signal_age * 60:
        return False
    return True


def assert_balances_explained(account: Account, strategies: list[dict], prices: dict[str, Decimal]) -> None:
    """Live spot balances must match visible strategy quantities. Demo ledgers do not."""
    if account.demo:
        return
    known: dict[str, Decimal] = {}
    for strategy in strategies:
        base = pair_symbols(strategy).get("base")
        if not base:
            raise IntegrationError("Strategy base asset missing")
        known[str(base)] = known.get(str(base), Decimal("0")) + net_base_qty(strategy)
    holdings = account.holdings()
    for asset, balance in holdings.items():
        if asset == "USDT" or balance.total == 0:
            continue
        price = prices.get(f"{asset}USDT")
        if price is None or price <= 0:
            raise IntegrationError("Held asset has no USDT mark")
        dust = max(Decimal("0.00000001"), Decimal("1") / price)
        quantity = known.get(asset, Decimal("0"))
        if balance.total > quantity + dust:
            raise IntegrationError("Unexplained spot balance; entries halted")
        if quantity > balance.total + dust:
            raise IntegrationError("Strategy quantity exceeds wallet balance")
    for asset, quantity in known.items():
        if quantity <= 0:
            continue
        if asset not in holdings:
            raise IntegrationError("Open strategy is missing from wallet balances")


def mark_to_market(
    account: Account, strategies: list[dict], prices: dict[str, Decimal]
) -> tuple[Decimal, bool]:
    if not account.demo:
        equity = Decimal("0")
        for asset, balance in account.holdings().items():
            if balance.total == 0:
                continue
            if asset == "USDT":
                equity += balance.total
                continue
            price = prices.get(f"{asset}USDT")
            if price is None or price <= 0:
                return Decimal("0"), False
            equity += balance.total * price
        return equity, equity > 0
    if not strategies:
        return account.total_usdt, account.total_usdt > 0
    # Paper free balance may ignore open positions. Rebuild equity from fills and public marks.
    equity = account.free_usdt
    for strategy in strategies:
        if None in (
            strategy.get("totalEntryCost"),
            strategy.get("totalExitCost"),
            strategy.get("totalEntryAmount"),
            strategy.get("totalExitAmount"),
        ):
            return Decimal("0"), False
        cost = positive(strategy["totalEntryCost"], "entry cost") - positive(strategy["totalExitCost"], "exit cost")
        base = pair_symbols(strategy).get("base")
        price = prices.get(f"{base}USDT") if base else None
        if price is None or price <= 0:
            return Decimal("0"), False
        last = strategy.get("lastPrice")
        if last is not None:
            mark = positive(last, "broker mark", allow_zero=False)
            if abs(mark - price) / price > Decimal("0.02"):
                return Decimal("0"), False
        equity += net_base_qty(strategy) * price - max(cost, Decimal("0"))
    return equity, equity > 0


def circuit_reason(
    marks: list[EquityMark], equity: Decimal | None, reliable: bool, cfg: Settings, now: datetime
) -> str | None:
    if not reliable or equity is None or equity <= 0:
        return "mtm_unreliable"
    moment = now.astimezone(timezone.utc)
    prior = [mark for mark in marks if mark.reliable and mark.marked_at <= moment]
    peak = max((mark.equity for mark in prior), default=None)
    if peak is not None and peak > 0 and equity < peak * (Decimal("1") - cfg.max_drawdown):
        return "drawdown"
    today = [mark for mark in prior if mark.marked_at.astimezone(timezone.utc).date() == moment.date()]
    if today:
        start = min(today, key=lambda mark: mark.marked_at).equity
        if start > 0 and equity < start * (Decimal("1") - cfg.max_daily_loss):
            return "daily_loss"
    return None


class GlobalRisk:
    def __init__(self, cfg: Settings):
        self.cfg = cfg

    def assess(
        self,
        account: Account,
        positions: list[dict],
        signals: list[Signal],
        *,
        prices: dict[str, Decimal],
        marks: list[EquityMark],
        now: datetime | None = None,
        evidence: dict[str, MarketEvidence] | None = None,
    ) -> RiskDecision:
        moment = now or datetime.now(timezone.utc)
        self._require_fresh(account, moment)
        active = managed_strategies(
            positions, account.code, demo=account.demo, exchange_code=self.cfg.exchange_code
        )
        if len(active) > self.cfg.max_positions:
            raise IntegrationError("Active position count already exceeds configured limit")
        assert_balances_explained(account, active, prices)
        equity, reliable = mark_to_market(account, active, prices)
        reason = circuit_reason(marks, equity, reliable, self.cfg, moment)
        if reason or not reliable or equity is None:
            return RiskDecision([], False, reason or "mtm_unreliable", equity if reliable else None, reliable)
        if _abnormal_volatility(active, evidence or {}, self.cfg.max_volatility):
            return RiskDecision([], False, "abnormal_volatility", equity, True)
        orders = self.plan(account, positions, signals, equity=equity, now=moment)
        return RiskDecision(orders, True, "ok", equity, True)

    def plan(
        self,
        account: Account,
        positions: list[dict],
        signals: list[Signal],
        *,
        equity: Decimal,
        now: datetime | None = None,
    ) -> list[PlannedOrder]:
        moment = now or datetime.now(timezone.utc)
        active = managed_strategies(
            positions, account.code, demo=account.demo, exchange_code=self.cfg.exchange_code
        )
        if equity <= 0:
            return []
        active_pairs = []
        for strategy in active:
            pair = str(strategy.get("pair") or "")
            if not pair.endswith("USDT"):
                raise IntegrationError("Invalid active pair")
            active_pairs.append(pair)
        committed = sum((reserved_quote(strategy, self.cfg.fee_rate) for strategy in active), Decimal("0"))
        locked = (
            committed
            if account.demo
            else sum((unfilled_quote(strategy, self.cfg.fee_rate) for strategy in active), Decimal("0"))
        )
        group_committed: dict[str, Decimal] = {}
        for strategy, pair in zip(active, active_pairs, strict=True):
            group = self.cfg.group_for(pair)
            group_committed[group] = group_committed.get(group, Decimal("0")) + reserved_quote(
                strategy, self.cfg.fee_rate
            )
        factor = dca_ladder_factor(self.cfg.dca_orders, self.cfg.dca_multiplier)
        gross = factor * (Decimal("1") + self.cfg.fee_rate + self.cfg.slippage_rate)
        proposals = []
        spendable = account.free_usdt - locked
        for signal in sorted(signals, key=lambda item: item.confidence, reverse=True):
            if not signal_executable(signal, self.cfg, moment):
                continue
            if signal.symbol in active_pairs or len(active_pairs) >= self.cfg.max_positions:
                continue
            group = self.cfg.group_for(signal.symbol)
            full_budget = min(
                self.cfg.max_committed * equity - committed,
                self.cfg.max_pair * equity,
                self.cfg.max_group * equity - group_committed.get(group, Decimal("0")),
                spendable - self.cfg.min_reserve * equity,
            )
            if full_budget <= 0:
                break
            evidence = signal.evidence
            assert evidence is not None
            profile = profile_for(evidence.regime, self.cfg)
            if not profile.entries_allowed or profile.size_scale <= 0:
                continue
            base = min(self.cfg.max_base * profile.size_scale, full_budget / gross)
            base = quantize_quote(base, evidence.quote_precision)
            if base < self.cfg.min_base or base < evidence.min_notional:
                continue
            full = base * gross
            proposals.append(PlannedOrder(signal.symbol, base, full, signal.confidence))
            committed += full
            spendable -= full
            group_committed[group] = group_committed.get(group, Decimal("0")) + full
            active_pairs.append(signal.symbol)
        return proposals

    def _require_fresh(self, account: Account, now: datetime) -> None:
        if account.fetched_at.tzinfo is None:
            raise IntegrationError("Account snapshot has no timezone")
        age = (now - account.fetched_at.astimezone(timezone.utc)).total_seconds()
        if age < -5 or age > self.cfg.max_account_age:
            raise IntegrationError("Account snapshot is stale")


def _abnormal_volatility(strategies: list[dict], evidence: dict[str, MarketEvidence], limit: Decimal) -> bool:
    for strategy in strategies:
        item = evidence.get(str(strategy.get("pair") or ""))
        if item is not None and item.volatility > limit:
            return True
    return False


def make_trade_payload(cfg: Settings, plan: PlannedOrder, client_id: str) -> dict:
    """Spot long, market entry, quote sizing, one take-profit and a stop."""
    profile = profile_for("trend_up", cfg)
    if not profile.entries_allowed:
        raise ValueError("Trend profile does not permit entries")
    if not 32 <= len(client_id) <= 64:
        raise ValueError("clientId length is outside the 3Commas contract")
    if len(cfg.profile_code) < 4:
        raise ValueError("API profile code is too short")
    return {
        "clientId": client_id,
        "exchangeCode": cfg.exchange_code,
        "pairCode": plan.symbol,
        "profiles": [{"code": cfg.profile_code, "multiplier": 1}],
        "side": "long",
        "orderType": "market",
        "amountPerTrade": plan.base_amount,
        "amountPerTradeType": "quote",
        "extraOrderCount": profile.dca_orders,
        "extraOrderDeviation": profile.dca_deviation,
        "extraOrderVolumeMultiplier": profile.dca_multiplier,
        "extraOrderDeviationMultiplier": profile.dca_step_multiplier,
        "extraOrderCostAveraging": "quote",
        "applyDcaForFirstSafetyOrder": False,
        "takeProfits": [{"priceDeviation": profile.take_profit, "portfolio": Decimal("1")}],
        "takeProfitBaseOn": "average_price",
        "stopLoss": profile.stop_loss,
        "stopLossBaseOn": "average_price",
        "keepPositionOpen": False,
    }
