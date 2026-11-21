"""Shared HTTP routes for the stdlib server and the FastAPI server."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .accounts import account_allowed
from .auth import (
    cookie_header,
    issue_session,
    login_blocked,
    note_login_failure,
    note_login_success,
    password_configured,
    password_matches,
)
from .bots import BotTemplate, preview_capital
from .models import positive
from .operator import (
    DASHBOARD_HTML,
    HEALTH,
    ROUTES,
    RUNTIME,
    SNAPSHOT,
    authorized,
    pause_new_entries,
    publish,
    resume_new_entries,
)
from .ownership import ACTIONS, NotOwned, apply_action
from .papercheck import run_roundtrip
from .screener import ScreenerFilters
from .threecommas import IntegrationError

_ACTIONS = {
    "/api/pause",
    "/api/resume",
    "/api/screener/filters",
    "/api/bots/template",
    "/api/bots/preview",
    "/api/accounts/select",
    "/api/accounts/budget",
    "/api/warp-bots/action",
    "/api/paper/roundtrip",
    "/api/rules",
}

_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}


def web_root() -> Path:
    """Built dashboard. Docker sets WARP_WEB_DIST because pip install moves this module."""
    configured = os.getenv("WARP_WEB_DIST", "").strip()
    if configured:
        return Path(configured)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "web" / "dist"
        if (candidate / "index.html").is_file():
            return candidate
    return here.parents[2] / "web" / "dist"


def dispatch(method: str, path: str, headers, body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    clean = path.split("?", 1)[0]
    if method == "GET" and clean in {"/health", "/live", "/ready"}:
        return _health(clean)
    if method == "GET" and clean in {"/", "/dashboard"}:
        return _page()
    if method == "GET" and clean.startswith("/assets/"):
        return _asset(clean)
    if method == "GET" and clean == "/api/session":
        return _json(
            200,
            {
                "authenticated": authorized(headers.get("Authorization"), headers.get("Cookie")),
                "password_login": password_configured(),
            },
        )
    if method == "POST" and clean == "/api/login":
        return _login(body)
    if method == "POST" and clean == "/api/logout":
        return _json(200, {"ok": True}, extra=[("Set-Cookie", cookie_header("", clear=True))])
    if clean in ROUTES or clean in _ACTIONS:
        if not authorized(headers.get("Authorization"), headers.get("Cookie")):
            return _json(401, {"error": "unauthorized"})
        if method == "GET" and clean == "/api/accounts":
            _refresh_accounts()
            return _json(200, SNAPSHOT.get(clean, {"accounts": [], "selected": None, "places_orders": False}))
        if method == "GET" and clean in ROUTES:
            payload = SNAPSHOT.get(clean, {})
            if clean in {"/api/status", "/api/breakers"}:
                payload = _authoritative_halt(payload)
            return _json(200, payload)
        if method == "POST" and clean == "/api/pause":
            return _pause()
        if method == "POST" and clean == "/api/resume":
            return _resume()
        if method == "POST" and clean == "/api/screener/filters":
            return _save_filters(body)
        if method == "POST" and clean == "/api/bots/template":
            return _save_template(body)
        if method == "POST" and clean == "/api/bots/preview":
            return _preview(body)
        if method == "POST" and clean == "/api/accounts/select":
            return _select_account(body)
        if method == "POST" and clean == "/api/accounts/budget":
            return _save_budget(body)
        if method == "POST" and clean == "/api/warp-bots/action":
            return _bot_action(body)
        if method == "GET" and clean == "/api/rules":
            return _rules()
        if method == "POST" and clean == "/api/rules":
            return _save_rules(body)
        if method == "POST" and clean == "/api/paper/roundtrip":
            return _paper_roundtrip(body)
    return _json(404, {"error": "not_found"})


def _health(path: str) -> tuple[int, list[tuple[str, str]], bytes]:
    if path == "/ready":
        code = 200 if HEALTH.get("dependencies_healthy") and HEALTH.get("last_cycle_ok") else 503
    else:
        code = 200
    return _json(code, HEALTH)


def _page() -> tuple[int, list[tuple[str, str]], bytes]:
    index = web_root() / "index.html"
    if index.is_file():
        return 200, [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")], index.read_bytes()
    body = DASHBOARD_HTML.encode()
    return 200, [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")], body


def _asset(path: str) -> tuple[int, list[tuple[str, str]], bytes]:
    root = web_root().resolve()
    relative = path.removeprefix("/assets/")
    candidate = (root / "assets" / relative).resolve()
    if not _inside(root, candidate) or not candidate.is_file():
        return _json(404, {"error": "not_found"})
    kind = _TYPES.get(candidate.suffix, "application/octet-stream")
    return 200, [("Content-Type", kind), ("Cache-Control", "public, max-age=86400")], candidate.read_bytes()


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _login(body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    if login_blocked():
        return _json(429, {"error": "too_many_attempts"})
    if not password_configured():
        return _json(503, {"error": "password_not_configured"})
    try:
        payload = _body(body)
    except ValueError:
        return _json(400, {"error": "invalid_json"})
    if not password_matches(str(payload.get("password", ""))):
        note_login_failure()
        return _json(401, {"error": "unauthorized"})
    note_login_success()
    return _json(200, {"ok": True}, extra=[("Set-Cookie", cookie_header(issue_session()))])


def _file_halt() -> bool | None:
    """The HALT file is the live entry gate. A missing path means it cannot be known."""
    path = HEALTH.get("halt_path")
    if not isinstance(path, str) or not path:
        return None
    return Path(path).is_file()


def _published_under_halt(payload: dict) -> bool:
    if payload.get("halt") is True or payload.get("reason") == "halt":
        return True
    pipeline = payload.get("pipeline")
    risk = pipeline.get("risk") if isinstance(pipeline, dict) else None
    return isinstance(risk, dict) and risk.get("reason") == "halt"


def _authoritative_halt(payload: object) -> object:
    """Serve the HALT file on top of the last worker snapshot.

    publish() refreshes the snapshot on a later cycle. Pause and resume must
    not wait for that cycle before the dashboard tells the truth. Removing the
    file does not reopen entries: a snapshot taken under HALT stays blocked
    until the worker publishes a new evaluation.
    """
    halted = _file_halt()
    if halted is None or not isinstance(payload, dict) or not payload:
        return payload
    view = dict(payload)
    view["halt"] = halted
    if halted:
        view["execution_permitted"] = False
        view["gating"] = "halt"
        _set_risk(view, state="blocked", reason="halt")
        return view
    if _published_under_halt(payload):
        view["gating"] = "awaiting_worker"
        if view.get("reason") == "halt":
            view["reason"] = "awaiting_worker"
        _set_risk(view, reason="awaiting_worker")
    else:
        view["gating"] = "current"
    return view


def _set_risk(view: dict, *, state: str | None = None, reason: str | None = None) -> None:
    pipeline = view.get("pipeline")
    if not isinstance(pipeline, dict):
        return
    pipeline = dict(pipeline)
    risk = dict(pipeline.get("risk") or {})
    if state is not None:
        risk["state"] = state
    if reason is not None:
        risk["reason"] = reason
    pipeline["risk"] = risk
    view["pipeline"] = pipeline


def broadcast_live(routes: dict | None = None) -> None:
    """Push dashboard routes with the HALT file applied, same as GET /api/status."""
    from .live import BUS
    from .operator import ROUTES, SNAPSHOT

    chosen = dict(routes) if routes is not None else {key: SNAPSHOT[key] for key in ROUTES if key in SNAPSHOT}
    for key in ("/api/status", "/api/breakers"):
        if isinstance(chosen.get(key), dict):
            chosen[key] = _authoritative_halt(chosen[key])
    BUS.emit_snapshot(chosen)


def _pause() -> tuple[int, list[tuple[str, str]], bytes]:
    if not pause_new_entries(HEALTH.get("halt_path")):
        return _json(503, {"error": "halt_path_unconfigured"})
    broadcast_live()
    return _json(200, {"paused": True, "closes_positions": False, "halt": True})


def _resume() -> tuple[int, list[tuple[str, str]], bytes]:
    if not resume_new_entries(HEALTH.get("halt_path")):
        return _json(503, {"error": "halt_path_unconfigured"})
    broadcast_live()
    return _json(200, {"resumed": True, "closes_positions": False, "halt": False})


def _journal():
    journal = RUNTIME.get("journal")
    cfg = RUNTIME.get("cfg")
    if journal is None or cfg is None:
        return None, None
    return journal, cfg


def _save_filters(body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    journal, _cfg = _journal()
    if journal is None:
        return _json(503, {"error": "journal_unavailable"})
    try:
        filters = ScreenerFilters.from_mapping(_body(body))
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid_filters"})
    journal.set_setting("screener_filters", json.dumps(filters.to_dict()))
    return _json(200, {"saved": True, "filters": filters.to_dict()})


def _save_template(body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    journal, cfg = _journal()
    if journal is None or cfg is None:
        return _json(503, {"error": "journal_unavailable"})
    try:
        template = BotTemplate.from_mapping(_body(body))
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid_template"})
    journal.set_setting("bot_template", json.dumps(template.to_dict()))
    return _json(200, {"saved": True, "template": template.to_dict(), "places_orders": False})


def _refresh_accounts() -> None:
    cfg = RUNTIME.get("cfg")
    engine = RUNTIME.get("engine")
    journal = RUNTIME.get("journal")
    if cfg is None or engine is None or journal is None:
        return
    broker = getattr(engine, "broker", None)
    if broker is None or not getattr(cfg, "api_key", "") or not hasattr(broker, "list_accounts"):
        return
    try:
        engine.last_accounts = broker.list_accounts()
    except IntegrationError:
        return
    publish(cfg, journal, engine)


def _select_account(body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    journal, cfg = _journal()
    engine = RUNTIME.get("engine")
    if journal is None or cfg is None or engine is None:
        return _json(503, {"error": "journal_unavailable"})
    try:
        payload = _body(body)
        code = str(payload.get("code") or "")
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid_json"})
    _refresh_accounts()
    match = [row for row in getattr(engine, "last_accounts", []) if getattr(row, "code", None) == code]
    if len(match) != 1:
        return _json(404, {"error": "account_not_found", "places_orders": False})
    account = match[0]
    allowed, reason = account_allowed(account, mode=cfg.mode, live_ack=cfg.live_ack)
    if not allowed:
        return _json(403, {"error": reason, "places_orders": False})
    journal.set_setting(
        "target_account",
        json.dumps(
            {
                "code": account.code,
                "name": account.name,
                "market": account.market,
                "exchange_code": account.exchange_code,
                "demo": account.demo,
            }
        ),
    )
    publish(cfg, journal, engine)
    return _json(200, {"saved": True, "places_orders": False, "reason": reason, "account": account.to_public(selectable=True, reason=reason)})


def _save_budget(body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    journal, cfg = _journal()
    if journal is None or cfg is None:
        return _json(503, {"error": "journal_unavailable"})
    try:
        payload = _body(body)
        code = str(payload.get("code") or "")
        absolute = positive(payload.get("budget_usdt"), "budget", allow_zero=False)
        fraction = positive(payload.get("budget_fraction"), "budget fraction", allow_zero=False)
        max_bots = payload.get("max_bots")
        if fraction > 1 or type(max_bots) is not int or max_bots < 1 or len(code) < 4:
            raise ValueError("invalid budget")
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid_budget", "places_orders": False})
    current = {}
    raw = journal.get_setting("account_budgets")
    if raw:
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            current = loaded
    current[code] = {
        "budget_usdt": format(absolute, "f"),
        "budget_fraction": format(fraction, "f"),
        "max_bots": max_bots,
    }
    journal.set_setting("account_budgets", json.dumps(current))
    engine = RUNTIME.get("engine")
    if engine is not None:
        publish(cfg, journal, engine)
    return _json(200, {"saved": True, "places_orders": False, "code": code})


def _bot_action(body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    journal, cfg = _journal()
    engine = RUNTIME.get("engine")
    if journal is None or cfg is None or engine is None:
        return _json(503, {"error": "journal_unavailable"})
    try:
        payload = _body(body)
    except ValueError:
        return _json(400, {"error": "invalid_json"})
    action = str(payload.get("action") or "")
    warp_id = str(payload.get("warp_id") or "")
    expected = {"pause": "PAUSE", "resume": "RESUME", "close": "CLOSE", "delete": "DELETE"}.get(action)
    if action not in ACTIONS or payload.get("confirm") != expected:
        return _json(400, {"error": "confirmation_required", "places_orders": False})
    if action == "resume" and PathHalt(cfg):
        return _json(409, {"error": "halt", "places_orders": False})
    try:
        result = apply_action(engine.broker, journal, warp_id, action)
    except NotOwned as exc:
        return _json(403, {"error": exc.reason, "places_orders": False})
    except IntegrationError:
        return _json(502, {"error": "broker_error", "places_orders": False})
    publish(cfg, journal, engine)
    return _json(200, {"ok": True, "places_orders": False, **result})


def PathHalt(cfg) -> bool:
    from pathlib import Path

    return Path(cfg.db_path).parent.joinpath("HALT").exists()


def _rules() -> tuple[int, list[tuple[str, str]], bytes]:
    from .capital import Policy, apply_rule_edits, rules_document

    journal, cfg = _journal()
    if journal is None or cfg is None:
        return _json(503, {"error": "journal_unavailable"})
    policy = Policy.from_settings(cfg)
    raw = journal.get_setting("trade_rules", request_local=True)
    if raw:
        try:
            policy = apply_rule_edits(policy, json.loads(raw))
        except (ValueError, json.JSONDecodeError):
            return _json(200, {"values": {}, "error": "invalid_rules", "places_orders": False})
    return _json(200, rules_document(policy))


def _save_rules(body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    from .capital import Policy, apply_rule_edits, rules_document

    journal, cfg = _journal()
    if journal is None or cfg is None:
        return _json(503, {"error": "journal_unavailable"})
    try:
        payload = _body(body)
        policy = apply_rule_edits(Policy.from_settings(cfg), payload)
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid_rules", "places_orders": False})
    journal.set_setting("trade_rules", json.dumps(rules_document(policy)["values"]))
    return _json(200, {"saved": True, "places_orders": False, "values": rules_document(policy)["values"]})


def _paper_roundtrip(body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    journal, cfg = _journal()
    engine = RUNTIME.get("engine")
    if journal is None or cfg is None or engine is None:
        return _json(503, {"error": "journal_unavailable"})
    try:
        payload = _body(body)
    except ValueError:
        return _json(400, {"error": "invalid_json"})
    if payload.get("confirm") != "PAPERCHECK":
        return _json(400, {"error": "confirmation_required", "places_orders": False})
    report = run_roundtrip(cfg, engine.broker, journal)
    publish(cfg, journal, engine)
    code = 200 if report["ok"] else 409
    return _json(code, report)


def _preview(body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    _journal_obj, cfg = _journal()
    if cfg is None:
        return _json(503, {"error": "journal_unavailable"})
    try:
        template = BotTemplate.from_mapping(_body(body))
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid_template"})
    equity = SNAPSHOT.get("/api/risk", {}).get("equity")
    from decimal import Decimal

    marked = Decimal(equity) if equity else None
    return _json(200, {"preview": preview_capital(cfg, template, equity=marked), "places_orders": False})


def _body(body: bytes) -> dict:
    if len(body) > 100_000:
        raise ValueError("Body too large")
    try:
        payload = json.loads(body.decode() or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _json(code: int, payload: dict, extra: list[tuple[str, str]] | None = None):
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    headers = [("Content-Type", "application/json"), ("Cache-Control", "no-store")]
    if extra:
        headers.extend(extra)
    return code, headers, raw
