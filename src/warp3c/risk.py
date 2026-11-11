"""Pure, deterministic portfolio allocation and complete DCA reservation."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from .config import Settings
from .models import Account, PlannedOrder, Signal, positive
from .threecommas import IntegrationError


def dca_ladder_factor(total_orders: int, multiplier: Decimal, *, order_averaging=False) -> Decimal:
    """3Commas: total_orders includes base; position averaging repeats base at first extra."""
    if not 1 <= total_orders <= 30 or not Decimal("0.1") <= multiplier <= Decimal("10"):
        raise ValueError("Invalid DCA ladder")
    total = Decimal("1")
    for i in range(1, total_orders):
        total += multiplier ** (i if order_averaging else i - 1)
    return total


def managed_strategies(raw: list[dict], profile_code: str) -> list[dict]:
    result = []
    for strategy in raw:
        profiles = strategy.get("profileStrategies")
        if not isinstance(profiles, list):
            raise IntegrationError("Strategy lacks profile association")
        is_managed = any(isinstance(p, dict) and p.get("profile", {}).get("code") == profile_code for p in profiles)
        if not is_managed:
            continue
        if strategy.get("createdBy") != "open_api" or not str(strategy.get("clientId") or "").startswith("warp3c-"):
            raise IntegrationError("External strategy on dedicated account; require manual reconciliation")
        if strategy.get("exchange", {}).get("pairType") != "spot":
            raise IntegrationError("Non-spot strategy on dedicated account")
        if strategy.get("exchange", {}).get("pairSymbols", {}).get("quote") != "USDT":
            raise IntegrationError("Non-USDT active strategy")
        result.append(strategy)
    return result


def committed_quote(strategy: dict) -> Decimal:
    """Reserve maximum original ladder, not merely the cost of filled orders."""
    if strategy.get("initFixedAmountSymbol") != "quote":
        raise IntegrationError("Strategy amount not denominated in quote asset")
    base = positive(strategy.get("initFixedAmount"), "initial strategy amount", allow_zero=False)
    count = strategy.get("extraOrderCount")
    if not isinstance(count, int):
        raise IntegrationError("Strategy DCA order count missing")
    multiplier = positive(strategy.get("extraOrderVolumeMultiplier"), "DCA multiplier", allow_zero=False)
    factor = dca_ladder_factor(count, multiplier,
                               order_averaging=strategy.get("applyDcaForFirstSafetyOrder") is True)
    filled_cost = positive(strategy.get("totalEntryCost"), "filled entry cost")
    return max(base * factor, filled_cost)


class GlobalRisk:
    def __init__(self, cfg: Settings):
        self.cfg = cfg

    def plan(self, account: Account, positions: list[dict], signals: list[Signal]) -> list[PlannedOrder]:
        active = managed_strategies(positions, account.code)
        if len(active) > self.cfg.max_positions:
            raise IntegrationError("Active position count already exceeds configured limit")
        active_pairs = [str(p.get("pair", "")) for p in active]
        if any(not p.endswith("USDT") for p in active_pairs):
            raise IntegrationError("Invalid active pair")
        committed = sum((committed_quote(p) for p in active), Decimal("0"))
        invested = sum((positive(p.get("totalEntryCost"), "entry cost") for p in active), Decimal("0"))
        # Conservative capital approximation for an isolated USDT spot account:
        # quote balances plus cost basis of crypto acquired by THIS worker.
        capital = account.total_usdt + invested
        if capital <= 0:
            return []
        factor = dca_ladder_factor(self.cfg.dca_orders, self.cfg.dca_multiplier)
        proposals = []
        remaining_free = account.free_usdt
        for signal in sorted(signals, key=lambda x: x.confidence, reverse=True):
            if signal.action != "BUY" or signal.confidence < self.cfg.min_confidence:
                continue
            if signal.symbol in active_pairs or len(active_pairs) >= self.cfg.max_positions:
                continue
            commitment_budget = self.cfg.max_committed * capital - committed
            pair_budget = self.cfg.max_pair * capital
            reserve_budget = remaining_free - self.cfg.min_reserve * capital
            full_budget = min(commitment_budget, pair_budget, reserve_budget)
            if full_budget <= 0:
                break
            base = min(self.cfg.max_base, full_budget / factor).quantize(
                Decimal("0.01"), rounding=ROUND_DOWN
            )
            if base < self.cfg.min_base:
                continue
            full = base * factor
            proposals.append(PlannedOrder(signal.symbol, base, full, signal.confidence))
            committed += full
            remaining_free -= full
            active_pairs.append(signal.symbol)
        return proposals


def make_trade_payload(cfg: Settings, plan: PlannedOrder, client_id: str) -> dict:
    """Fixed spot long, market entry, quote sizing, with mandatory TP and SL."""
    return {
        "clientId": client_id,
        "exchangeCode": cfg.exchange_code,
        "pairCode": plan.symbol,
        "profilesCodes": [cfg.profile_code],
        "side": "long",
        "orderType": "market",
        "amountPerTrade": float(plan.base_amount),
        "amountPerTradeType": "quote",
        "extraOrderCount": cfg.dca_orders,
        "extraOrderDeviation": float(cfg.dca_deviation),
        "extraOrderVolumeMultiplier": float(cfg.dca_multiplier),
        "extraOrderDeviationMultiplier": float(cfg.dca_step_multiplier),
        "extraOrderCostAveraging": "quote",
        "applyDcaForFirstSafetyOrder": False,
        "takeProfits": [{"priceDeviation": float(cfg.take_profit), "portfolio": 1}],
        "takeProfitBaseOn": "average_price",
        "stopLoss": float(cfg.stop_loss),
        "stopLossBaseOn": "average_price",
        "keepPositionOpen": False,
    }
