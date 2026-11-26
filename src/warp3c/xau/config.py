"""XAU settings. Invalid values raise XauConfigError and never escape the runner."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

FEEDS = ("binance", "bybit", "okx")
RULES_VERSION = "refined-1.0"


class XauConfigError(ValueError):
    """A WARP_XAU_* value is present and outside its bounds."""


def _decimal(name: str, raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise XauConfigError(f"{name} is not a number") from exc
    if not value.is_finite():
        raise XauConfigError(f"{name} is not finite")
    return value


def _feed_list(raw: str) -> tuple[str, ...]:
    names = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    if not names or any(name not in FEEDS for name in names):
        raise XauConfigError("WARP_XAU_FEED_FALLBACKS must list binance, bybit, or okx")
    return names


@dataclass(frozen=True)
class XauSettings:
    feed: str = "binance"
    feed_fallbacks: tuple[str, ...] = ("bybit", "okx")
    spread_assumed: Decimal = Decimal("0.30")
    basis_max: Decimal = Decimal("15.0")
    news_max_age_h: int = 24
    profile_path: str = ""
    ref_equity: Decimal = Decimal("10000")
    cycle_seconds: int = 30
    rules_version: str = RULES_VERSION

    def __post_init__(self) -> None:
        if self.feed not in FEEDS:
            raise XauConfigError("WARP_XAU_FEED must be binance, bybit, or okx")
        if not self.feed_fallbacks or any(name not in FEEDS for name in self.feed_fallbacks):
            raise XauConfigError("WARP_XAU_FEED_FALLBACKS must list binance, bybit, or okx")
        if not Decimal("0.05") <= self.spread_assumed <= Decimal("2.0"):
            raise XauConfigError("WARP_XAU_SPREAD_ASSUMED must be between 0.05 and 2.0")
        if self.basis_max <= 0:
            raise XauConfigError("WARP_XAU_BASIS_MAX must be positive")
        if self.news_max_age_h < 1 or self.news_max_age_h > 168:
            raise XauConfigError("WARP_XAU_NEWS_MAX_AGE_H is out of range")
        if self.ref_equity <= 0:
            raise XauConfigError("WARP_XAU_REF_EQUITY must be positive")
        if not 15 <= self.cycle_seconds <= 300:
            raise XauConfigError("WARP_XAU_CYCLE_SECONDS must be between 15 and 300")
        if self.rules_version != RULES_VERSION:
            raise XauConfigError("WARP_XAU_RULES_VERSION must be refined-1.0")
        if not self.profile_path:
            object.__setattr__(self, "profile_path", str(_default_profile()))

    @classmethod
    def from_env(cls) -> XauSettings:
        """Read WARP_XAU_* only. Telegram credentials are never read here."""
        spread = _decimal("WARP_XAU_SPREAD_ASSUMED", os.getenv("WARP_XAU_SPREAD_ASSUMED", "0.30"))
        basis = _decimal("WARP_XAU_BASIS_MAX", os.getenv("WARP_XAU_BASIS_MAX", "15.0"))
        equity = _decimal("WARP_XAU_REF_EQUITY", os.getenv("WARP_XAU_REF_EQUITY", "10000"))
        try:
            news_age = int(os.getenv("WARP_XAU_NEWS_MAX_AGE_H", "24"))
            cycle = int(os.getenv("WARP_XAU_CYCLE_SECONDS", "30"))
        except ValueError as exc:
            raise XauConfigError("WARP_XAU cycle or news age is not an integer") from exc
        profile = os.getenv("WARP_XAU_PROFILE", "").strip() or str(_default_profile())
        return cls(
            feed=os.getenv("WARP_XAU_FEED", "binance").strip().lower(),
            feed_fallbacks=_feed_list(os.getenv("WARP_XAU_FEED_FALLBACKS", "bybit,okx")),
            spread_assumed=spread,
            basis_max=basis,
            news_max_age_h=news_age,
            profile_path=profile,
            ref_equity=equity,
            cycle_seconds=cycle,
            rules_version=os.getenv("WARP_XAU_RULES_VERSION", RULES_VERSION).strip(),
        )


def _default_profile() -> Path:
    return Path(__file__).resolve().parent / "profiles" / "example.toml"
