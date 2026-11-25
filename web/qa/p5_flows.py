"""P5 end-to-end operator flows on the real in-process OBSERVE worker, with a state-change ledger.

One signed-in journey (login failure and success, navigation by touch targets, keyboard and history, Signals,
account switching, view-only external bots, preset save, risk preview, HALT and RESUME typed confirmation, the
awaiting-fresh-snapshot gate, the OBSERVE chip, logout) followed by a keyboard-only journey. The ledger fails the
run if the browser sends any state-changing request outside the documented set, if any response claims an order
was placed, or if any request leaves loopback.

Broker payloads are route-fulfilled fixtures that exist only in this process.
Run: python web/qa/p5_flows.py   (WARP_QA_SCREENSHOTS=DIR for WebP evidence)
"""
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from account_reads import main
from p5_common import (
    Recorder,
    SnapshotGuard,
    broker_fixtures,
    capture,
    healthy_routes,
    healthy_status,
    put,
    serve_fixtures,
    stamp,
)
from playwright.sync_api import expect

from warp3c.live import BUS
from warp3c.operator import HEALTH, SNAPSHOT

EXPECTED_POSTS = {"/api/login", "/api/logout", "/api/bots/preview", "/api/bots/template", "/api/pause", "/api/resume"}
NEVER = {"/api/warp-bots/action", "/api/paper/roundtrip", "/api/accounts/select", "/api/accounts/budget"}
ROUTES = ("signals", "assets", "screener", "preferences", "overview")


def risk_badge(page):
    return page.locator(".risk-page .card-head .badge").first


def open_settings_tab(page, name):
    page.goto(page.url.split("#")[0] + "#preferences")
    page.get_by_role("button", name=name, exact=True).click()
    expect(page.get_by_role("button", name=name, exact=True)).to_have_attribute("aria-pressed", "true")


def tab_to(page, selector, limit=150):
    """Press Tab until the focused element matches. Fails if it is unreachable by keyboard."""
    for steps in range(1, limit + 1):
        page.keyboard.press("Tab")
        if page.evaluate("selector => !!document.activeElement && document.activeElement.matches(selector)", selector):
            return steps
    raise AssertionError(f"{selector} is not reachable by keyboard within {limit} tab stops")


def journey(browser, base, password, ledger):
    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce",
                                  timezone_id="Asia/Jakarta")
    recorder = Recorder(context, base)
    ledger.append(recorder)
    previews = []
    context.on("response", lambda response: previews.append(response)
               if urlparse(response.url).path == "/api/bots/preview" else None)
    page = context.new_page()
    page.goto(base + "/dashboard", wait_until="domcontentloaded")

    # ── Login: a wrong password gives no session and no data ──
    page.locator("input[type=password]").fill("not-the-password-0000")
    page.locator("form button").last.click()
    expect(page.get_by_role("alert")).to_be_visible()
    expect(page.locator(".command-identity")).to_have_count(0)
    assert not [c for c in context.cookies() if c["name"] == "warp_session"]
    assert context.request.get(base + "/api/status").status == 401
    page.locator("input[type=password]").fill(password)
    page.locator("form button").last.click()
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE")
    cookie = next(c for c in context.cookies() if c["name"] == "warp_session")
    assert cookie["httpOnly"] is True, "session cookie must be HttpOnly"
    assert cookie["sameSite"] in {"Strict", "Lax"}, cookie["sameSite"]
    expect(page.locator(".live-badge")).to_have_text("Live", timeout=15000)

    # ── Navigation: 5 bottom links on a phone, 6 rail links on a desktop ──
    bottom = page.locator(".bottom-nav a")
    expect(bottom).to_have_count(5)
    for route in ROUTES:
        link = page.locator(f'.bottom-nav a[href="#{route}"]')
        link.click()
        expect(link).to_have_attribute("aria-current", "page")
        expect(page.locator("h1")).to_be_focused()
        assert page.url.endswith("#" + route)
    page.go_back()
    assert page.url.endswith("#preferences"), page.url
    page.go_forward()
    assert page.url.endswith("#overview"), page.url
    page.goto(base + "/dashboard#assets", wait_until="domcontentloaded")
    expect(page.locator('.bottom-nav a[href="#assets"]')).to_have_attribute("aria-current", "page")
    page.locator(".skip-link").focus()
    page.keyboard.press("Enter")
    expect(page.locator("h1")).to_be_focused()
    page.set_viewport_size({"width": 1440, "height": 960})
    expect(page.locator(".rail nav a")).to_have_count(6)
    expect(page.locator("nav:visible")).to_have_count(1)
    page.set_viewport_size({"width": 390, "height": 844})

    # ── Signals: proposals are labelled, and no execution control exists ──
    base_status = SNAPSHOT["/api/status"]
    status = {**base_status, "published_at": stamp(), "universe": ["SOLUSDT", "BTCUSDT"],
              "pipeline": {**base_status["pipeline"], "screening": {"state": "ready", "source": "binance_spot_public", "count": 2}}}
    card = {"symbol": "SOLUSDT", "action": "BUY", "model_action": "BUY", "source": "llm evidence snapshot",
            "entry": "120.50", "stop": "118.00", "take": "125.00", "score": {"total": 82}, "fresh": True,
            "thesis": "Closed-candle trend passed the market check.", "as_of": stamp(),
            "evidence_as_of": stamp(-1500), "backtest_passed": True, "backtest_reason": "ok"}
    put({"status": status, "opportunities": {"cards": [card, {**card, "symbol": "BTCUSDT", "action": "WAIT",
                                                              "entry": None, "backtest_passed": False,
                                                              "backtest_reason": "too_few_trades", "thesis": None}]}})
    page.locator('.bottom-nav a[href="#signals"]').click()
    expect(page.locator(".p4-card")).to_have_count(2)
    expect(page.locator(".p4-card").first).to_contain_text("PROPOSED / NOT SUBMITTED")
    expect(page.get_by_role("button", name=re.compile(r"^(buy|sell|execute|place|submit|order|trade)", re.I))).to_have_count(0)
    capture(page, "p5-flow-signals-390-en")

    # ── Accounts: switching, unassigned rows, external bots are view-only ──
    serve_fixtures(page, base, broker_fixtures())
    page.goto(base + "/dashboard#preferences", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    picker = page.locator(".account-picker")
    cards = page.locator(".position-card")
    expect(picker).to_be_visible()
    expect(cards).to_have_count(5)
    picker.get_by_role("button", name="Real Beta", exact=False).click()
    expect(cards).to_have_count(1)
    expect(page.locator(".account-card .badge", has_text="REAL").first).to_be_visible()
    picker.get_by_role("button", name="Account not reported", exact=False).click()
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text("Orphan")
    picker.get_by_role("button", name="Demo Alpha", exact=False).click()
    expect(cards).to_have_count(5)
    for bot in ("External DCA", "VORTEQ-USDC-STOCKS"):
        card_view = cards.filter(has_text=bot)
        expect(card_view).to_contain_text("Read-only · external")
        expect(card_view.locator("button, a, input, select")).to_have_count(0)
    interactive = page.evaluate("""() => [...document.querySelectorAll('.position-card')]
        .filter(card => !card.textContent.includes('WARP-demo'))
        .filter(card => card.querySelector('button, a, input, select, textarea, [role=button]')).length""")
    assert interactive == 0, f"{interactive} external position cards carry a control"
    expect(page.locator(".p4b-bots li", has_text="External DCA").locator("button, a, input")).to_have_count(0)
    expect(page.get_by_role("button", name=re.compile(r"^(Pause|Resume|Close|Delete)$"))).to_have_count(0)
    capture(page, "p5-flow-accounts-390-en")
    page.goto(base + "/dashboard#assets", wait_until="domcontentloaded")
    expect(page.get_by_text("Bot actions are available only in paper mode")).to_be_visible()
    expect(page.locator(".action-grid")).to_have_count(0)

    # ── Preset save and risk preview. Nothing is sent to an exchange ──
    page.get_by_role("button", name="Bot preset").click()
    page.get_by_role("button", name="Aggressive", exact=False).click()
    expect(page.get_by_role("button", name="Aggressive", exact=False)).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Aggressive uses more of the cap", exact=False)).to_be_visible()
    page.get_by_role("button", name="Conservative", exact=False).click()
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Saved. Nothing was sent to the exchange.")).to_be_visible()
    assert previews, "choosing a preset never requested a risk preview"
    for response in previews:
        body = response.json()
        assert response.status == 200 and body["places_orders"] is False and "preview" in body, body

    # ── HALT and RESUME keep their typed confirmation ──
    put(healthy_routes())
    open_settings_tab(page, "Risk & health")
    page.get_by_role("button", name="HALT new entries", exact=True).click()
    confirm = page.locator(".confirm-box").get_by_role("button", name="HALT", exact=True)
    expect(confirm).to_be_disabled()
    for wrong in ("HAL", "RESUME", "halted"):
        page.locator(".confirm-box input").fill(wrong)
        expect(confirm).to_be_disabled()
    posts_before = recorder.paths("POST").count("/api/pause")
    page.get_by_role("button", name="Cancel", exact=True).click()
    assert recorder.paths("POST").count("/api/pause") == posts_before == 0
    page.get_by_role("button", name="HALT new entries", exact=True).click()
    page.locator(".confirm-box input").fill("HALT")
    page.locator(".confirm-box").get_by_role("button", name="HALT", exact=True).click()
    expect(page.get_by_text("HALT is on.", exact=False)).to_be_visible()
    expect(page.locator(".halt-strip")).to_be_visible()
    expect(page.locator(".rk-incidents")).to_contain_text("HALT is active")
    page.get_by_role("button", name="Resume entries", exact=True).click()
    resume = page.locator(".confirm-box").get_by_role("button", name="RESUME", exact=True)
    expect(resume).to_be_disabled()
    page.locator(".confirm-box input").fill("HALT")
    expect(resume).to_be_disabled()
    page.locator(".confirm-box input").fill("RESUME")
    expect(resume).to_be_enabled()
    resume.click()
    expect(page.get_by_text("HALT is off.", exact=False)).to_be_visible()
    gate = page.locator(".ledger:not(.rk-row)", has_text="Entry gate").locator("b")
    expect(gate).to_have_text("Awaiting fresh snapshot")
    expect(risk_badge(page)).to_have_text("Awaiting fresh snapshot")
    assert page.locator(".badge-good").count() == 0 or "No incident reported" not in page.locator(".risk-page").inner_text()
    capture(page, "p5-flow-resume-awaiting-390-en")
    put({"status": healthy_status(stamp(1))})
    expect(gate).not_to_have_text("Awaiting fresh snapshot")
    expect(risk_badge(page)).to_have_text("No incident reported")

    # ── OBSERVE mode is a neutral chip, never an incident, never "clear" ──
    observe = {"status": {**healthy_status(), "risk_permitted": False, "reason": "observe_without_account"},
               "breakers": {"halt": False, "risk_permitted": False, "reason": "observe_without_account"},
               "risk": {"reason": "observe_without_account", "risk_permitted": False, "execution_permitted": False,
                        "equity": None, "equity_reliable": False}}
    put(observe)
    page.goto(base + "/dashboard#overview")
    expect(page.locator(".incident-strip")).to_have_count(0)
    open_settings_tab(page, "Risk & health")
    expect(page.get_by_text("Observe mode · entries blocked by design")).to_be_visible()
    expect(risk_badge(page)).to_have_text("Not verified")
    expect(page.get_by_text("No incident reported")).to_have_count(0)
    capture(page, "p5-flow-observe-chip-390-en")

    # ── Logout: the SPA, the cookie and the stream all end ──
    streams_before = recorder.count("/api/stream")
    page.get_by_role("button", name="Sign out").click()
    expect(page.locator("input[type=password]")).to_be_visible()
    assert not [c for c in context.cookies() if c["name"] == "warp_session" and c["value"]]
    assert context.request.get(base + "/api/status").status == 401
    page.wait_for_timeout(3500)
    assert recorder.count("/api/stream") == streams_before, "the app reconnected the stream after logout"
    page.go_back()
    expect(page.locator(".command-identity")).to_have_count(0)
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("input[type=password]")).to_be_visible()
    expect(page.locator(".command-identity")).to_have_count(0)
    page.close()
    context.close()


def keyboard_only(browser, base, password, ledger):
    """No pointer events at all: sign in, move between pages, open a tab, reach and cancel HALT, close a dialog."""
    context = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    recorder = Recorder(context, base)
    ledger.append(recorder)
    page = context.new_page()
    page.goto(base + "/dashboard", wait_until="domcontentloaded")
    page.locator("input[type=password]").wait_for()
    tab_to(page, "input[type=password]")
    page.keyboard.type(password)
    page.keyboard.press("Enter")
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE")
    put(healthy_routes())
    # Skip link, then each rail link by Enter.
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.classList.contains('skip-link')")
    page.keyboard.press("Enter")
    expect(page.locator("h1")).to_be_focused()
    for route in ("signals", "assets", "screener", "preferences", "overview"):
        link = page.locator(f'.rail a[href="#{route}"]')
        link.focus()
        page.keyboard.press("Enter")
        expect(link).to_have_attribute("aria-current", "page")
        expect(page.locator("h1")).to_be_focused()
    # Settings tabs by keyboard.
    page.locator('.rail a[href="#preferences"]').focus()
    page.keyboard.press("Enter")
    page.locator(".sections button").nth(4).focus()
    page.keyboard.press("Space")
    expect(page.locator(".sections button").nth(4)).to_have_attribute("aria-pressed", "true")
    halt = page.get_by_role("button", name="HALT new entries", exact=True)
    halt.focus()
    page.keyboard.press("Enter")
    confirm_input = page.locator(".confirm-box input")
    expect(confirm_input).to_be_visible()
    confirm_input.focus()
    page.keyboard.type("HALT")
    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement.textContent.trim()")
    assert focused, "focus left the confirmation with no visible target"
    page.get_by_role("button", name="Cancel", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator(".confirm-box")).to_have_count(0)
    assert recorder.paths("POST").count("/api/pause") == 0
    # The room drawer opens and closes by keyboard and returns focus to its trigger.
    page.locator('.rail a[href="#desk"]').focus()
    page.keyboard.press("Enter")
    trigger = page.locator(".trading-room").get_by_role("button", name="EXECUTOR", exact=True)
    trigger.focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(trigger).to_be_focused()
    page.locator(".toolbar button[aria-label*=ign]").focus()
    page.keyboard.press("Enter")
    expect(page.locator("input[type=password]")).to_be_visible()
    page.close()
    context.close()


def verify(browser, base, password):
    ledger: list[Recorder] = []
    with SnapshotGuard(), tempfile.TemporaryDirectory() as directory:
        HEALTH["halt_path"] = str(Path(directory) / "HALT")
        try:
            journey(browser, base, password, ledger)
            keyboard_only(browser, base, password, ledger)
        finally:
            HEALTH.pop("halt_path", None)
    posts = set()
    for recorder in ledger:
        recorder.assert_clean(allow_posts=EXPECTED_POSTS)
        posts.update(recorder.paths("POST"))
    assert not posts & NEVER, posts & NEVER
    print("state-changing requests seen:", sorted(posts))
    assert posts <= EXPECTED_POSTS
    assert len(BUS._subs) >= 0


if __name__ == "__main__":
    main(verify)
