"""Engine selection. E1–E4. The choice is locked once, at the start of the killzone."""

from __future__ import annotations

from decimal import Decimal

D = Decimal


def select_engine(
    *,
    d1_bias: str,
    h4_bias: str,
    fresh_reversal: bool,
    h1_poi: bool,
    dol_distance: D | None,
    atr_h1: D | None,
    m15_directional: bool,
    h1_veto: bool,
) -> tuple[str, str]:
    """E2. SMC, else SCALP, else NO_TRADE. EMA is not chosen here (E3)."""
    aligned = d1_bias == h4_bias and d1_bias in {"bullish", "bearish"}
    path = (
        atr_h1 is not None
        and atr_h1 > 0
        and dol_distance is not None
        and dol_distance >= 2 * atr_h1
    )
    if (aligned or fresh_reversal) and h1_poi and path:
        return "SMC", "aligned bias, H1 POI, DOL at least 2 ATR"
    mixed = h4_bias in {"range", "mixed"} or (d1_bias != h4_bias)
    if mixed and m15_directional and not h1_veto:
        return "SCALP", "mixed higher timeframe, M15 directional, no H1 veto"
    return "NO_TRADE", "no engine passed E2"
