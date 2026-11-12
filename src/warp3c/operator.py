"""Read-only operator snapshot. Trading controls are pause-only and authenticated."""

from __future__ import annotations

import hmac
import os
from decimal import Decimal
from pathlib import Path

SNAPSHOT: dict[str, dict] = {}
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


def authorized(header: str | None) -> bool:
    token = os.getenv("WARP_OPERATOR_TOKEN", "")
    if len(token) < 16 or header is None:
        return False
    expected = "Bearer " + token
    if len(header) != len(expected):
        return False
    return hmac.compare_digest(header, expected)


def pause_new_entries(halt_path: str | None) -> bool:
    """Create the HALT file. This does not close broker positions."""
    if not halt_path:
        return False
    path = Path(halt_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("pause\n", encoding="utf-8")
    return True
