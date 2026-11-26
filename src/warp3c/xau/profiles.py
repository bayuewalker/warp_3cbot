"""Broker profile. Lots are informational. risk_pct is the tighter of the rule and the cap."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

D = Decimal
FIELDS = (
    "name",
    "account_type",
    "broker_symbol",
    "contract_size",
    "digits",
    "tick_size",
    "tick_value",
    "lot_min",
    "lot_step",
    "lot_max",
    "commission_per_lot_rt",
    "swap_long",
    "swap_short",
    "typical_spread",
    "server_tz",
    "starting_balance",
    "daily_dd_pct",
    "daily_dd_basis",
    "max_dd_pct",
    "max_dd_type",
    "news_rule",
    "news_window_min",
    "ea_allowed",
    "min_hold_seconds",
    "max_risk_per_trade_pct",
    "internal_buffer",
    "pip_size",
)


class ProfileError(ValueError):
    """The TOML profile is missing a field or a bound."""


@dataclass(frozen=True)
class Profile:
    name: str
    account_type: str
    broker_symbol: str
    contract_size: D
    digits: int
    tick_size: D
    tick_value: D
    lot_min: D
    lot_step: D
    lot_max: D
    commission_per_lot_rt: D
    swap_long: D
    swap_short: D
    typical_spread: D
    server_tz: str
    starting_balance: D
    daily_dd_pct: D
    daily_dd_basis: str
    max_dd_pct: D
    max_dd_type: str
    news_rule: str
    news_window_min: int
    ea_allowed: bool
    min_hold_seconds: int
    max_risk_per_trade_pct: D
    internal_buffer: D
    pip_size: D


def load_profile(path: str | Path) -> Profile:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    missing = [name for name in FIELDS if name not in raw]
    if missing:
        raise ProfileError("profile missing " + ",".join(missing))
    try:
        profile = Profile(
            name=str(raw["name"]),
            account_type=str(raw["account_type"]),
            broker_symbol=str(raw["broker_symbol"]),
            contract_size=_dec(raw["contract_size"]),
            digits=int(raw["digits"]),
            tick_size=_dec(raw["tick_size"]),
            tick_value=_dec(raw["tick_value"]),
            lot_min=_dec(raw["lot_min"]),
            lot_step=_dec(raw["lot_step"]),
            lot_max=_dec(raw["lot_max"]),
            commission_per_lot_rt=_dec(raw["commission_per_lot_rt"]),
            swap_long=_dec(raw["swap_long"]),
            swap_short=_dec(raw["swap_short"]),
            typical_spread=_dec(raw["typical_spread"]),
            server_tz=str(raw["server_tz"]),
            starting_balance=_dec(raw["starting_balance"]),
            daily_dd_pct=_dec(raw["daily_dd_pct"]),
            daily_dd_basis=str(raw["daily_dd_basis"]),
            max_dd_pct=_dec(raw["max_dd_pct"]),
            max_dd_type=str(raw["max_dd_type"]),
            news_rule=str(raw["news_rule"]),
            news_window_min=int(raw["news_window_min"]),
            ea_allowed=bool(raw["ea_allowed"]),
            min_hold_seconds=int(raw["min_hold_seconds"]),
            max_risk_per_trade_pct=_dec(raw["max_risk_per_trade_pct"]),
            internal_buffer=_dec(raw["internal_buffer"]),
            pip_size=_dec(raw["pip_size"]),
        )
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ProfileError("profile field is not usable") from exc
    _validate(profile)
    return profile


def _dec(value: object) -> D:
    number = D(str(value))
    if not number.is_finite():
        raise ProfileError("non-finite number")
    return number


def _validate(profile: Profile) -> None:
    if profile.contract_size <= 0 or profile.tick_size <= 0 or profile.tick_value <= 0:
        raise ProfileError("contract, tick size, and tick value must be positive")
    if profile.lot_step <= 0 or profile.lot_min <= 0 or profile.lot_max < profile.lot_min:
        raise ProfileError("lot bounds are inconsistent")
    if profile.starting_balance <= 0 or profile.pip_size <= 0:
        raise ProfileError("balance and pip size must be positive")
    if profile.max_risk_per_trade_pct <= 0 or profile.max_risk_per_trade_pct > D("5"):
        raise ProfileError("max_risk_per_trade_pct is out of range")
    if profile.digits < 0 or profile.min_hold_seconds < 0 or profile.news_window_min < 0:
        raise ProfileError("digits, hold, and news window cannot be negative")
    try:
        ZoneInfo(profile.server_tz)
    except Exception as exc:
        raise ProfileError("server_tz is not a timezone") from exc


def risk_pct(tier_pct: D, profile: Profile) -> D:
    """The profile cap applies when it is lower than the rule tier. Both are in percent."""
    return min(tier_pct, profile.max_risk_per_trade_pct)


def floor_to_step(quantity: D, step: D) -> D:
    if step <= 0:
        raise ProfileError("lot step must be positive")
    steps = (quantity / step).to_integral_value(rounding=ROUND_DOWN)
    return steps * step


def example_lot(risk_usd: D, r_usd: D, profile: Profile) -> D:
    """Informational size. floor_to_step(risk_usd / ((r_usd / tick_size) * tick_value))."""
    if r_usd <= 0 or risk_usd <= 0:
        return profile.lot_min
    per_lot = (r_usd / profile.tick_size) * profile.tick_value
    if per_lot <= 0:
        return profile.lot_min
    sized = floor_to_step(risk_usd / per_lot, profile.lot_step)
    if sized < profile.lot_min:
        return profile.lot_min
    if sized > profile.lot_max:
        return profile.lot_max
    return sized
