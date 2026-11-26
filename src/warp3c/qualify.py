"""Confidence score and the backtest gate. Neither one sets a price."""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal

from .backtest import candles_for_backtest, run_backtest
from .bots import BotTemplate, execution_fractions
from .levels import derive_levels
from .models import MarketEvidence, Signal


def blend_confidence(model: Decimal, evidence: MarketEvidence | None) -> Decimal:
    if evidence is None or not evidence.passed:
        quality = Decimal("0")
    elif evidence.regime == "trend_up":
        quality = Decimal("1")
    else:
        quality = Decimal("0.4")
    raw = model * Decimal("0.7") + quality * Decimal("0.3")
    score = min(model, max(Decimal("0"), raw))
    return score.quantize(Decimal("0.0001"))


def _places(value: Decimal, places: str) -> str:
    """Display text only. Decision math keeps the original Decimal."""
    return format(value.quantize(Decimal(places), rounding=ROUND_HALF_UP), "f")


def _why(signal: Signal, evidence: MarketEvidence | None, levels, backtest, *, indonesian: bool) -> str:
    regime = evidence.regime.replace("_", " ") if evidence else "unknown"
    if evidence is None:
        rsi = "n/a"
        momentum = "n/a"
    else:
        rsi = _places(evidence.rsi, "0.1")
        momentum = _places(evidence.momentum, "0.01")
    action = signal.action
    confidence = format(signal.confidence, "f")
    if levels is None:
        prices = "Levels are unavailable because the market snapshot is missing."
        prices_id = "Level harga belum ada karena snapshot pasar tidak lengkap."
    else:
        prices = (
            f"Entry {levels.entry}, stop {levels.stop}, and target {levels.take} "
            "are calculated from the last traded price."
        )
        prices_id = (
            f"Entry {levels.entry}, stop {levels.stop}, dan target {levels.take} "
            "dihitung dari harga terakhir."
        )
    if indonesian:
        return (
            f"{signal.symbol} berada di regime {regime} dengan RSI {rsi} dan momentum {momentum}. "
            f"Model memilih {action} dengan keyakinan {confidence}. {signal.thesis} {prices_id}"
        )
    return (
        f"{signal.symbol} is in a {regime} regime with RSI {rsi} and momentum {momentum}. "
        f"The model says {action} with confidence {confidence}. {signal.thesis} {prices}"
    )


def qualify(
    signal: Signal,
    evidence: MarketEvidence | None,
    candles,
    cfg,
    template: BotTemplate | None = None,
) -> tuple[Signal, dict]:
    chosen = template or BotTemplate()
    stop_fraction, take_fraction = execution_fractions(cfg, chosen)
    levels = derive_levels(evidence, cfg, stop_fraction=stop_fraction, take_fraction=take_fraction)
    blended = blend_confidence(signal.confidence, evidence)
    supplied = candles if (cfg.require_backtest or candles) else None
    window = candles_for_backtest(supplied, int(getattr(cfg, "backtest_candles", 120) or 120))
    check = run_backtest(
        window,
        stop_fraction,
        take_fraction,
        min_trades=cfg.backtest_min_trades,
    )
    updated = signal
    if cfg.blend_confidence and blended != signal.confidence:
        updated = replace(updated, confidence=blended)
    if cfg.require_backtest and updated.action == "BUY" and not check.passed:
        updated = replace(updated, action="WAIT")
    card = {
        "symbol": signal.symbol,
        "model_action": signal.action,
        "action": updated.action,
        "confidence": format(updated.confidence, "f"),
        "model_confidence": format(signal.confidence, "f"),
        "blended": format(blended, "f"),
        "thesis": signal.thesis,
        "why": _why(signal, evidence, levels, check, indonesian=False),
        "why_id": _why(signal, evidence, levels, check, indonesian=True),
        "regime": None if evidence is None else evidence.regime,
        "rsi": None if evidence is None else format(evidence.rsi, "f"),
        "backtest_passed": check.passed,
        "backtest_reason": check.reason,
        "backtest_trades": check.trades,
        "backtest_expectancy": format(check.expectancy, "f"),
    }
    if levels is None:
        card.update({"entry": None, "stop": None, "take": None})
    else:
        card.update(levels.to_dict())
    return updated, card
