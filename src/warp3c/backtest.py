"""Long-only check that a stop and target were reachable on closed candles."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .market import ema


@dataclass(frozen=True)
class BacktestResult:
    passed: bool
    reason: str
    trades: int
    wins: int
    losses: int
    expectancy: Decimal

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "expectancy": format(self.expectancy, "f"),
        }


def _fail(reason: str) -> BacktestResult:
    return BacktestResult(False, reason, 0, 0, 0, Decimal("0"))


# The scan already fetches this many 15m candles. A larger opt-in window is
# WARP_BACKTEST_CANDLES, and it is used only when that many candles are in hand.
DEFAULT_BACKTEST_CANDLES = 120
MAX_BACKTEST_CANDLES = 1000


def candles_for_backtest(candles: list[dict] | None, limit: int):
    """Return the series the backtest should see.

    Limit 120 (the default) returns the series unchanged, including None.
    A higher limit uses the most recent ``limit`` candles. A shorter series
    fails closed as not_enough_candles.
    """
    if limit <= DEFAULT_BACKTEST_CANDLES:
        return candles
    if not candles or len(candles) < limit:
        return None
    if len(candles) == limit:
        return candles
    return list(candles[-limit:])


def run_backtest(
    candles: list[dict] | None,
    stop_fraction: Decimal,
    take_fraction: Decimal,
    *,
    min_trades: int = 3,
) -> BacktestResult:
    """Enter only on an uptrend close. A bar that touches the stop is a loss."""
    if not candles or len(candles) < 60:
        return _fail("not_enough_candles")
    if stop_fraction <= 0 or take_fraction <= 0:
        return _fail("invalid_levels")
    closes = [c["close"] for c in candles]
    wins = 0
    losses = 0
    index = 50
    while index < len(candles) - 2:
        window = closes[: index + 1]
        fast = ema(window, 20)
        slow = ema(window, 50)
        momentum = (closes[index] / closes[index - 10]) - Decimal("1")
        if not (fast > slow and closes[index] > fast and momentum > 0):
            index += 1
            continue
        entry = closes[index]
        stop = entry * (Decimal("1") - stop_fraction)
        take = entry * (Decimal("1") + take_fraction)
        outcome = None
        exit_at = index
        for later in range(index + 1, len(candles)):
            low = candles[later]["low"]
            high = candles[later]["high"]
            if low <= stop:
                outcome = "loss"
                exit_at = later
                break
            if high >= take:
                outcome = "win"
                exit_at = later
                break
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        else:
            break
        index = exit_at + 1
    trades = wins + losses
    if trades < min_trades:
        return BacktestResult(False, "too_few_trades", trades, wins, losses, Decimal("0"))
    expectancy = (Decimal(wins) * take_fraction - Decimal(losses) * stop_fraction) / Decimal(trades)
    if expectancy <= 0:
        return BacktestResult(False, "non_positive_expectancy", trades, wins, losses, expectancy)
    return BacktestResult(True, "ok", trades, wins, losses, expectancy)
