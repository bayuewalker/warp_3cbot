"""Shared helpers for the P5 release-gate suites.

Everything here runs against the in-process OBSERVE worker started by account_reads.main().
No helper contacts 3Commas, Binance, TradingView, Fly or any other host. Each Recorder asserts that
every browser request stayed on the loopback worker.
"""
from __future__ import annotations

import io
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect

LOOPBACK = {"127.0.0.1", "localhost"}
# The worker's only state-changing routes. P5 adds none and the suites fail if the browser sends others.
KNOWN_POSTS = {
    "/api/login", "/api/logout", "/api/pause", "/api/resume", "/api/rules", "/api/accounts/budget",
    "/api/accounts/select", "/api/screener/filters", "/api/bots/template", "/api/bots/preview",
    "/api/paper/roundtrip", "/api/warp-bots/action",
}


def stamp(seconds: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="microseconds")


class Recorder:
    """Collects every request, page error and console error for one browser context."""

    def __init__(self, context, base: str):
        self.base = base
        self.requests: list[tuple[str, str]] = []
        self.errors: list[str] = []
        self.console: list[str] = []
        context.on("request", lambda request: self.requests.append((request.method, request.url)))
        context.on("page", self._watch)
        for page in context.pages:
            self._watch(page)

    def _watch(self, page) -> None:
        page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        page.on("console", lambda msg: self.console.append(msg.text) if msg.type == "error" else None)

    def paths(self, method: str | None = None) -> list[str]:
        out = []
        for verb, url in self.requests:
            if method is None or verb == method:
                out.append(urlparse(url).path)
        return out

    def count(self, path: str) -> int:
        return sum(1 for p in self.paths() if p == path)

    def assert_clean(self, *, allow_posts: set[str] | None = None) -> None:
        hosts = {urlparse(url).hostname for _, url in self.requests if urlparse(url).scheme in {"http", "https"}}
        assert hosts <= LOOPBACK, f"request left loopback: {sorted(h for h in hosts if h not in LOOPBACK)}"
        allowed = KNOWN_POSTS if allow_posts is None else allow_posts
        extra = [(m, p) for m, p in ((v, urlparse(u).path) for v, u in self.requests)
                 if m not in {"GET", "HEAD", "OPTIONS"} and p not in allowed]
        assert not extra, f"unexpected state-changing requests: {extra}"
        assert not self.errors, self.errors


def sign_in(context, base: str, password: str, *, lang: str = "en"):
    page = context.new_page()
    page.goto(base + "/dashboard", wait_until="domcontentloaded")
    page.locator("input[type=password], .command-identity").first.wait_for()
    if page.locator("input[type=password]").count():
        page.locator("input[type=password]").fill(password)
        page.locator("form button").last.click()
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE")
    if lang == "id":
        page.locator(".language-button").click()
        expect(page.locator("html")).to_have_attribute("lang", "id")
    return page


def overflow(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def evidence_dir() -> Path | None:
    value = os.getenv("WARP_QA_SCREENSHOTS")
    if not value:
        return None
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def capture(page, name: str, *, full_page: bool = False) -> None:
    """Save a WebP evidence screenshot when WARP_QA_SCREENSHOTS is set. Never call on a login form."""
    target = evidence_dir()
    if target is None:
        return
    from PIL import Image

    png = page.screenshot(full_page=full_page)
    Image.open(io.BytesIO(png)).convert("RGB").save(target / f"{name}.webp", "WEBP", quality=82, method=6)


def healthy_status(published: str | None = None, **extra) -> dict:
    return {
        "mode": "observe", "reason": "observe", "halt": False, "risk_permitted": True, "execution_permitted": False,
        "dependencies_healthy": True, "market_data_fresh": True, "published_at": published or stamp(),
        "providers": {"nvidia": {"state": "ok", "model": "qa-model"}}, **extra,
    }


def healthy_routes(published: str | None = None) -> dict:
    return {
        "status": healthy_status(published),
        "breakers": {"halt": False, "risk_permitted": True, "reason": "observe"},
        "risk": {"reason": "observe", "risk_permitted": True, "execution_permitted": False,
                 "equity": "1000.00", "equity_reliable": True},
    }


def put(routes: dict) -> None:
    """Publish worker routes through the worker's own snapshot store and live bus."""
    from warp3c.httpapi import broadcast_live
    from warp3c.operator import SNAPSHOT

    for name, value in routes.items():
        SNAPSHOT["/api/" + name] = value
    broadcast_live()


class SnapshotGuard:
    """Restore the worker snapshot store after a suite, so a failed case cannot leak fixtures."""

    def __enter__(self):
        from warp3c.operator import SNAPSHOT

        self.saved = dict(SNAPSHOT)
        return self

    def __exit__(self, *_exc):
        from warp3c.operator import SNAPSHOT

        SNAPSHOT.clear()
        SNAPSHOT.update(self.saved)
        return False


# ── Browser-only broker fixtures. They live in this process and are served through page.route. ──
def broker_fixtures(at: str | None = None) -> dict:
    """DEMO and REAL profiles, WARP and external bots, a grid bot with unreported fields and an unassigned row."""
    at = at or stamp()

    def account(code, name, demo, **extra):
        return {"code": code, "name": name, "demo": demo, "badge": "DEMO" if demo else "REAL", "status": "active",
                "exchange_code": "binance", "exchange_name": "Binance", "market": "spot", "quote": "USDT",
                "free": "120.5", "used": "79.5", "total": "200", "assets": [], "selectable": demo, "reason": "ok", **extra}

    def row(identifier, **extra):
        base = {"id": identifier, "account_code": "A1", "account_name": "Demo Alpha", "kind": "dca", "bot_name": "External DCA",
                "bot_code": "E1", "owner": "external", "pair": "BTC/USDT", "symbol": "BTCUSDT", "base_asset": "BTC",
                "quote_asset": "USDT", "side": "long", "status": "bought", "paper": True, "average_price": "60000",
                "size": None, "cost": None, "current_price": "61000", "current_price_source": "3commas",
                "pnl_usd": "12.3", "pnl_usd_source": "3commas", "pnl_pct": "1.5", "pnl_pct_source": "3commas",
                "safety_used": 1, "safety_max": 3, "take_profits": ["65000"], "stop_price": None,
                "opened_at": at, "stale": False}
        return {**base, **extra}

    alpha = account("A1", "Demo Alpha", True, assets=[
        {"asset": "USDT", "total": "120.5", "usd": "120.5", "usd_reason": None},
        {"asset": "SOL", "total": "2", "usd": None, "usd_reason": "usd_mark_unavailable", "quote_age_seconds": 45}])
    accounts = {"accounts": [alpha, account("R1", "Real Beta", False, free=None, total=None)], "selected": {"code": "A1"},
                "as_of": at, "error": None, "cadence_seconds": 30,
                "sleeve": {"eligible": "80", "sleeve": "100", "reason": None}, "readonly": True}
    rows = [
        row("ext-1"),
        row("ext-2", bot_name="VORTEQ-USDC-STOCKS", bot_code="V1", pair="ETH/USDT", symbol="ETHUSDT"),
        row("warp-1", bot_name="WARP-demo", bot_code="W1", owner="warp", pair="ETH/USDT", symbol="ETHUSDT", base_asset="ETH",
            size="0.5", cost="1400", current_price="3000", current_price_source="binance", pnl_usd="100",
            pnl_usd_source="computed", pnl_pct="7.14", pnl_pct_source="computed"),
        *[row(f"g{i}", kind="grid", bot_name="Grid One", bot_code="G1", side=None, average_price=None, current_price=None,
              current_price_source=None, pnl_usd=None, pnl_usd_source=None, pnl_pct=None, pnl_pct_source=None,
              safety_used=None, safety_max=None, take_profits=[]) for i in (1, 2)],
        row("real-1", account_code="R1", account_name="Real Beta", bot_name="Real DCA", bot_code="R9"),
        row("loose-1", account_code=None, account_name=None, bot_name="Orphan", bot_code="O1"),
    ]
    tally: dict[str, int] = {}
    for item in rows:
        tally[item["account_code"] or "unknown"] = tally.get(item["account_code"] or "unknown", 0) + 1
    book = {"positions": rows, "by_account": tally, "as_of": at, "error": None,
            "errors": [f"grid G{n}: incomplete position rows" for n in range(1, 3)], "bots_checked": 4,
            "stale": False, "cadence_seconds": 60, "readonly": True}
    warp_bot = {"warp_id": "demo1", "name": "WARP-demo1", "external_code": "W1", "kind": "dca", "product": "dca_bot",
                "account_code": "A1", "market": "spot", "symbol": "ETHUSDT", "status": "active", "commitment": "300",
                "created_at": at}
    bots = {"bots": [warp_bot], "budget": {"sleeve_usdt": "500", "sleeve_pct": "0.10", "max_deals": 3}}
    return {"/api/accounts": accounts, "/api/account-positions": book, "/api/warp-bots": bots}


def timeline_routes() -> dict:
    long_id = "warp-client-" + "x" * 90
    return {
        **healthy_routes(),
        "events": {"as_of": stamp(), "events": [
            {"ts": stamp(-60), "event": "worker_started", "mode": "observe"},
            {"ts": stamp(-50), "event": "zz_never_seen_kind", "reason": "because_reasons"},
            {"ts": stamp(-40), "event": "strategy_confirmed", "symbol": "BTCUSDT", "strategy_id": "strat-777"},
            {"ts": "not-a-time", "event": "bad_time_event"},
        ]},
        "alerts": {"alerts": [
            {"id": 12, "event": "worker_stalled", "state": "pending", "attempts": 3,
             "last_error": "HTTP 500 from webhook", "created_at": stamp(-30), "delivered_at": None},
            {"id": 11, "event": "halt", "state": "delivered", "attempts": 1, "last_error": None,
             "created_at": stamp(-300), "delivered_at": stamp(-299)},
        ]},
        "journal": {"submissions": [{"client_id": long_id, "symbol": "ETHUSDT", "state": "unknown",
                                     "strategy_id": None, "created_at": "2026-11-24 09:59:00"}]},
        "signals": {"decisions": [{"recorded_at": stamp(-20), "symbol": "*", "stage": "universe",
                                   "reason": "no_eligible_market", "detail": "fail_closed"}]},
    }


def serve_fixtures(page, base: str, fixtures: dict) -> dict:
    """Serve GET fixtures for the broker routes. A non-GET to these paths is a test failure."""
    def respond(route):
        assert route.request.method == "GET", route.request.method
        value = fixtures[route.request.url.removeprefix(base).split("?")[0]]
        if isinstance(value, int):
            route.fulfill(status=value, json={"error": "fixture"})
        else:
            route.fulfill(json=value)

    for path in fixtures:
        page.route("**" + path, respond)
    return fixtures


def wait_subscribers(count: int, timeout: float = 8.0) -> None:
    """The bus only notices a closed browser stream on its next write, so nudge it until it lets go."""
    from warp3c.live import BUS

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        BUS.publish("heartbeat", {"ts": stamp()})
        if len(BUS._subs) <= count:
            return
        time.sleep(0.2)
    raise AssertionError(f"{len(BUS._subs)} stream subscribers remain, wanted <= {count}")
