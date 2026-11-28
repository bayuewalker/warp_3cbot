"""Plain-text Telegram cards. No parse mode, no balances, no order claim."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_LIMIT = 400
_CLIP = 90
_MARK = "🎯 Verdict"

_ACTION_ICON = {
    "BUY": "🟢",
    "WAIT": "🟡",
    "SKIP": "🔵",
    "BLOCKED": "🔴",
    "HALT": "🛑",
}

# English phrases already shown on the trading desk. Unknown codes are not echoed.
_BLOCK_WHY = {
    "wrong_account": "Account mismatch",
    "stale": "Evidence has expired",
    "risk_off": "Market risk filter blocked entry",
    "daily_loss": "Daily loss limit reached",
    "drawdown": "Drawdown limit reached",
    "not_binance_spot": "Outside Binance spot",
    "not_trading": "Market closed for trading",
    "listing_unknown": "Listing age is unavailable",
    "listing_young": "Listing is too recent",
    "spread": "Spread check failed",
    "thin_book": "Insufficient liquidity",
    "surge": "Price surge filter triggered",
    "model_not_buy": "Model did not select BUY",
    "score": "Rubric score is under the threshold",
    "stop_streak": "Stop-loss streak limit reached",
    "stop_cooldown": "Cooldown after stop-loss",
    "tp_cooldown": "Cooldown after take-profit",
    "max_deals": "Open-deal limit reached",
    "duplicate_symbol": "Symbol already open",
    "alt_cap": "Altcoin cap reached",
    "correlation_unknown": "Correlation data unavailable",
    "correlation_cluster": "Correlation limit reached",
    "no_capital": "No eligible capital",
    "no_feasible_ladder": "No ladder fits the limits",
    "missing_stop": "Stop is missing",
    "market_data": "Market data incomplete",
    "rules": "Risk rules unavailable",
    "abnormal_volatility": "Volatility is outside the band",
    "insufficient_free_usdt": "Free quote is below the sleeve need",
    "mtm_unreliable": "Mark to market is not reliable",
}

_BACKTEST_WHY = "Backtest did not pass, so the BUY was held."
_EVIDENCE_WHY = "Market check did not pass, so the BUY was held."
_BLOCK_FALLBACK = "Risk rejected this pair."


def _sentence(text: str) -> str:
    shown = clip(text)
    if not shown or shown.endswith((".", "!", "?", "…")):
        return shown
    return shown + "."


def clip(text: str, limit: int = _CLIP) -> str:
    """One line, at most `limit` characters. A clipped line ends with an ellipsis."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    if limit <= 1:
        return flat[:limit]
    return flat[: limit - 1] + "…"


def telegram_text(event: str, detail: str) -> str:
    """Operator alerts keep `event: detail`. A verdict card is the whole bubble."""
    if detail.startswith(_MARK):
        return detail[:_LIMIT]
    return f"{event}: {detail}"[:_LIMIT]


def halt_on_card() -> str:
    return _join([
        f"{_MARK} — HALT",
        f"Action: {_ACTION_ICON['HALT']} HALT",
        "Why: The halt switch is on.",
        "Size: none",
        "Next: No new orders until the switch is off. Open positions stay as they are.",
    ])


def halt_off_card() -> str:
    return _join([
        f"{_MARK} — HALT",
        "Why: The halt switch is off.",
        "Size: none",
        "Next: Halt is off. Entries may be considered on the next run.",
    ])


def worker_block_card() -> str:
    return _join([
        f"{_MARK} — BLOCKED",
        f"Action: {_ACTION_ICON['BLOCKED']} BLOCKED",
        "Why: Research did not finish.",
        "Size: none",
        "Next: No order.",
    ])


def block_why(reason: str) -> str:
    """Desk phrase for a known reason code. Anything else stays off the bubble."""
    code = str(reason or "").split(",")[0].strip()
    return _BLOCK_WHY.get(code, _BLOCK_FALLBACK)


def spot_card(
    *,
    symbol: str,
    action: str,
    confidence: object,
    thesis: str = "",
    regime: str | None = None,
    rsi: object = None,
    entry: object = None,
    stop: object = None,
    take: object = None,
    pair_cap: object = None,
    model_action: str | None = None,
    backtest_passed: bool | None = None,
    evidence_passed: bool | None = None,
) -> str:
    """BUY, WAIT, or SKIP. Levels only on a BUY that has all three prices."""
    state = action if action in {"BUY", "WAIT", "SKIP"} else "WAIT"
    rows = [
        f"{_MARK} — {symbol}",
        f"Action: {_ACTION_ICON[state]} {state}",
        f"Confidence: {_percent(confidence)}",
    ]
    technical = _technical(regime, rsi)
    if technical:
        rows.append(technical)
    rows.append(f"Why: {clip(_why(state, thesis, model_action, backtest_passed, evidence_passed))}")
    if state == "BUY":
        prices = _levels(entry, stop, take)
        if prices:
            rows.extend(prices)
        rows.append(f"Size: {_cap(pair_cap)}")
        rows.append("Next: No order. Research only.")
    elif state == "WAIT":
        rows.append("Size: none")
        rows.append("Next: No order. Checked again on the next research run.")
    else:
        rows.append("Size: none")
        rows.append("Next: No order. This pair is not sized.")
    return _join(rows)


def blocked_card(*, symbol: str, confidence: object, why: str, was: str = "BUY") -> str:
    was_action = was if was in {"BUY", "WAIT", "SKIP"} else "BUY"
    confidence_row = f"Confidence: {_percent(confidence)}"
    if was_action == "BUY":
        confidence_row += " on a BUY"
    return _join([
        f"{_MARK} — {symbol}",
        f"Action: {_ACTION_ICON['BLOCKED']} BLOCKED",
        confidence_row,
        f"Why: {_sentence(why)}",
        "Size: none",
        "Next: No order. Risk rejected this pair.",
    ])


def _why(
    action: str,
    thesis: str,
    model_action: str | None,
    backtest_passed: bool | None,
    evidence_passed: bool | None,
) -> str:
    if action == "WAIT" and model_action == "BUY" and backtest_passed is False:
        return _BACKTEST_WHY
    if action == "WAIT" and evidence_passed is False:
        return _EVIDENCE_WHY
    return thesis


def _technical(regime: str | None, rsi: object) -> str | None:
    bits: list[str] = []
    if regime:
        bits.append(str(regime).replace("_", " "))
    shown = _number(rsi)
    if shown:
        bits.append(f"RSI {shown}")
    if not bits:
        return None
    return "Technical: 📊 " + ", ".join(bits)


def _levels(entry: object, stop: object, take: object) -> list[str] | None:
    shown = (_number(entry), _number(stop), _number(take))
    if any(item is None for item in shown):
        return None
    entry_text, stop_text, take_text = shown
    return [f"Entry: {entry_text}", f"SL: {stop_text}", f"TP: {take_text}"]


def _cap(pair_cap: object) -> str:
    shown = _number(pair_cap)
    if shown is None:
        return "none"
    percent = _number(Decimal(shown) * 100)
    return f"{percent}% pair cap. Not sent."


def _percent(confidence: object) -> str:
    number = _decimal(confidence)
    if number is None:
        return "0%"
    whole = (number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{int(whole)}%"


def _number(value: object) -> str | None:
    number = _decimal(value)
    if number is None:
        return None
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return number


def _join(rows: list[str]) -> str:
    text = "\n".join(rows)
    if len(text) <= _LIMIT:
        return text
    return clip(text, _LIMIT)
