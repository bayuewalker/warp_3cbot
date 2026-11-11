"""Strict configuration: no execution without credentials and explicit mode."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal


def dec(name: str, fallback: str) -> Decimal:
    result = Decimal(os.getenv(name, fallback))
    if not result.is_finite():
        raise ValueError(f"{name}: non-finite number")
    return result


@dataclass(frozen=True)
class Settings:
    mode: str = "observe"
    db_path: str = "/data/warp.db"
    cycle_seconds: int = 900
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    exchange_code: str = "BINANCE"
    api_url: str = "https://trade.3commas.io"
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)
    profile_code: str = ""
    vibe_url: str = "http://127.0.0.1:8899"
    vibe_key: str = field(default="", repr=False)
    max_committed: Decimal = Decimal("0.40")
    max_pair: Decimal = Decimal("0.15")
    min_reserve: Decimal = Decimal("0.25")
    max_positions: int = 4
    min_confidence: Decimal = Decimal("0.75")
    max_signal_age: int = 30
    min_base: Decimal = Decimal("10")
    max_base: Decimal = Decimal("100")
    dca_orders: int = 4
    dca_deviation: Decimal = Decimal("0.02")
    dca_multiplier: Decimal = Decimal("1.5")
    dca_step_multiplier: Decimal = Decimal("1.2")
    take_profit: Decimal = Decimal("0.025")
    stop_loss: Decimal = Decimal("0.20")
    live_ack: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if self.mode not in {"observe", "paper", "live"}:
            raise ValueError("Invalid TRADING_MODE")
        if self.mode == "live" and self.live_ack != "I_UNDERSTAND_LIVE_ORDERS":
            raise ValueError("Live trading requires WARP_LIVE_EXECUTION_ACK")
        if self.mode != "observe" and not all((self.api_key, self.api_secret, self.profile_code)):
            raise ValueError("3Commas credentials and API profile are required")
        if self.cycle_seconds < 60 or self.max_signal_age < 1:
            raise ValueError("Invalid cadence or signal age")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Symbol universe empty or duplicated")
        if any(not (s.isalnum() and s.endswith("USDT")) for s in self.symbols):
            raise ValueError("Only allowlisted USDT pairs are supported")
        if self.max_positions < 1 or not 1 <= self.dca_orders <= 30:
            raise ValueError("Invalid position/order count")
        if self.min_base <= 0 or self.max_base < self.min_base:
            raise ValueError("Invalid base order range")
        for name in ("max_committed", "max_pair", "min_reserve", "min_confidence", "dca_deviation", "take_profit", "stop_loss"):
            v = getattr(self, name)
            if not v.is_finite() or not Decimal("0") < v <= Decimal("1"):
                raise ValueError(f"Invalid fraction: {name}")
        if self.max_committed + self.min_reserve > 1:
            raise ValueError("Committed + reserve fractions cannot exceed one")
        if not Decimal("0.1") <= self.dca_multiplier <= Decimal("10"):
            raise ValueError("Invalid DCA amount multiplier")
        if not Decimal("0.1") <= self.dca_step_multiplier <= Decimal("10"):
            raise ValueError("Invalid DCA deviation multiplier")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            mode=os.getenv("TRADING_MODE", "observe").lower(),
            db_path=os.getenv("WARP_DB_PATH", "/data/warp.db"),
            cycle_seconds=int(os.getenv("WARP_CYCLE_SECONDS", "900")),
            symbols=tuple(s.strip().upper() for s in os.getenv("WARP_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()),
            exchange_code=os.getenv("WARP_EXCHANGE_CODE", "BINANCE").upper(),
            api_url=os.getenv("THREECOMMAS_API_URL", "https://trade.3commas.io"),
            api_key=os.getenv("THREECOMMAS_API_KEY", ""),
            api_secret=os.getenv("THREECOMMAS_API_SECRET", ""),
            profile_code=os.getenv("THREECOMMAS_PROFILE_CODE", ""),
            vibe_url=os.getenv("VIBE_API_URL", "http://127.0.0.1:8899"),
            vibe_key=os.getenv("API_AUTH_KEY", ""),
            max_committed=dec("WARP_MAX_COMMITTED_FRACTION", "0.40"),
            max_pair=dec("WARP_MAX_PER_PAIR_FRACTION", "0.15"),
            min_reserve=dec("WARP_MIN_FREE_RESERVE_FRACTION", "0.25"),
            max_positions=int(os.getenv("WARP_MAX_ACTIVE_STRATEGIES", "4")),
            min_confidence=dec("WARP_MIN_CONFIDENCE", "0.75"),
            max_signal_age=int(os.getenv("WARP_MAX_SIGNAL_AGE_MINUTES", "30")),
            min_base=dec("WARP_MIN_BASE_ORDER_USDT", "10"),
            max_base=dec("WARP_MAX_BASE_ORDER_USDT", "100"),
            dca_orders=int(os.getenv("WARP_DCA_TOTAL_ORDERS", "4")),
            dca_deviation=dec("WARP_DCA_PRICE_DEVIATION", "0.02"),
            dca_multiplier=dec("WARP_DCA_VOLUME_MULTIPLIER", "1.5"),
            dca_step_multiplier=dec("WARP_DCA_DEVIATION_MULTIPLIER", "1.2"),
            take_profit=dec("WARP_TAKE_PROFIT_DEVIATION", "0.025"),
            stop_loss=dec("WARP_STOP_LOSS_DEVIATION", "0.20"),
            live_ack=os.getenv("WARP_LIVE_EXECUTION_ACK", ""),
        )
