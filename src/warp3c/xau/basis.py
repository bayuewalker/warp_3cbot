"""Perp-minus-spot basis. Median of the last 15 one-minute samples. Spec §2."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .candles import Bar

D = Decimal
# One sample per minute. Fewer than this stays unhealthy with reason few_samples.
MIN_SAMPLES = 10


@dataclass(frozen=True)
class BasisState:
    value: D | None
    healthy: bool
    samples: int
    std: D | None
    reason: str


class BasisBook:
    def __init__(self, maximum: D):
        self.maximum = maximum
        self._samples: deque[tuple[datetime, D]] = deque(maxlen=15)

    def observe(self, perp_close: D | None, spot: D | None, minute: datetime) -> BasisState:
        """One sample per minute. Unhealthy under 10 samples, |basis| > max, or std > 1.50."""
        if perp_close is not None and spot is not None:
            stamp = minute.replace(second=0, microsecond=0)
            if not self._samples or self._samples[-1][0] != stamp:
                self._samples.append((stamp, perp_close - spot))
        values = [sample[1] for sample in self._samples]
        if len(values) < MIN_SAMPLES:
            return BasisState(None, False, len(values), None, "few_samples")
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            median = ordered[mid]
        else:
            median = (ordered[mid - 1] + ordered[mid]) / 2
        mean = sum(values, D("0")) / D(len(values))
        variance = sum((value - mean) ** 2 for value in values) / D(len(values))
        std = variance.sqrt()
        if abs(median) > self.maximum:
            return BasisState(median, False, len(values), std, "basis_wide")
        if std > D("1.50"):
            return BasisState(median, False, len(values), std, "basis_noisy")
        return BasisState(median, True, len(values), std, "ok")

    def seed(self, samples: list[tuple[datetime, D]]) -> None:
        self._samples.clear()
        for item in samples[-15:]:
            self._samples.append(item)


def last_close(bars: tuple[Bar, ...] | list[Bar]) -> D | None:
    if not bars:
        return None
    return bars[-1].close
