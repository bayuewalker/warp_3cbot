"""DCA and grid payloads. Prices come from the risk plan and market levels."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .levels import TradeLevels
from .market import quantize_quote
from .models import PlannedOrder
from .risk import dca_ladder_factor, make_trade_payload


@dataclass(frozen=True)
class Preset:
    name: str
    base_scale: Decimal
    dca_orders: int
    dca_deviation: Decimal
    dca_multiplier: Decimal
    dca_step_multiplier: Decimal
    take_profit: Decimal
    stop_loss: Decimal
    grid_count: int
    grid_band: Decimal


PRESETS: dict[str, Preset] = {
    "conservative": Preset(
        "conservative",
        Decimal("0.6"),
        3,
        Decimal("0.03"),
        Decimal("1.2"),
        Decimal("1.1"),
        Decimal("0.015"),
        Decimal("0.08"),
        6,
        Decimal("0.04"),
    ),
    "moderate": Preset(
        "moderate",
        Decimal("1"),
        4,
        Decimal("0.02"),
        Decimal("1.5"),
        Decimal("1.2"),
        Decimal("0.025"),
        Decimal("0.12"),
        10,
        Decimal("0.06"),
    ),
    "aggressive": Preset(
        "aggressive",
        Decimal("1"),
        6,
        Decimal("0.015"),
        Decimal("1.7"),
        Decimal("1.25"),
        Decimal("0.04"),
        Decimal("0.18"),
        16,
        Decimal("0.10"),
    ),
}


@dataclass(frozen=True)
class BotTemplate:
    kind: str = "dca"
    product: str = "strategy"
    preset: str = "moderate"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "product": self.product, "preset": self.preset}

    @classmethod
    def from_mapping(cls, raw: dict | None) -> BotTemplate:
        if not raw:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("Bot template must be an object")
        kind = str(raw.get("kind", "dca"))
        preset = str(raw.get("preset", "moderate"))
        product = str(raw.get("product", "strategy" if kind == "dca" else "grid_bot"))
        if kind not in {"dca", "grid"} or preset not in PRESETS:
            raise ValueError("Unknown bot kind or preset")
        if kind == "grid":
            product = "grid_bot"
        elif product not in {"strategy", "dca_bot"}:
            raise ValueError("Unknown DCA product")
        return cls(kind, product, preset)


def client_id_for(symbol: str, kind: str, preset: str, day: str, attempt: int) -> str:
    material = f"{symbol}|{kind}|{preset}|{day}|{attempt}".encode()
    return "warp3c-" + hashlib.sha256(material).hexdigest()[:32]


def allocate_client_id(journal, symbol: str, template: BotTemplate, now: datetime) -> str | None:
    """Same setup and day reuse one id. A rejected attempt may take the next id."""
    day = now.astimezone(timezone.utc).date().isoformat()
    for attempt in range(6):
        client_id = client_id_for(symbol, template.kind, template.preset, day, attempt)
        state = journal.submission_state(client_id)
        if state is None:
            return client_id
        if state in {"unknown", "confirmed"}:
            return None
    return None


def _fit_base(cfg, plan: PlannedOrder, orders: int, multiplier: Decimal) -> Decimal:
    factor = dca_ladder_factor(orders, multiplier)
    gross = factor * (Decimal("1") + cfg.fee_rate + cfg.slippage_rate)
    if gross <= 0:
        raise ValueError("Invalid ladder")
    base = min(plan.base_amount, plan.full_commitment / gross, cfg.max_base)
    return quantize_quote(base, 2)


def build_strategy_dca_payload(cfg, plan: PlannedOrder, client_id: str, template: BotTemplate) -> dict:
    """Open API strategy. Moderate matches the historical payload exactly."""
    if template.preset == "moderate":
        return make_trade_payload(cfg, plan, client_id)
    preset = PRESETS[template.preset]
    orders = min(preset.dca_orders, cfg.dca_orders)
    multiplier = min(preset.dca_multiplier, cfg.dca_multiplier)
    deviation = min(max(preset.dca_deviation, Decimal("0.001")), cfg.dca_deviation)
    step = min(max(preset.dca_step_multiplier, Decimal("0.1")), cfg.dca_step_multiplier)
    take_profit = min(preset.take_profit, cfg.take_profit)
    stop_loss = min(preset.stop_loss, cfg.stop_loss)
    base = _fit_base(cfg, plan, orders, multiplier)
    if base < cfg.min_base:
        raise ValueError("Preset base is below the configured minimum")
    fitted = PlannedOrder(plan.symbol, base, base * dca_ladder_factor(orders, multiplier), plan.confidence)
    payload = make_trade_payload(cfg, fitted, client_id)
    payload["extraOrderCount"] = orders
    payload["extraOrderDeviation"] = deviation
    payload["extraOrderVolumeMultiplier"] = multiplier
    payload["extraOrderDeviationMultiplier"] = step
    payload["takeProfits"] = [{"priceDeviation": take_profit, "portfolio": Decimal("1")}]
    payload["stopLoss"] = stop_loss
    return payload


def build_dca_bot_payload(cfg, plan: PlannedOrder, client_id: str, template: BotTemplate) -> dict:
    """Cabinet DCA bot. The create response may omit the bot code; the caller must halt then."""
    preset = PRESETS[template.preset]
    orders = min(max(preset.dca_orders, 1), cfg.dca_orders)
    multiplier = min(preset.dca_multiplier, cfg.dca_multiplier)
    base = _fit_base(cfg, plan, orders, multiplier)
    if base <= 0:
        raise ValueError("DCA base rounds to zero")
    return {
        "name": f"warp3c-{client_id}",
        "exchangeCode": cfg.exchange_code,
        "pairsCodes": [plan.symbol],
        "profilesCodes": [cfg.profile_code],
        "dcaTradingType": "long",
        "amountPerTrade": base,
        "amountPerTradeType": "quote",
        "orderType": "market",
        "entrySignalCondition": "immediate",
        "extraOrderCount": orders,
        "extraOrderDeviation": min(max(preset.dca_deviation, Decimal("0.001")), cfg.dca_deviation),
        "extraOrderVolumeMultiplier": multiplier,
        "extraOrderDeviationMultiplier": min(preset.dca_step_multiplier, cfg.dca_step_multiplier),
        "extraOrderCostAveraging": "quote",
        "applyDcaForFirstSafetyOrder": False,
        "takeProfits": [
            {"priceDeviation": min(preset.take_profit, cfg.take_profit), "portfolio": Decimal("1")}
        ],
        "takeProfitBaseOn": "average_price",
        "stopLoss": min(preset.stop_loss, cfg.stop_loss),
        "stopLossBaseOn": "average_price",
        "maxOpenPositions": 1,
    }


def build_grid_payload(
    cfg, plan: PlannedOrder, levels: TradeLevels, client_id: str, template: BotTemplate
) -> tuple[dict, Decimal]:
    """Interval long grid. Stop and target are absolute USD amounts, not model prices."""
    if levels.symbol != plan.symbol:
        raise ValueError("Grid levels do not match the plan")
    preset = PRESETS[template.preset]
    band = min(preset.grid_band, cfg.stop_loss)
    upper_band = min(preset.grid_band, max(cfg.take_profit, band))
    low = levels.entry * (Decimal("1") - band)
    high = levels.entry * (Decimal("1") + upper_band)
    if not low < levels.entry < high:
        raise ValueError("Grid bounds must contain the entry")
    count = min(max(preset.grid_count, 2), 30)
    step = (high - low) / levels.entry / Decimal(count)
    if step <= 0:
        raise ValueError("Grid step is not positive")
    per = quantize_quote(plan.full_commitment / Decimal(count), 2)
    if per <= 0:
        raise ValueError("Grid size rounds to zero")
    total = per * Decimal(count)
    if total > plan.full_commitment:
        per = quantize_quote((plan.full_commitment / Decimal(count)) * Decimal("0.99"), 2)
        total = per * Decimal(count)
    if per <= 0 or total > plan.full_commitment:
        raise ValueError("Grid capital does not fit the risk plan")
    stop_usd = total * min(preset.stop_loss, cfg.stop_loss)
    take_usd = total * min(preset.take_profit, cfg.take_profit)
    payload = {
        "name": f"warp3c-{client_id}",
        "exchangeCode": cfg.exchange_code,
        "pairCode": plan.symbol,
        "profilesCodes": [cfg.profile_code],
        "gridType": "interval",
        "gridMethod": "classic",
        "gridTradingType": "long",
        "midPrice": high,
        "highPrice": high,
        "lowPrice": low,
        "gridPercentStep": step,
        "investmentBase": total,
        "amountPerTrade": per,
        "amountPerTradeType": "quote",
        "profitCurrencyType": "quote",
        "stopCondition": "stop_only",
        "startCondition": "immediate",
        "takeProfit": take_usd,
        "stopLoss": stop_usd,
    }
    return payload, total


def preview_capital(cfg, template: BotTemplate, *, equity: Decimal | None) -> dict:
    """Show how a preset would spend. This does not place an order."""
    preset = PRESETS[template.preset]
    reference = equity if equity is not None and equity > 0 else Decimal("1000")
    pair_cap = cfg.max_pair * reference
    orders = min(preset.dca_orders, cfg.dca_orders)
    multiplier = min(preset.dca_multiplier, cfg.dca_multiplier)
    base = min(cfg.max_base * preset.base_scale, pair_cap)
    if template.kind == "grid":
        count = min(preset.grid_count, 30)
        per = quantize_quote(pair_cap / Decimal(count), 2)
        full = per * Decimal(count)
        kind = "grid"
    else:
        factor = dca_ladder_factor(orders, multiplier)
        base = quantize_quote(min(base, pair_cap / factor), 2)
        full = base * factor * (Decimal("1") + cfg.fee_rate + cfg.slippage_rate)
        kind = "dca"
    share = (full / reference) if reference else Decimal("0")
    blocked = share > cfg.max_pair or full > cfg.max_committed * reference
    return {
        "kind": kind,
        "preset": template.preset,
        "product": template.product,
        "base_or_grid_quote": format(base if kind == "dca" else per, "f"),
        "full_commitment": format(full, "f"),
        "equity_reference": format(reference, "f"),
        "equity_is_live": equity is not None and equity > 0,
        "share_of_equity": format(share, "f"),
        "max_pair": format(cfg.max_pair, "f"),
        "max_committed": format(cfg.max_committed, "f"),
        "fits_caps": not blocked,
        "orders_or_grids": orders if kind == "dca" else min(preset.grid_count, 30),
        "stop": format(min(preset.stop_loss, cfg.stop_loss), "f"),
        "take_profit": format(min(preset.take_profit, cfg.take_profit), "f"),
    }
