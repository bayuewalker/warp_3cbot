"""Eligible Binance spot USDT markets. Ranking uses exchange numbers only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Conservative exclusions for DCA discovery. These assets have quote-like or
# leveraged-token behavior inconsistent with the long-only spot playbook.
_STABLE_BASES = frozenset({
    "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USD1", "BUSD",
    "USDE", "USDS", "RLUSD", "PYUSD", "RWUSD", "EUR", "AEUR", "EURI",
    # United Stables. UUSDT is a USD-pegged coin, not a crypto market.
    "U",
})
# Spot USDT bases that end in B and are not Binance bStocks (checked 2026-11-21).
_CRYPTO_B_SUFFIX = frozenset({"ARB", "BB", "BNB", "CKB", "DGB", "SHIB", "TRB", "YB"})
# Binance groups every bStock (ticker + B, including leveraged ETFs such as SOXLB) here.
BSTOCK_PERMISSION = "TRD_GRP_261"


@dataclass(frozen=True)
class Candidate:
    symbol: str
    quote_volume: Decimal
    spread: Decimal | None
    tradable: bool
    min_notional: Decimal
    quote_precision: int
    tokenized_equity: bool = False


def _quote_like(base: str) -> bool:
    """Stables and other dollar pegs. A bare 'US' prefix is not enough: USUAL is crypto."""
    if base in _STABLE_BASES:
        return True
    return "USD" in base


def excluded_base(base: str) -> str | None:
    """Quote-like coins, leveraged tokens, and tokenized equities or ETFs."""
    if _quote_like(base):
        return "excluded_spot_instrument"
    leveraged = base.endswith(("DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")) or (
        len(base) > 4 and base.endswith("UP")
    )
    if leveraged:
        return "excluded_spot_instrument"
    # bStocks are the equity or ETF ticker plus B: NVDA -> NVDAB, SOXL -> SOXLB.
    if (
        base.endswith("B")
        and base not in _CRYPTO_B_SUFFIX
        and base[:-1].isalpha()
        and 1 <= len(base[:-1]) <= 5
    ):
        return "excluded_spot_instrument"
    return None


def excluded_symbol(symbol: str) -> bool:
    if not symbol.isalnum() or not symbol.endswith("USDT"):
        return True
    return excluded_base(symbol[:-4]) is not None


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
    base = candidate.symbol[:-4]
    # "JUP" is a legitimate spot ticker; do not drop all 3-letter names
    # just because the name happens to end in UP.
    if candidate.tokenized_equity or excluded_base(base):
        return "excluded_spot_instrument"
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
