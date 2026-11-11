"""Eligible Binance spot USDT markets. Ranking uses exchange numbers only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Candidate:
    symbol: str
    quote_volume: Decimal
    spread: Decimal | None
    tradable: bool
    min_notional: Decimal
    quote_precision: int


def classify(
    candidate: Candidate,
    *,
    allow: set[str],
    deny: set[str],
    min_volume: Decimal,
    max_spread: Decimal,
) -> str:
    if not candidate.symbol.isalnum() or not candidate.symbol.endswith("USDT"):
        return "not_usdt_spot"
    if not candidate.tradable:
        return "market_not_trading"
    if candidate.symbol in deny:
        return "denylist"
    if allow and candidate.symbol not in allow:
        return "outside_allowlist"
    if candidate.quote_volume < min_volume:
        return "illiquid"
    if candidate.spread is None:
        return "spread_unknown"
    if candidate.spread > max_spread:
        return "spread_too_wide"
    if candidate.min_notional <= 0 or candidate.quote_precision < 0:
        return "missing_market_rule"
    return "eligible"


def select_universe(
    candidates: list[Candidate],
    *,
    allow: set[str],
    deny: set[str],
    min_volume: Decimal,
    max_spread: Decimal,
    limit: int,
) -> tuple[tuple[str, ...], list[tuple[str, str]]]:
    """Return the capped eligible symbols and every rejection reason."""
    if limit < 1:
        raise ValueError("Candidate cap must be positive")
    ranked: list[Candidate] = []
    rejected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.symbol in seen:
            rejected.append((candidate.symbol, "duplicate"))
            continue
        seen.add(candidate.symbol)
        reason = classify(
            candidate,
            allow=allow,
            deny=deny,
            min_volume=min_volume,
            max_spread=max_spread,
        )
        if reason == "eligible":
            ranked.append(candidate)
        else:
            rejected.append((candidate.symbol, reason))
    ranked.sort(key=lambda item: item.quote_volume, reverse=True)
    chosen = tuple(item.symbol for item in ranked[:limit])
    for item in ranked[limit:]:
        rejected.append((item.symbol, "above_candidate_cap"))
    return chosen, rejected
