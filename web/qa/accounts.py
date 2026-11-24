"""P4B accounts, capital, bots and position-provenance checks on a credential-free local OBSERVE worker.

Every broker payload is a route-fulfilled fixture that exists only in this process. The page never
reaches 3Commas. Set WARP_QA_SCREENSHOTS to capture the labelled fixtures at 320, 390 and 1280 px.
"""
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from account_reads import main
from playwright.sync_api import expect

NOT_REPORTED = {"en": "Not reported", "id": "Belum dilaporkan"}


def verify(browser, base, password):
    context = browser.new_context(timezone_id="Asia/Jakarta", reduced_motion="reduce")
    assert context.request.post(base + "/api/login", data={"password": password}).ok
    page = context.new_page()
    writes, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: writes.append((request.method, request.url)) if request.method != "GET" else None)
    status = context.request.get(base + "/api/status").json()
    assert status["mode"] == "observe"
    now = datetime.now(timezone.utc)
    page.clock.install(time=now)
    at = now.isoformat()

    def account(code, name, demo, **extra):
        return {"code": code, "name": name, "demo": demo, "badge": "DEMO" if demo else "REAL", "status": "active",
                "exchange_code": "binance", "exchange_name": "Binance", "market": "spot", "quote": "USDT",
                "free": "120.5", "used": "79.5", "total": "200", "assets": [], "selectable": demo, "reason": "ok", **extra}

    alpha = account("A1", "Demo Alpha", True, assets=[
        {"asset": "USDT", "total": "120.5", "usd": "120.5", "usd_reason": None},
        {"asset": "ETH", "total": "0.5", "usd": "1500", "usd_reason": None},
        {"asset": "SOL", "total": "2", "usd": None, "usd_reason": "usd_mark_unavailable", "quote_age_seconds": 45},
    ])
    beta = account("R1", "Real Beta", False)
    accounts = {"accounts": [alpha, beta], "selected": {"code": "A1"}, "as_of": at, "error": None, "cadence_seconds": 30,
                "sleeve": {"eligible": "80", "sleeve": "100", "reason": None}, "readonly": True}

    def row(identifier, **extra):
        base = {"id": identifier, "account_code": "A1", "account_name": "Demo Alpha", "kind": "dca", "bot_name": "External DCA",
                "bot_code": "E1", "owner": "external", "pair": "BTC/USDT", "symbol": "BTCUSDT", "base_asset": "BTC",
                "quote_asset": "USDT", "side": "long", "status": "bought", "paper": True, "average_price": "60000",
                "size": None, "cost": None, "current_price": "61000", "current_price_source": "3commas",
                "pnl_usd": "12.3", "pnl_usd_source": "3commas", "pnl_pct": "1.5", "pnl_pct_source": "3commas",
                "safety_used": 1, "safety_max": 3, "take_profits": ["65000"], "stop_price": None,
                "opened_at": at, "stale": False}
        return {**base, **extra}

    external = row("ext-1")
    warp = row("warp-1", bot_name="WARP-demo", bot_code="W1", owner="warp", pair="ETH/USDT", symbol="ETHUSDT",
               base_asset="ETH", size="0.5", cost="1400", current_price="3000", current_price_source="binance",
               pnl_usd="100", pnl_usd_source="computed", pnl_pct="7.14", pnl_pct_source="computed")
    grid = [row(f"g{i}", kind="grid", bot_name="Grid One", bot_code="G1", side=None, average_price=None, current_price=None,
                current_price_source=None, pnl_usd=None, pnl_usd_source=None, pnl_pct=None, pnl_pct_source=None,
                safety_used=None, safety_max=None, take_profits=[]) for i in (1, 2)]
    real_row = row("real-1", account_code="R1", account_name="Real Beta", bot_name="Real DCA", bot_code="R9")
    loose = row("loose-1", account_code=None, account_name=None, bot_name="Orphan", bot_code="O1")

    def book(rows, **extra):
        tally = {}
        for item in rows:
            key = item["account_code"] or "unknown"
            tally[key] = tally.get(key, 0) + 1
        return {"positions": rows, "by_account": tally, "as_of": at, "error": None, "errors": [], "bots_checked": 4,
                "stale": False, "cadence_seconds": 60, "readonly": True, **extra}

    warp_bot = {"warp_id": "demo1", "name": "WARP-demo1", "external_code": "W1", "kind": "dca", "product": "dca_bot",
                "account_code": "A1", "market": "spot", "symbol": "ETHUSDT", "status": "active", "commitment": "300",
                "created_at": at}
    bots = {"bots": [warp_bot], "budget": {"sleeve_usdt": "500", "sleeve_pct": "0.10", "max_deals": 3}}
    observed = {"/api/accounts": accounts, "/api/account-positions": book([external, warp, real_row, loose]),
                "/api/warp-bots": bots}

    def respond(route):
        assert route.request.method == "GET"
        value = observed[route.request.url.removeprefix(base).split("?")[0]]
        if isinstance(value, int):
            route.fulfill(status=value, json={"error": "fixture"})
        else:
            route.fulfill(json=value)

    page.route("**/api/stream", lambda route: route.abort())
    for path in observed:
        page.route("**" + path, respond)
    page.set_default_timeout(5000)
    page.set_viewport_size({"width": 390, "height": 844})

    def open_venues(**changes):
        observed.update(changes)
        page.goto(base + "/dashboard#preferences", wait_until="domcontentloaded")
        page.reload(wait_until="domcontentloaded")
        page.locator(".account-picker").wait_for()

    picker = page.locator(".account-picker")
    cards = page.locator(".position-card")
    flags = page.locator(".p4b-flags")
    unassigned = picker.get_by_role("button", name="Account not reported", exact=False)
    open_venues()

    # Picker: DEMO and REAL badges, market, quote and account-scoped counts from by_account.
    expect(picker.locator(".choice").nth(0).locator(".badge")).to_have_text("DEMO")
    expect(picker.locator(".choice").nth(1).locator(".badge")).to_have_text("REAL")
    expect(picker).to_contain_text("Binance · Spot · active · USDT · Open positions · 2")
    expect(picker).to_contain_text("Open positions · 1")
    expect(unassigned).to_be_visible()
    expect(cards).to_have_count(2)
    expect(cards.filter(has_text="External DCA")).to_contain_text("Read-only · external")
    expect(cards.filter(has_text="WARP-demo")).not_to_contain_text("Read-only")
    # Fields the contract does not report, or that 3Commas left null, are never zero and never full size.
    external_card = cards.filter(has_text="External DCA")
    expect(external_card.locator(".kv > div", has_text="Open size")).to_contain_text("Not reported")
    expect(external_card.locator(".kv > div", has_text="Cost")).to_contain_text("Not reported")
    expect(external_card.locator(".kv > div", has_text="Sold size")).to_contain_text("Not reported")
    expect(external_card.locator(".kv > div", has_text="Stop loss")).to_contain_text("Not reported")
    expect(external_card.locator(".position-pnl")).to_contain_text("3Commas")
    expect(external_card.locator(".position-pnl")).not_to_contain_text("Derived")
    computed_card = cards.filter(has_text="WARP-demo")
    expect(computed_card.locator(".position-pnl")).to_contain_text("Derived (computed)")
    expect(computed_card.locator(".kv > div", has_text="Current price")).to_contain_text("Binance mark")
    expect(computed_card.locator(".kv > div", has_text="Current price")).to_contain_text("source time not reported")
    expect(computed_card.locator(".kv > div", has_text="Open size")).to_contain_text("0.5 ETH")
    # Capital strip: reported, configured, derived and absent fields each say which.
    capital = page.locator(".p4b-capital")
    expect(capital.locator(".p4b-cell", has_text="Total")).to_contain_text("200.00 USDT")
    expect(capital.locator(".p4b-cell", has_text="Total")).to_contain_text("3Commas")
    expect(capital.locator(".p4b-cell", has_text="Configured USDT sleeve")).to_contain_text("500.00 USDT")
    expect(capital.locator(".p4b-cell", has_text="Configured USDT sleeve")).to_contain_text("Worker")
    expect(capital.locator(".p4b-cell", has_text="Open WARP bots")).to_contain_text("1")
    expect(capital.locator(".p4b-cell", has_text="Open WARP bots")).to_contain_text("Derived")
    expect(capital.locator(".p4b-cell", has_text="Max WARP bots").locator("dd")).to_have_text("Not reported")
    expect(capital.locator(".p4b-cell", has_text="Max WARP bots")).to_contain_text("not published by the worker")
    expect(capital.locator(".p4b-cell", has_text="Used by WARP").locator("dd")).to_have_text("Not reported")
    expect(capital.locator(".p4b-cell", has_text="Remaining").locator("dd")).to_have_text("Not reported")
    sol = page.locator(".p4b-asset", has_text="SOL")
    expect(sol).to_contain_text("Mark age 45s")
    expect(sol).to_contain_text("Mark stale")
    expect(page.locator(".p4b-asset", has_text="ETH")).to_contain_text("Derived: reported balance")
    expect(flags).to_contain_text("Mark stale")
    expect(flags).not_to_contain_text("Broker book stale")
    expect(flags).not_to_contain_text("Account stale")
    expect(page.get_by_role("alert")).to_have_count(0)

    # Account switch filters the book. Real Beta has one external row and no WARP capital reuse.
    picker.get_by_role("button", name="Real Beta", exact=False).click()
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text("Real DCA")
    expect(page.locator(".account-card .badge", has_text="REAL").first).to_be_visible()
    expect(page.locator(".p4b-cell", has_text="Budget WARP can use").locator("dd")).to_have_text("Not reported")
    expect(page.locator(".p4b-cell", has_text="Budget WARP can use")).to_contain_text("only for the selected account")
    unassigned.click()
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text("Orphan")
    expect(page.locator(".account-card")).to_have_count(0)
    picker.get_by_role("button", name="Demo Alpha", exact=False).click()
    expect(cards).to_have_count(2)

    # Bots reporting positions: the external bot is read-only and no control can act on it.
    external_bot = page.locator(".p4b-bots li", has_text="External DCA")
    expect(external_bot).to_contain_text("Read-only · external")
    expect(external_bot.locator("button, a, input")).to_have_count(0)
    expect(page.get_by_role("button", name=re.compile(r"^(Pause|Resume|Close|Delete)$"))).to_have_count(0)

    # An incomplete grid bot is partial with unreported fields, its error is listed, and counts are withheld.
    open_venues(**{"/api/account-positions": book([external, *grid], errors=["grid G1: incomplete position rows"])})
    errors_box = page.locator(".p4b-errors")
    expect(errors_box).to_be_visible()
    expect(errors_box).to_contain_text("Incomplete position rows")
    expect(errors_box.locator("code")).to_have_text("grid G1: incomplete position rows")
    expect(picker).to_contain_text("Open positions · Unavailable")
    expect(page.get_by_text("No open positions on this account.")).to_have_count(0)
    grid_cards = cards.filter(has_text="Grid One")
    expect(grid_cards).to_have_count(2)
    expect(grid_cards.first).to_contain_text("Incomplete rows")
    for label in ("Average entry", "Current price", "Open size", "Cost", "Stop loss", "Take profit", "Safety orders"):
        expect(grid_cards.first.locator(".kv > div", has_text=label).locator("dd")).to_have_text(re.compile(r"^Not reported"))
    expect(grid_cards.first.locator(".position-pnl b")).to_have_text("Not reported")
    expect(cards.filter(has_text="External DCA")).not_to_contain_text("Incomplete rows")
    expect(page.locator(".p4b-bots li", has_text="Grid One")).to_contain_text("2 rows reported")
    expect(page.locator(".p4b-bots li", has_text="Grid One")).to_contain_text("Incomplete rows")

    # Broker, account and mark stale are three separate labelled conditions.
    open_venues(**{"/api/account-positions": book([external], stale=True, error="strategies_stale",
                                                      errors=["strategies: HTTP 429"])})
    expect(flags).to_contain_text("Broker book stale")
    expect(flags).not_to_contain_text("Account stale")
    expect(cards).to_have_count(1)
    open_venues(**{"/api/account-positions": book([external]), "/api/accounts": {**accounts, "error": "HTTP 429"}})
    expect(flags).to_contain_text("Account stale")
    expect(flags).not_to_contain_text("Broker book stale")
    expect(unassigned).to_be_visible()
    expect(cards).to_have_count(1)

    # A failed accounts read keeps every loaded position under Unassigned.
    open_venues(**{"/api/account-positions": book([external, warp]), "/api/accounts": 500})
    expect(unassigned).to_be_visible()
    expect(cards).to_have_count(2)
    expect(page.get_by_role("alert")).to_have_count(1)
    expect(page.locator(".account-card")).to_have_count(0)

    # Confirmed empty is not the same as unavailable.
    open_venues(**{"/api/account-positions": book([]), "/api/accounts": accounts})
    expect(page.get_by_text("No open positions on this account.")).to_be_visible()
    expect(picker).to_contain_text("Open positions · 0")

    # OBSERVE: WARP bot actions are not rendered, and the owner is told why.
    observed["/api/account-positions"] = book([external, warp, real_row, loose])
    page.goto(base + "/dashboard#assets", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".sections button").first).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_text("WARP-demo1")).to_be_visible()
    expect(page.locator(".action-grid")).to_have_count(0)
    expect(page.get_by_role("button", name=re.compile(r"^(Pause|Resume|Close|Delete)$"))).to_have_count(0)
    expect(page.get_by_text("Bot actions are available only in paper mode")).to_be_visible()

    # Responsive evidence in both languages. The rich fixture shows every provenance label at once.
    open_venues(**{"/api/account-positions": book([external, warp, *grid, real_row, loose],
                                                  errors=["grid G1: incomplete position rows"]),
                   "/api/accounts": accounts})
    output = os.getenv("WARP_QA_SCREENSHOTS")
    for lang, width in (("en", 320), ("en", 390), ("en", 1280), ("id", 320), ("id", 390), ("id", 1280)):
        page.set_viewport_size({"width": width, "height": 960 if width > 600 else 844})
        if lang == "id" and width == 320:
            page.locator(".language-button").click()
        en = lang == "en"
        expect(picker.locator(".badge").first).to_have_text("DEMO")
        expect(page.locator(".p4b-capital .p4b-cell", has_text="Max WARP bots" if en else "Maks bot WARP").locator("dd")
               ).to_have_text(NOT_REPORTED[lang])
        expect(page.locator(".p4b-errors")).to_contain_text("grid G1: incomplete position rows")
        expect(cards.filter(has_text="Grid One").first).to_contain_text("Incomplete rows" if en else "Baris tidak lengkap")
        expect(cards.filter(has_text="External DCA")).to_contain_text("Read-only · external" if en else "Hanya baca · eksternal")
        expect(cards.filter(has_text="WARP-demo").locator(".position-pnl")).to_contain_text(
            "Derived (computed)" if en else "Turunan (dihitung)")
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (lang, width)
        assert page.evaluate("[...document.querySelectorAll('.p4b-cell, .position-card, .p4b-errors, .p4b-bots li')]"
                             ".every(el => el.scrollWidth <= el.clientWidth + 1)"), (lang, width)
        if output and (lang, width) in {("en", 320), ("en", 390), ("en", 1280), ("id", 320)}:
            directory = Path(output)
            directory.mkdir(parents=True, exist_ok=True)
            page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
            tag = page.evaluate_handle("""() => {
              const tag = document.createElement('span');
              tag.textContent = 'QA fixture · OBSERVE';
              tag.style.cssText = 'position:fixed;top:0;right:0;z-index:20000;background:#253442;color:white;padding:2px 7px;font:11px monospace;pointer-events:none';
              document.body.append(tag); return tag;
            }""")
            page.screenshot(path=str(directory / f"P4B-{width}-{lang.upper()}.png"), full_page=True)
            tag.evaluate("tag => tag.remove()")
    assert not writes, writes
    assert not errors, errors
    context.close()


if __name__ == "__main__":
    main(verify)
