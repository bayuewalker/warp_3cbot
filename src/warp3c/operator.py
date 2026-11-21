"""Read-only operator snapshot. Trading controls are pause-only and authenticated."""

from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .auth import parse_cookies, session_valid
from .bots import PRESETS, BotTemplate, preview_capital
from .forward import summary
from .screener import ScreenerFilters

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
)


def _text(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def public_config(cfg) -> dict:
    """Numbers and modes only. Secrets and webhook URLs stay out."""
    return {
        "mode": cfg.mode,
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
    today = [
        mark
        for mark in journal.equity_marks()
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


def publish(cfg, journal, engine) -> None:
    status = engine.status
    decision = getattr(engine, "last_decision", None)
    SNAPSHOT["/api/status"] = {
        "alive": True,
        "mode": cfg.mode,
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
    }
    SNAPSHOT["/api/risk"] = {
        "reason": status.reason,
        "risk_permitted": status.risk_permitted,
        "execution_permitted": status.execution_permitted,
        "equity": None if decision is None or decision.equity is None else _text(decision.equity),
        "equity_reliable": None if decision is None else decision.equity_reliable,
    }
    SNAPSHOT["/api/positions"] = {"positions": _positions(engine)}
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
    SNAPSHOT["/api/opportunities"] = {"cards": list(getattr(engine, "last_opportunities", []) or [])}
    SNAPSHOT["/api/screener"] = {
        "filters": _filters(cfg, journal).to_dict(),
        "hits": [hit.to_dict() for hit in getattr(engine, "screener_hits", []) or []],
        "error": getattr(engine, "screener_error", None),
        "source": getattr(engine, "screener_source", ""),
    }
    accounts = []
    for row in getattr(engine, "last_accounts", []) or []:
        if hasattr(row, "to_public"):
            allowed, reason = _account_reason(cfg, row)
            accounts.append(row.to_public(selectable=allowed, reason=reason))
    selected = _selected(journal)
    SNAPSHOT["/api/accounts"] = {"accounts": accounts, "selected": selected, "places_orders": False}
    SNAPSHOT["/api/forward"] = summary(journal)
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
