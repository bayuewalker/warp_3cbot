"""Shared HTTP routes for the stdlib server and the FastAPI server."""

from __future__ import annotations

import json
import os
from pathlib import Path

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
from .operator import (
    DASHBOARD_HTML,
    HEALTH,
    ROUTES,
    RUNTIME,
    SNAPSHOT,
    authorized,
    pause_new_entries,
    resume_new_entries,
)
from .screener import ScreenerFilters

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
    if clean in ROUTES or clean in {"/api/pause", "/api/resume", "/api/screener/filters", "/api/bots/template", "/api/bots/preview"}:
        if not authorized(headers.get("Authorization"), headers.get("Cookie")):
            return _json(401, {"error": "unauthorized"})
        if method == "GET" and clean in ROUTES:
            return _json(200, SNAPSHOT.get(clean, {}))
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


def _pause() -> tuple[int, list[tuple[str, str]], bytes]:
    if not pause_new_entries(HEALTH.get("halt_path")):
        return _json(503, {"error": "halt_path_unconfigured"})
    return _json(200, {"paused": True, "closes_positions": False})


def _resume() -> tuple[int, list[tuple[str, str]], bytes]:
    if not resume_new_entries(HEALTH.get("halt_path")):
        return _json(503, {"error": "halt_path_unconfigured"})
    return _json(200, {"resumed": True, "closes_positions": False})


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
    journal, _cfg = _journal()
    if journal is None:
        return _json(503, {"error": "journal_unavailable"})
    try:
        template = BotTemplate.from_mapping(_body(body))
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid_template"})
    journal.set_setting("bot_template", json.dumps(template.to_dict()))
    return _json(200, {"saved": True, "template": template.to_dict(), "places_orders": False})


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
