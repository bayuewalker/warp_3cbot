"""Bounded execution parameters. A model thesis cannot change these numbers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .config import Settings


@dataclass(frozen=True)
class RegimeProfile:
    name: str
    entries_allowed: bool
    dca_orders: int
    dca_deviation: Decimal
    dca_multiplier: Decimal
    dca_step_multiplier: Decimal
    take_profit: Decimal
    stop_loss: Decimal
    size_scale: Decimal


def profile_for(regime: str, cfg: Settings) -> RegimeProfile:
    """Trend entries use the configured bounds. Other regimes do not open risk."""
    if regime == "trend_up":
        return RegimeProfile(
            "trend_up",
            True,
            cfg.dca_orders,
            cfg.dca_deviation,
            cfg.dca_multiplier,
            cfg.dca_step_multiplier,
            cfg.take_profit,
            cfg.stop_loss,
            Decimal("1"),
        )
    orders = max(1, min(cfg.dca_orders, 2))
    return RegimeProfile(
        regime or "unknown",
        False,
        orders,
        cfg.dca_deviation,
        cfg.dca_multiplier,
        cfg.dca_step_multiplier,
        min(cfg.take_profit, Decimal("0.02")),
        cfg.stop_loss,
        Decimal("0"),
    )
