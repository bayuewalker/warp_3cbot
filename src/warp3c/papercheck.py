"""One-shot paper round trip against a demo account. Real accounts are refused.

Run: python -m warp3c.papercheck

The command creates a tiny WARP DCA bot, reads it back, pauses it, and deletes
it. It does not run in observe or live mode, and it does not run while HALT is set.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .accounts import budget_decision, budget_for, effective_budget, parse_budget_override
from .ownership import NotOwned, apply_action, warp_name
from .threecommas import IntegrationError


def _step(name: str, ok: bool, detail: str) -> dict:
    return {"step": name, "ok": ok, "detail": detail}


def choose_demo(accounts, *, requested: str | None, target: str | None):
    demos = [row for row in accounts if row.demo and row.status == "active" and row.quote == "USDT"]
    chosen_code = requested or target
    if chosen_code:
        matches = [row for row in accounts if row.code == chosen_code]
        if len(matches) != 1:
            return None, "account_not_found"
        account = matches[0]
        if not account.demo:
            return None, "paper_requires_demo"
        if account.quote != "USDT" or account.status != "active":
            return None, "account_not_usable"
        return account, "ok"
    futures = [row for row in demos if row.market == "futures"]
    pool = futures or demos
    if len(pool) != 1:
        return None, "demo_account_ambiguous" if pool else "demo_account_missing"
    return pool[0], "ok"


def run_roundtrip(cfg, broker, journal, *, now: datetime | None = None) -> dict:
    """Create, read, pause, and delete one tiny bot. Stops before any write if a gate fails."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    steps: list[dict] = []
    report = {"ok": False, "places_orders": False, "steps": steps, "mode": cfg.mode}

    def fail(step: str, detail: str) -> dict:
        steps.append(_step(step, False, detail))
        return report

    if cfg.mode != "paper":
        return fail("mode", "paper_mode_required")
    steps.append(_step("mode", True, "paper"))
    if Path(cfg.db_path).parent.joinpath("HALT").exists():
        return fail("halt", "halt")
    steps.append(_step("halt", True, "clear"))
    if not cfg.api_key or not cfg.api_secret:
        return fail("credentials", "missing")
    steps.append(_step("credentials", True, "present"))
    try:
        accounts = broker.list_accounts()
    except IntegrationError:
        return fail("accounts", "unavailable")
    target = None
    raw_target = journal.get_setting("target_account")
    if raw_target:
        try:
            saved = json.loads(raw_target)
        except json.JSONDecodeError:
            saved = {}
        if isinstance(saved, dict):
            target = saved.get("code")
    account, reason = choose_demo(
        accounts,
        requested=os.getenv("WARP_PAPER_ACCOUNT", "").strip() or None,
        target=target if isinstance(target, str) else None,
    )
    if account is None:
        return fail("account", reason)
    if not account.demo:
        return fail("account", "paper_requires_demo")
    steps.append(_step("account", True, f"{account.name} {account.market} demo"))
    warp_id = secrets.token_hex(4)
    name = warp_name(warp_id)
    quote = Decimal("15")
    override = parse_budget_override(journal.get_setting("account_budgets"), account.code)
    try:
        absolute, fraction, max_bots = effective_budget(cfg, override)
    except ValueError:
        return fail("budget", "invalid")
    decision = budget_decision(
        budget_for(
            balance=account.total,
            absolute=absolute,
            fraction=fraction,
            max_bots=max_bots,
            bots=journal.warp_bots(account.code),
        ),
        quote,
    )
    if not decision[0]:
        return fail("budget", decision[1])
    steps.append(_step("budget", True, decision[1]))
    payload = {
        "name": name,
        "exchangeCode": account.exchange_code,
        "pairsCodes": ["BTCUSDT"],
        "profilesCodes": [account.code],
        "dcaTradingType": "long",
        "marginMode": "isolated",
        "leverage": 1,
        "exchangeLeverage": 1,
        "amountPerTrade": quote,
        "amountPerTradeType": "quote",
        "amountPerTradeMultiplier": 1,
        "orderType": "market",
        "entrySignalCondition": "immediate",
        "extraOrderCount": 1,
        "extraOrderDeviation": Decimal("0.02"),
        "extraOrderVolumeMultiplier": Decimal("1"),
        "extraOrderDeviationMultiplier": Decimal("1"),
        "takeProfits": [{"priceDeviation": Decimal("0.02"), "portfolio": Decimal("1")}],
        "maxOpenPositions": 1,
    }
    stamp = moment.isoformat()
    journal.insert_warp_bot(
        {
            "warp_id": warp_id,
            "name": name,
            "client_id": None,
            "external_code": None,
            "kind": "dca",
            "product": "dca_bot",
            "account_code": account.code,
            "market": account.market,
            "symbol": "BTCUSDT",
            "status": "pending",
            "commitment": format(quote, "f"),
            "created_at": stamp,
            "updated_at": stamp,
        }
    )
    report["places_orders"] = True
    try:
        created = broker.post_dca_bot(payload)
    except IntegrationError:
        journal.update_warp_bot(warp_id, status="unknown", updated_at=datetime.now(timezone.utc).isoformat())
        return fail("create", "broker_error")
    steps.append(_step("create", True, name))
    code = created.get("code") if isinstance(created, dict) else None
    if not isinstance(code, str) or not code:
        try:
            listed = broker.list_dca_bots()
        except IntegrationError:
            return fail("read", "list_failed")
        matches = [row for row in listed if row.get("name") == name]
        if len(matches) != 1:
            return fail("read", "name_not_unique")
        code = matches[0].get("code")
    if not isinstance(code, str) or not code:
        return fail("read", "missing_code")
    try:
        remote = broker.get_dca_bot(code)
    except IntegrationError:
        return fail("read", "lookup_failed")
    if remote.get("name") != name:
        return fail("read", "foreign_bot")
    journal.update_warp_bot(
        warp_id,
        external_code=code,
        status="confirmed",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    steps.append(_step("read", True, code))
    try:
        apply_action(broker, journal, warp_id, "pause")
    except (NotOwned, IntegrationError, ValueError, AttributeError):
        return fail("pause", "refused")
    steps.append(_step("pause", True, "paused"))
    try:
        apply_action(broker, journal, warp_id, "delete")
    except (NotOwned, IntegrationError, ValueError, AttributeError):
        return fail("delete", "refused")
    steps.append(_step("delete", True, "deleted"))
    report["ok"] = True
    return report


def main() -> int:
    from .config import Settings
    from .state import OrderJournal
    from .threecommas import ThreeCommas

    cfg = Settings.from_env()
    journal = OrderJournal(cfg.db_path)
    broker = ThreeCommas(cfg.api_url, cfg.api_key, cfg.api_secret)
    try:
        report = run_roundtrip(cfg, broker, journal)
    finally:
        broker.close()
        journal.close()
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
