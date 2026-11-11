"""Validated boundaries between model text, market evidence, and orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def positive(value: object, label: str, *, allow_zero: bool = True) -> Decimal:
    try:
        result = Decimal(str(value))
    except (ValueError, ArithmeticError, InvalidOperation, TypeError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise ValueError(f"Invalid {label}")
    return result


@dataclass(frozen=True)
class AssetBalance:
    free: Decimal
    used: Decimal
    total: Decimal


@dataclass(frozen=True)
class MarketEvidence:
    """Numbers taken from an exchange market-data response, not from the model."""

    symbol: str
    as_of: datetime
    last_price: Decimal
    quote_volume: Decimal
    trend: str
    momentum: Decimal
    rsi: Decimal
    volatility: Decimal
    regime: str
    min_notional: Decimal
    quote_precision: int
    tradable: bool
    passed: bool
    reason: str


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: str
    confidence: Decimal
    as_of: datetime
    source: str
    thesis: str
    evidence: MarketEvidence | None = None

    @classmethod
    def parse(cls, raw: dict, allowed_symbols: set[str], *, max_age_minutes: int) -> Signal:
        if not isinstance(raw, dict):
            raise ValueError("Signal must be an object")
        symbol = str(raw.get("symbol", "")).upper().strip()
        action = str(raw.get("action", "")).upper().strip()
        if action == "HOLD":
            action = "WAIT"
        if symbol not in allowed_symbols or action not in {"BUY", "WAIT", "SKIP"}:
            raise ValueError("Unknown symbol or trade action")
        confidence = positive(raw.get("confidence"), "confidence")
        if confidence > 1:
            raise ValueError("Confidence exceeds one")
        as_of = datetime.fromisoformat(str(raw.get("as_of", "")).replace("Z", "+00:00"))
        if as_of.tzinfo is None:
            raise ValueError("Signal timestamp needs timezone")
        now = datetime.now(timezone.utc)
        age = (now - as_of.astimezone(timezone.utc)).total_seconds()
        if age < -60 or age > 60 * max_age_minutes:
            raise ValueError("Signal evidence stale or future-dated")
        source = str(raw.get("source", "")).strip()
        thesis = str(raw.get("thesis", "")).strip()
        if len(source) < 3 or len(thesis) < 8:
            raise ValueError("Signal lacks evidence source or thesis")
        return cls(symbol, action, confidence, as_of, source[:300], thesis[:800])


@dataclass(frozen=True)
class Account:
    code: str
    demo: bool
    free_usdt: Decimal
    used_usdt: Decimal
    total_usdt: Decimal
    assets: dict[str, AssetBalance] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def holdings(self) -> dict[str, AssetBalance]:
        if self.assets:
            return self.assets
        return {
            "USDT": AssetBalance(self.free_usdt, self.used_usdt, self.total_usdt),
        }


@dataclass(frozen=True)
class PlannedOrder:
    symbol: str
    base_amount: Decimal
    full_commitment: Decimal
    confidence: Decimal


@dataclass(frozen=True)
class EquityMark:
    marked_at: datetime
    equity: Decimal
    reliable: bool


@dataclass(frozen=True)
class RiskDecision:
    orders: list[PlannedOrder]
    execution_permitted: bool
    reason: str
    equity: Decimal | None
    equity_reliable: bool
