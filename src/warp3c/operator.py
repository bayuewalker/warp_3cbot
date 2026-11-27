"""Read-only operator snapshot. Trading controls are pause-only and authenticated."""

from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .auth import parse_cookies, session_valid
from .bots import PRESETS, BotTemplate, preview_capital
from .forward import summary
from .live import ACCOUNT_SECONDS
from .screener import ScreenerFilters
from .vibe import ResearchError

SNAPSHOT: dict[str, dict] = {}
RUNTIME: dict[str, object] = {"journal": None, "cfg": None, "engine": None}
HEALTH = {
    "alive": True,
    "dependencies_healthy": False,
    "market_data_fresh": False,
    "execution_permitted": False,
    "risk_permitted": False,
    "mode": "observe",
    "last_cycle_ok": None,
    "reason": "starting",
}
_LANE_SINCE = datetime.now(timezone.utc).isoformat()
_POSTPONED_ROUTES = (
    "/api/accounts",
    "/api/account-positions",
    "/api/positions",
    "/api/warp-bots",
    "/api/bots",
    "/api/forward",
    "/api/decisions",
    "/api/screener",
    "/api/journal",
    "/api/opportunities",
    "/api/signals",
    "/api/risk",
)


def lanes(state: str) -> dict:
    return {
        "primary": "xau",
        "spot": {"state": state, "since": _LANE_SINCE, "flag": "WARP_SPOT_LANE_ENABLED"},
    }
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WARP 3C</title>
<style>
body { font-family: sans-serif; margin: 1rem; }
input, button { font: inherit; margin: 0.25rem 0; }
pre { white-space: pre-wrap; word-break: break-word; }
</style>
</head>
<body>
<h1>WARP 3C</h1>
<p>Read only. Pause stops new strategies and does not close 3Commas positions.</p>
<label>Operator token <input id="token" type="password" autocomplete="off"></label>
<button id="load" type="button">Refresh</button>
<button id="pause" type="button">Pause new entries</button>
<pre id="out">No account data is loaded until the token is sent.</pre>
<script>
const routes = ["/api/status","/api/risk","/api/positions","/api/signals","/api/journal","/api/breakers","/api/alerts","/api/config"];
function headers() { return { "Authorization": "Bearer " + document.getElementById("token").value }; }
async function load() {
  const parts = [];
  for (const route of routes) {
    const response = await fetch(route, { headers: headers() });
    parts.push(route + " " + response.status + "\\n" + await response.text());
  }
  document.getElementById("out").textContent = parts.join("\\n\\n");
}
document.getElementById("load").addEventListener("click", load);
document.getElementById("pause").addEventListener("click", async () => {
  const response = await fetch("/api/pause", { method: "POST", headers: headers() });
  document.getElementById("out").textContent = await response.text();
});
</script>
</body>
</html>
"""

ROUTES = (
    "/api/status",
    "/api/risk",
    "/api/positions",
    "/api/signals",
    "/api/journal",
    "/api/breakers",
    "/api/alerts",
    "/api/config",
    "/api/opportunities",
    "/api/screener",
    "/api/bots",
    "/api/accounts",
    "/api/forward",
    "/api/warp-bots",
    "/api/decisions",
    "/api/events",
    "/api/prices",
    "/api/account-positions",
)


def _text(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def public_config(cfg) -> dict:
    """Numbers and modes only. Secrets and webhook URLs stay out."""
    return {
        "mode": cfg.mode,
        "shared_account": bool(cfg.shared_account),
        "discovery": cfg.discovery,
        "symbols": list(cfg.symbols),
        "discovery_allow": list(cfg.discovery_allow),
        "deny_symbols": list(cfg.deny_symbols),
        "max_candidates": cfg.max_candidates,
        "max_committed": _text(cfg.max_committed),
        "max_pair": _text(cfg.max_pair),
        "max_group": _text(cfg.max_group),
        "min_reserve": _text(cfg.min_reserve),
        "max_positions": cfg.max_positions,
        "fee_bps": _text(cfg.fee_bps),
        "slippage_bps": _text(cfg.slippage_bps),
        "max_daily_loss": _text(cfg.max_daily_loss),
        "max_drawdown": _text(cfg.max_drawdown),
        "scan_seconds": cfg.scan_seconds,
        "research_seconds": cfg.research_seconds,
        "monitor_seconds": cfg.monitor_seconds,
        "reconcile_seconds": cfg.reconcile_seconds,
        "alerts_configured": bool(cfg.alert_webhook or cfg.telegram_bot_token),
        "analysis": cfg.analysis,
        "require_backtest": cfg.require_backtest,
        "blend_confidence": cfg.blend_confidence,
        "password_login": len(os.getenv("WARP_DASHBOARD_PASSWORD", "")) >= 12,
        "sleeve_usdt": _text(cfg.sleeve_usdt),
        "sleeve_pct": _text(cfg.sleeve_pct),
        "allocation_ceiling": _text(cfg.allocation_ceiling),
        "per_deal_ceiling": _text(cfg.per_deal_ceiling),
        "max_deals": cfg.max_deals,
        "deal_stop_risk": _text(cfg.deal_stop_risk),
        "aggregate_stop_risk": _text(cfg.aggregate_stop_risk),
        "trade_daily_loss": _text(cfg.trade_daily_loss),
        "trade_drawdown": _text(cfg.trade_drawdown),
        "stop_slippage": _text(cfg.stop_slippage),
        "stress_shock": _text(cfg.stress_shock),
        "stress_cap": _text(cfg.stress_cap),
        "corr_min": _text(cfg.corr_min),
        "max_alts": cfg.max_alts,
        "min_score": cfg.min_score,
        "tp_min": _text(cfg.tp_min),
        "cooldown_stop_hours": cfg.cooldown_stop_hours,
        "cooldown_tp_hours": cfg.cooldown_tp_hours,
        "review_days": cfg.review_days,
        "paper_min_days": cfg.paper_min_days,
        "paper_min_deals": cfg.paper_min_deals,
    }


def _provider_health(cfg, engine) -> dict:
    analyst = getattr(engine, "analyst", None)
    if analyst is not None and hasattr(analyst, "health"):
        health = analyst.health()
    else:
        blank = {
            "configured": False,
            "healthy": False,
            "state": "unconfigured",
            "detail": "",
            "until": None,
        }
        health = {
            "nvidia": {**blank, "model": cfg.nvidia_model},
            "openrouter": {**blank, "model": cfg.openrouter_model},
        }
    budget = getattr(getattr(analyst, "budget", None), "snapshot", None)
    if budget is None:
        health["budget"] = {
            "day": datetime.now(timezone.utc).date().isoformat(),
            "calls_used": 0,
            "calls_cap": cfg.llm_daily_calls,
            "usd_used": "0",
            "usd_cap": _text(cfg.llm_daily_usd),
        }
    else:
        try:
            health["budget"] = budget()
        except ResearchError:
            # Charging still raises. Status must not take the worker down with it.
            health["budget"] = {
                "state": "unreadable",
                "calls_cap": cfg.llm_daily_calls,
                "usd_cap": _text(cfg.llm_daily_usd),
            }
    return health


def _research_view(engine) -> dict:
    view = getattr(engine, "research_status", None)
    if not isinstance(view, dict):
        return {
            "state": "ok",
            "error_code": None,
            "last_ok_at": None,
            "last_failed_at": None,
            "next_attempt_at": None,
            "consecutive_failures": 0,
            "running": False,
        }
    return {
        "state": view.get("state") or "ok",
        "error_code": view.get("error_code"),
        "last_ok_at": view.get("last_ok_at"),
        "last_failed_at": view.get("last_failed_at"),
        "next_attempt_at": view.get("next_attempt_at"),
        "consecutive_failures": view.get("consecutive_failures") or 0,
        "running": view.get("running") is True,
    }


def _positions(engine) -> list[dict]:
    rows = []
    for row in getattr(engine, "last_positions", []) or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "symbol": row.get("pair"),
                "status": row.get("status"),
                "strategy_id": row.get("strategyId"),
                "client_id": row.get("clientId"),
                "paper": row.get("paperTrading"),
            }
        )
    return rows


def _pnl_today(journal) -> str | None:
    now = datetime.now(timezone.utc)
    # Sleeve marks and whole-account marks are different series. Use whichever was written last.
    today = [
        mark
        for mark in journal.equity_marks(scope=journal.latest_equity_scope())
        if mark.reliable and mark.marked_at.astimezone(timezone.utc).date() == now.date()
    ]
    if not today:
        return None
    return format(today[-1].equity - today[0].equity, "f")


def _template(journal) -> BotTemplate:
    raw = journal.get_setting("bot_template")
    if not raw:
        return BotTemplate()
    try:
        return BotTemplate.from_mapping(json.loads(raw))
    except (ValueError, json.JSONDecodeError):
        return BotTemplate()


def _filters(cfg, journal) -> ScreenerFilters:
    raw = journal.get_setting("screener_filters")
    if not raw:
        return ScreenerFilters.from_settings(cfg)
    try:
        return ScreenerFilters.from_mapping(json.loads(raw))
    except (ValueError, json.JSONDecodeError):
        return ScreenerFilters.from_settings(cfg)


def _card_expires_at(card: dict, now: datetime, max_age_minutes: int) -> datetime | None:
    """Return the first deadline for the model response and exchange evidence."""
    timestamps = []
    for field in ("as_of", "evidence_as_of"):
        raw = card.get(field)
        if not isinstance(raw, str) or not raw:
            return None
        try:
            timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                return None
            timestamp = timestamp.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            return None
        if (timestamp - now).total_seconds() > 60:
            return None
        timestamps.append(timestamp)
    try:
        return min(timestamps) + timedelta(minutes=max_age_minutes)
    except OverflowError:
        return datetime.max.replace(tzinfo=timezone.utc)


def _xau_block() -> dict:
    """Read the XAU snapshot. A failure becomes an unavailable block and nothing else changes."""
    try:
        from .xau.status import snapshot

        block = snapshot()
        if not isinstance(block, dict):
            return {"enabled": False, "state": "unavailable"}
        if block.get("enabled") is not True:
            return block
        # feed and basis are the live book, including before the first signal.
        live = dict(block)
        live.setdefault("live_feed", None)
        live.setdefault("last_bar_age_s", None)
        live.setdefault("basis_value", None)
        live.setdefault("basis_samples", None)
        live.setdefault("basis_reason", None)
        live.setdefault("warming", False)
        return live
    except Exception:
        return {"enabled": False, "state": "unavailable"}


def publish(cfg, journal, engine) -> None:
    status = engine.status
    decision = getattr(engine, "last_decision", None)
    # Retained cards must prove BOTH model and exchange data freshness.
    # An otherwise recent LLM answer about an older candle stays stale.
    cards = []
    now = datetime.now(timezone.utc)
    for card in getattr(engine, "last_opportunities", []) or []:
        if not isinstance(card, dict):
            continue
        expires_at = _card_expires_at(card, now, cfg.max_signal_age)
        cards.append({
            **card,
            "fresh": expires_at is not None and now <= expires_at,
            "expires_at": expires_at.isoformat() if expires_at is not None else None,
        })
    current_cards = [card for card in cards if card["fresh"]]
    discovered = list(getattr(engine, "universe", ()))
    research = _research_view(engine)
    research_failing = research["state"] in {"failed", "backoff", "capped"}
    pipeline = {
        "screening": {
            "state": "ready" if discovered and status.market_data_fresh else "blocked",
            "source": "binance_spot_public",
            "count": len(discovered),
        },
        "analysis": {
            "state": (
                "blocked" if research_failing
                else "ready" if current_cards and status.dependencies_healthy
                else "pending"
            ),
            "count": len(current_cards),
            "mode": cfg.analysis,
        },
        "risk": {
            "state": "pass" if status.risk_permitted else "blocked",
            "reason": status.reason,
        },
        "opportunities": {
            "state": "ready" if current_cards else "pending",
            "count": len(current_cards),
        },
    }
    SNAPSHOT["/api/status"] = {
        "alive": True,
        "mode": cfg.mode,
        "shared_account": bool(cfg.shared_account),
        "reason": status.reason,
        "market_data_fresh": status.market_data_fresh,
        "dependencies_healthy": status.dependencies_healthy,
        "risk_permitted": status.risk_permitted,
        "execution_permitted": status.execution_permitted,
        "opened": status.opened,
        "universe": list(getattr(engine, "universe", ())),
        "halt": engine._halted(),
        "pnl_today": _pnl_today(journal),
        "equity": None if decision is None or decision.equity is None else _text(decision.equity),
        "providers": _provider_health(cfg, engine),
        "research": research,
        "pipeline": pipeline,
        "published_at": now.isoformat(),
        "risk_decisions": journal.recent_risk_decisions(20),
        "xau": _xau_block(),
        "lanes": lanes("active" if getattr(cfg, "spot_lane_enabled", False) else "postponed"),
    }
    SNAPSHOT["/api/risk"] = {
        "reason": status.reason,
        "risk_permitted": status.risk_permitted,
        "execution_permitted": status.execution_permitted,
        "equity": None if decision is None or decision.equity is None else _text(decision.equity),
        "equity_reliable": None if decision is None else decision.equity_reliable,
    }
    SNAPSHOT["/api/positions"] = {
        "positions": _positions(engine),
        "as_of": getattr(engine, "positions_at", "") or None,
        "error": getattr(engine, "positions_error", None),
    }
    SNAPSHOT["/api/signals"] = {"decisions": journal.recent_decisions(30)}
    SNAPSHOT["/api/journal"] = {"submissions": journal.recent_submissions(30)}
    SNAPSHOT["/api/breakers"] = {
        "reason": status.reason,
        "halt": engine._halted(),
        "risk_permitted": status.risk_permitted,
    }
    SNAPSHOT["/api/alerts"] = {"alerts": journal.recent_alerts(30)}
    SNAPSHOT["/api/config"] = public_config(cfg)
    template = _template(journal)
    equity = None if decision is None else decision.equity
    SNAPSHOT["/api/opportunities"] = {"cards": cards}
    SNAPSHOT["/api/screener"] = {
        "filters": _filters(cfg, journal).to_dict(),
        "hits": [hit.to_dict() for hit in getattr(engine, "screener_hits", []) or []],
        "error": getattr(engine, "screener_error", None),
        "source": getattr(engine, "screener_source", ""),
        "fetched_at": getattr(engine, "screener_fetched_at", "") or None,
    }
    view = _accounts_view(cfg, journal, engine)
    accounts = view["accounts"]
    selected = view["selected"]
    SNAPSHOT["/api/accounts"] = view
    SNAPSHOT["/api/account-positions"] = _account_positions_view(engine)
    from .live import recent_events

    SNAPSHOT["/api/events"] = {"events": recent_events()[-20:], "as_of": now.isoformat()}
    from .live import BUS

    SNAPSHOT["/api/prices"] = {
        "symbols": BUS.prices,
        "as_of": BUS.prices_at,
        "error": BUS.prices_error or ("price_feed_unavailable" if not BUS.prices else None),
        "limit_seconds": 30,
    }
    SNAPSHOT["/api/forward"] = summary(journal)
    SNAPSHOT["/api/decisions"] = {"decisions": list(getattr(engine, "last_aegis", []) or [])}
    bots = journal.warp_bots()
    SNAPSHOT["/api/warp-bots"] = {"bots": bots, "budget": _budget_view(cfg, journal, selected, accounts)}
    SNAPSHOT["/api/bots"] = {
        "template": template.to_dict(),
        "presets": list(PRESETS),
        "preview": preview_capital(cfg, template, equity=equity),
    }
    RUNTIME["journal"] = journal
    RUNTIME["cfg"] = cfg
    RUNTIME["engine"] = engine
    from .httpapi import broadcast_live

    broadcast_live()


def publish_postponed(cfg, journal) -> None:
    """Spot lane is not running. XAU status stays live. Broker routes are not data."""
    now = datetime.now(timezone.utc)
    block = _xau_block()
    postponed = {"state": "postponed"}
    SNAPSHOT["/api/status"] = {
        "alive": True,
        "mode": cfg.mode,
        "shared_account": dict(postponed),
        "reason": "spot_postponed",
        "market_data_fresh": False,
        "dependencies_healthy": True,
        "risk_permitted": False,
        "execution_permitted": False,
        "opened": 0,
        "universe": [],
        "halt": False,
        "pnl_today": dict(postponed),
        "equity": dict(postponed),
        "providers": {"threecommas": dict(postponed)},
        "research": dict(postponed),
        "pipeline": {
            "screening": dict(postponed),
            "analysis": dict(postponed),
            "risk": dict(postponed),
            "opportunities": dict(postponed),
        },
        "published_at": now.isoformat(),
        "risk_decisions": [],
        "xau": block,
        "lanes": lanes("postponed"),
    }
    for route in _POSTPONED_ROUTES:
        SNAPSHOT[route] = dict(postponed)
    from .live import BUS, recent_events

    SNAPSHOT["/api/events"] = {"events": recent_events()[-20:], "as_of": now.isoformat()}
    SNAPSHOT["/api/prices"] = {
        "symbols": BUS.prices,
        "as_of": BUS.prices_at,
        "error": BUS.prices_error or ("price_feed_unavailable" if not BUS.prices else None),
        "limit_seconds": 30,
    }
    SNAPSHOT["/api/alerts"] = {"alerts": journal.recent_alerts(30) if journal is not None else []}
    SNAPSHOT["/api/config"] = public_config(cfg)
    SNAPSHOT["/api/breakers"] = {"reason": "spot_postponed", "halt": False, "risk_permitted": False}
    RUNTIME["journal"] = journal
    RUNTIME["cfg"] = cfg
    RUNTIME["engine"] = None
    HEALTH["xau_state"] = block.get("state") if isinstance(block, dict) else None
    from .httpapi import broadcast_live

    broadcast_live()


def _accounts_view(cfg, journal, engine) -> dict:
    accounts = []
    for row in getattr(engine, "last_accounts", []) or []:
        if hasattr(row, "to_public"):
            allowed, reason = _account_reason(cfg, row)
            accounts.append(row.to_public(selectable=allowed, reason=reason))
    _mark_assets(accounts)
    selected = _selected(journal)
    return {
        "accounts": accounts,
        "selected": selected,
        "places_orders": False,
        "as_of": getattr(engine, "accounts_at", "") or None,
        "error": getattr(engine, "accounts_error", None),
        "sleeve": _sleeve_view(cfg, accounts, selected, engine, journal),
        "cadence_seconds": ACCOUNT_SECONDS,
        "readonly": True,
    }


def _account_positions_view(engine) -> dict:
    from .book import positions_view
    from .live import BUS

    marks = BUS.fresh_marks()
    return positions_view(
        getattr(engine, "position_book", None),
        prices=marks,
        prices_fresh=bool(marks),
        error=getattr(engine, "position_book_error", None),
    )


def publish_reads(cfg, journal, engine) -> None:
    """After a read-only 3Commas refresh, push accounts and positions without waiting for the worker loop."""
    from .live import BUS

    accounts = _accounts_view(cfg, journal, engine)
    positions = _account_positions_view(engine)
    SNAPSHOT["/api/accounts"] = accounts
    SNAPSHOT["/api/account-positions"] = positions
    BUS.publish("accounts", accounts)
    BUS.publish("positions", positions)


def _mark_assets(accounts: list[dict]) -> None:
    from .live import BUS, quote_age_seconds

    marks = BUS.fresh_marks()
    age = quote_age_seconds(BUS.prices_at)
    for account in accounts:
        marked = []
        for asset in account.get("assets") or []:
            name = str(asset.get("asset") or "")
            total = asset.get("total")
            usd = None
            reason = None
            if name in ("USDT", "USDC", "USD"):
                usd = total
            else:
                price = marks.get(f"{name}USDT") or BUS.prices.get(f"{name}USDT")
                last = price.get("last") if isinstance(price, dict) else None
                if last and f"{name}USDT" in marks:
                    usd = format(Decimal(str(total)) * Decimal(str(last)), "f")
                else:
                    reason = "usd_mark_unavailable"
            row = {**asset, "usd": usd, "usd_reason": reason}
            if reason == "usd_mark_unavailable" and age is not None and last:
                row["quote_age_seconds"] = age
            marked.append(row)
        account["assets"] = marked


def _active_policy(cfg, journal):
    from .capital import Policy, apply_rule_edits

    policy = Policy.from_settings(cfg)
    if journal is None:
        return policy
    saved = journal.get_setting("trade_rules")
    if not saved:
        return policy
    return apply_rule_edits(policy, json.loads(saved))


def _sleeve_view(cfg, accounts: list[dict], selected: dict | None, engine=None, journal=None) -> dict:
    from .capital import sleeve_nav

    code = (selected or {}).get("code")
    account = next((row for row in accounts if row.get("code") == code), None)
    if account is None or account.get("error") or account.get("total") is None or account.get("free") is None:
        return {
            "eligible": None,
            "sleeve": None,
            "reason": (account or {}).get("error") or "account_unavailable",
        }
    if account.get("quote") not in ("USDT", "USDC"):
        return {"eligible": None, "sleeve": None, "reason": "quote_unavailable"}
    reserved = getattr(engine, "non_warp_future_so", None) if engine is not None else Decimal("0")
    if cfg.api_key and cfg.api_secret and reserved is None:
        return {"eligible": None, "sleeve": None, "reason": "reserves_unknown"}
    if reserved is None:
        reserved = Decimal("0")
    try:
        policy = _active_policy(cfg, journal)
        view = sleeve_nav(
            account_usdt=Decimal(str(account["total"])),
            free_usdt=Decimal(str(account["free"])),
            warp_owned=Decimal("0"),
            non_warp_spent=Decimal("0"),
            non_warp_future_so=Decimal(str(reserved)),
            policy=policy,
        )
    except (ArithmeticError, ValueError, json.JSONDecodeError) as exc:
        return {"eligible": None, "sleeve": None, "reason": str(exc)[:160]}
    return {
        "eligible": format(view.eligible, "f"),
        "sleeve": format(view.sleeve, "f"),
        "account_usdt": format(view.account_usdt, "f"),
        "reason": None,
        "basis": "min(configured sleeve, free quote minus other bots' future safety orders). Unpriced coins are not added.",
    }


def _selected(journal) -> dict | None:
    raw = journal.get_setting("target_account")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _account_reason(cfg, account) -> tuple[bool, str]:
    from .accounts import account_allowed

    return account_allowed(account, mode=cfg.mode, live_ack=cfg.live_ack)


def _budget_view(cfg, journal, selected, accounts) -> dict:
    return {
        "sleeve_usdt": _text(cfg.sleeve_usdt),
        "sleeve_pct": _text(cfg.sleeve_pct),
        "allocation_ceiling": _text(cfg.allocation_ceiling),
        "reserve_floor": _text(cfg.min_reserve),
        "per_deal_ceiling": _text(cfg.per_deal_ceiling),
        "max_deals": cfg.max_deals,
        "deal_stop_risk": _text(cfg.deal_stop_risk),
        "aggregate_stop_risk": _text(cfg.aggregate_stop_risk),
        "daily_loss_halt": _text(cfg.trade_daily_loss),
        "drawdown_halt": _text(cfg.trade_drawdown),
        "min_score": cfg.min_score,
        "review_days": cfg.review_days,
    }


def bearer_authorized(header: str | None) -> bool:
    token = os.getenv("WARP_OPERATOR_TOKEN", "")
    if len(token) < 16 or header is None:
        return False
    expected = "Bearer " + token
    if len(header) != len(expected):
        return False
    return hmac.compare_digest(header, expected)


def authorized(header: str | None, cookie_header: str | None = None) -> bool:
    if bearer_authorized(header):
        return True
    return session_valid(parse_cookies(cookie_header).get("warp_session"))


def resume_new_entries(halt_path: str | None) -> bool:
    """Remove the HALT file. This does not place an order or change the mode."""
    if not halt_path:
        return False
    path = Path(halt_path)
    if path.exists():
        path.unlink()
    return True


def pause_new_entries(halt_path: str | None) -> bool:
    """Create the HALT file. This does not close broker positions."""
    if not halt_path:
        return False
    path = Path(halt_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("pause\n", encoding="utf-8")
    return True
