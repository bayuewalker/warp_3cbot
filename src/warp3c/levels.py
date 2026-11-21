"""Entry, stop, and target from market evidence. Model output is not an input."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from .models import MarketEvidence


@dataclass(frozen=True)
class TradeLevels:
    symbol: str
    entry: Decimal
    stop: Decimal
    take: Decimal
    stop_fraction: Decimal
    take_fraction: Decimal

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "entry": format(self.entry, "f"),
            "stop": format(self.stop, "f"),
            "take": format(self.take, "f"),
            "stop_fraction": format(self.stop_fraction, "f"),
            "take_fraction": format(self.take_fraction, "f"),
        }


def _price(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)


def derive_levels(evidence: MarketEvidence | None, cfg) -> TradeLevels | None:
    """Long spot levels. The stop is at least 1% and at most the configured cap."""
    if evidence is None or evidence.last_price <= 0:
        return None
    volatility = evidence.volatility if evidence.volatility > 0 else Decimal("0.01")
    stop_fraction = min(cfg.stop_loss, max(volatility * Decimal("2"), Decimal("0.01")))
    take_fraction = cfg.take_profit
    if stop_fraction <= 0 or take_fraction <= 0 or stop_fraction >= 1:
        return None
    entry = evidence.last_price
    stop = _price(entry * (Decimal("1") - stop_fraction))
    take = _price(entry * (Decimal("1") + take_fraction))
    if not stop < entry < take:
        return None
    return TradeLevels(evidence.symbol, entry, stop, take, stop_fraction, take_fraction)
