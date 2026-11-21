"""3Commas account list, paper/live selection, and the WARP budget cap.

The model never chooses an account. Paper mode can select only a demo account.
A real account can be selected only in live mode, and only with the same
acknowledgement string the process already requires to boot in live mode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from .models import Account, AssetBalance, positive
from .threecommas import IntegrationError

LIVE_ACK = "I_UNDERSTAND_LIVE_ORDERS"
OPEN_BOT = {"pending", "unknown", "confirmed", "active", "paused"}


@dataclass(frozen=True)
class BrokerAccount:
    code: str
    name: str
    demo: bool
    status: str
    exchange_code: str
    exchange_name: str
    market: str
    quote: str
    free: Decimal
    used: Decimal
    total: Decimal

    def to_public(self, *, selectable: bool, reason: str) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "demo": self.demo,
            "badge": "DEMO" if self.demo else "REAL",
            "status": self.status,
            "exchange_code": self.exchange_code,
            "exchange_name": self.exchange_name,
            "market": self.market,
            "quote": self.quote,
            "free": format(self.free, "f"),
            "used": format(self.used, "f"),
            "total": format(self.total, "f"),
            "selectable": selectable,
            "reason": reason,
        }

    def as_account(self) -> Account:
        """Risk engine view. Quote totals are copied into the USDT fields only for USDT."""
        asset = AssetBalance(self.free, self.used, self.total)
        assets = {self.quote: asset}
        free = self.free if self.quote == "USDT" else Decimal("0")
        used = self.used if self.quote == "USDT" else Decimal("0")
        total = self.total if self.quote == "USDT" else Decimal("0")
        if self.quote == "USDT":
            assets["USDT"] = asset
        return Account(
            self.code,
            self.demo,
            free,
            used,
            total,
            assets,
            name=self.name,
            exchange_code=self.exchange_code,
            exchange_name=self.exchange_name,
            market=self.market,
            quote_asset=self.quote,
            status=self.status,
        )


def classify_market(exchange_code: str, exchange_name: str) -> str:
    blob = f"{exchange_code} {exchange_name}".upper()
    if "FUTURE" in blob:
        return "futures"
    if "SPOT" in blob:
        return "spot"
    return "other"


def parse_profile(row: dict) -> BrokerAccount:
    """One API profile. A missing demo flag fails closed: never guess real versus paper."""
    if not isinstance(row, dict):
        raise IntegrationError("Account row is not an object", uncertain=True)
    code = row.get("code")
    if not isinstance(code, str) or len(code) < 4:
        raise IntegrationError("Account code missing", uncertain=True)
    if not isinstance(row.get("demo"), bool):
        raise IntegrationError("Account demo/live classification absent", uncertain=True)
    exchange = row.get("exchange") if isinstance(row.get("exchange"), dict) else {}
    exchange_code = str(exchange.get("code") or "")
    exchange_name = str(exchange.get("name") or "")
    if not exchange_code:
        raise IntegrationError("Account exchange code missing", uncertain=True)
    market = classify_market(exchange_code, exchange_name)
    status = str(row.get("status") or "unknown")
    quote, free, used, total = _quote_balance(row.get("balance"))
    name = str(row.get("name") or code)
    return BrokerAccount(
        code,
        name,
        row["demo"],
        status,
        exchange_code,
        exchange_name,
        market,
        quote,
        free,
        used,
        total,
    )


def _quote_balance(balance: object) -> tuple[str, Decimal, Decimal, Decimal]:
    assets = balance.get("assets") if isinstance(balance, dict) else None
    if not isinstance(assets, dict) or not assets:
        raise IntegrationError("Account balance unavailable", uncertain=True)
    parsed: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for asset, item in assets.items():
        if not isinstance(item, dict):
            raise IntegrationError("Asset balance row is incomplete", uncertain=True)
        free, used, total = (positive(item.get(key), key) for key in ("free", "used", "total"))
        parsed[str(asset)] = (free, used, total)
    for preferred in ("USDT", "USDC"):
        if preferred in parsed:
            free, used, total = parsed[preferred]
            return preferred, free, used, total
    asset = max(parsed, key=lambda key: parsed[key][2])
    free, used, total = parsed[asset]
    return asset, free, used, total


def account_allowed(
    account: BrokerAccount,
    *,
    mode: str,
    live_ack: str,
    intent_market: str | None = None,
) -> tuple[bool, str]:
    """Whether the operator may save this account as the bot target.

    Observe can remember a demo account and cannot remember a real one.
    Paper accepts only demo. Live accepts only a real account, and only when
    the acknowledgement matches the string required to start live mode.
    """
    if account.status != "active":
        return False, "inactive"
    if account.quote != "USDT":
        return False, "quote_not_usdt"
    if account.market != "spot":
        return False, "spot_only"
    if mode == "paper":
        if not account.demo:
            return False, "paper_requires_demo"
        return True, "ok"
    if mode == "live":
        if account.demo:
            return False, "live_refuses_demo"
        if live_ack != LIVE_ACK:
            return False, "live_ack_required"
        return True, "ok"
    if mode == "observe":
        if not account.demo:
            return False, "observe_refuses_real"
        return True, "observe_saved"
    return False, "mode"


def execution_allowed(mode: str, demo: bool, live_ack: str) -> tuple[bool, str]:
    """Last check before a POST. Observe never passes. Live still needs the ack."""
    if mode == "observe":
        return False, "observe"
    if mode == "paper":
        if not demo:
            return False, "paper_requires_demo"
        return True, "ok"
    if mode == "live":
        if demo:
            return False, "live_refuses_demo"
        if live_ack != LIVE_ACK:
            return False, "live_ack_required"
        return True, "ok"
    return False, "mode"


@dataclass(frozen=True)
class Budget:
    cap: Decimal
    used: Decimal
    remaining: Decimal
    open_bots: int
    max_bots: int
    absolute: Decimal
    fraction: Decimal

    def to_public(self) -> dict:
        return {
            "cap": format(self.cap, "f"),
            "used": format(self.used, "f"),
            "remaining": format(self.remaining, "f"),
            "open_bots": self.open_bots,
            "max_bots": self.max_bots,
            "absolute": format(self.absolute, "f"),
            "fraction": format(self.fraction, "f"),
        }


def budget_for(
    *,
    balance: Decimal,
    absolute: Decimal,
    fraction: Decimal,
    max_bots: int,
    bots: list[dict],
) -> Budget:
    cap = min(absolute, fraction * balance)
    if cap < 0:
        cap = Decimal("0")
    used = Decimal("0")
    open_bots = 0
    for bot in bots:
        if bot.get("status") not in OPEN_BOT:
            continue
        open_bots += 1
        used += positive(bot.get("commitment") or "0", "commitment")
    return Budget(cap, used, cap - used, open_bots, max_bots, absolute, fraction)


def budget_decision(budget: Budget, next_commitment: Decimal) -> tuple[bool, str]:
    if budget.open_bots >= budget.max_bots:
        return False, "max_bots"
    if next_commitment > budget.remaining:
        return False, "budget"
    return True, "ok"


def parse_budget_override(raw: str | None, code: str) -> dict | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    row = payload.get(code)
    return row if isinstance(row, dict) else None


def effective_budget(cfg, override: dict | None) -> tuple[Decimal, Decimal, int]:
    absolute = cfg.budget_usdt
    fraction = cfg.budget_fraction
    max_bots = cfg.max_warp_bots
    if not override:
        return absolute, fraction, max_bots
    if override.get("budget_usdt") not in (None, ""):
        absolute = positive(override["budget_usdt"], "budget", allow_zero=False)
    if override.get("budget_fraction") not in (None, ""):
        fraction = positive(override["budget_fraction"], "budget fraction", allow_zero=False)
        if fraction > 1:
            raise ValueError("Budget fraction exceeds one")
    if override.get("max_bots") not in (None, ""):
        max_bots = override["max_bots"]
        if type(max_bots) is not int or max_bots < 1:
            raise ValueError("Invalid bot cap")
    return absolute, fraction, max_bots
