"""Browser regression for the broker read views. Fixtures exist only in this process.

Run after building web/dist with the project and Python Playwright installed:
    python web/qa/account_reads.py
Add --layout DIRECTORY to run the bilingual viewport audit and save screenshots.
Set CHROMIUM_PATH if using a system Chromium instead of Playwright's browser.
"""
from __future__ import annotations

import argparse
import os
import secrets
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

from warp3c.__main__ import HealthHandler
from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.live import BUS
from warp3c.operator import publish
from warp3c.state import OrderJournal


def run(browser, base, password):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    assert context.request.post(base + "/api/login", data={"password": password}).ok
    assert context.request.get(base + "/api/status").json()["execution_permitted"] is False
    page = context.new_page()
    page.clock.install()
    failures = []
    page.on("pageerror", lambda exc: failures.append(str(exc)))
    page.set_default_timeout(4000)
    # Force the existing REST fallback. Retain real session and all other routes.
    page.route("**/api/stream", lambda route: route.abort())
    now = datetime.now(timezone.utc)
    fresh = now.isoformat()
    old = (now - timedelta(minutes=20)).isoformat()
    profile = {"code": "QA-A", "name": "Profile A", "demo": True, "selectable": False}
    accounts = {"accounts": [profile], "as_of": fresh, "error": None}
    book = {"positions": [], "by_account": {}, "as_of": fresh, "errors": [], "error": None}
    row = {"id": "row-a", "pair": "QA/USDT", "kind": "strategy", "owner": "external",
           "account_code": profile["code"], "take_profits": []}
    observed = {"/api/accounts": accounts, "/api/account-positions": book}

    def respond(route):
        value = observed[route.request.url.removeprefix(base)]
        if value == "timeout":
            route.abort("timedout")
        elif isinstance(value, int):
            route.fulfill(status=value, json={"error": "fixture"})
        else:
            route.fulfill(json=value)

    for path in observed:
        page.route("**" + path, respond)

    def open_accounts(parent, positions):
        observed.update({"/api/accounts": parent, "/api/account-positions": positions})
        if page.url.endswith("/dashboard#preferences"):
            page.reload(wait_until="domcontentloaded")
        else:
            page.goto(base + "/dashboard#preferences", wait_until="domcontentloaded")
        page.locator(".fresh-lines").wait_for()

    picker = page.locator(".account-picker")
    for missing in ({}, {"positions": []}, {**book, "as_of": old}, {**book, "as_of": "invalid"},
                    {**book, "errors": ["HTTP 429"]}, {**book, "stale": True}, 429, 503, "timeout"):
        open_accounts(accounts, missing)
        expect(picker).to_contain_text("Open positions · Unavailable")
        expect(page.get_by_text("No open positions on this account.")).to_have_count(0)

    open_accounts(accounts, book)
    expect(picker).to_contain_text("Open positions · 0")
    expect(page.get_by_text("No open positions on this account.")).to_be_visible()
    # A failed re-read after success must invalidate exact counts, keeping the profile.
    observed["/api/account-positions"] = 503
    page.get_by_role("button", name="Refresh", exact=True).first.click()
    expect(picker).to_contain_text("Open positions · Unavailable")
    expect(page.get_by_role("alert")).to_be_visible()
    observed["/api/account-positions"] = book
    page.get_by_role("button", name="Retry", exact=True).click()
    expect(picker).to_contain_text("Open positions · 0")

    populated = {**book, "positions": [row], "by_account": {"QA-A": 1}}
    for unavailable_parent in ({}, {**accounts, "accounts": []}, {**accounts, "error": "HTTP 500"},
                               {**accounts, "as_of": old}, 429, 503, "timeout"):
        open_accounts(unavailable_parent, populated)
        expect(picker.get_by_text("Account not reported")).to_be_visible()
        expect(page.locator(".position-card")).to_contain_text("QA/USDT")
        if isinstance(unavailable_parent, int) or unavailable_parent == "timeout":
            expect(page.get_by_role("alert")).to_be_visible()
        if isinstance(unavailable_parent, dict) and unavailable_parent.get("error"):
            expect(page.locator(".fresh-lines .age.bad").first).to_contain_text("HTTP 500")

    profile_b = {**profile, "code": "QA-B", "name": "Profile B"}
    open_accounts({**accounts, "accounts": [profile, profile_b]}, populated)
    expect(page.locator(".position-card")).to_contain_text("QA/USDT")
    picker.get_by_role("button", name="Profile B", exact=False).click()
    expect(page.locator(".position-card")).to_have_count(0)
    expect(page.get_by_text("No open positions on this account.")).to_be_visible()
    picker.get_by_role("button", name="Profile A", exact=False).click()
    expect(page.locator(".position-card")).to_have_count(1)

    open_accounts(accounts, {**populated, "errors": ["position_read_cap"]})
    expect(picker).to_contain_text("Open positions · Unavailable")
    expect(page.locator(".position-card")).to_have_count(1)
    expect(page.locator(".fresh-lines p").nth(1)).to_contain_text("Incomplete position read")

    # Return to the authenticated stream. Producer frames use the same live
    # event encoder, HTTP handler and client reducers as the running worker.
    page.close()
    page = context.new_page()
    page.clock.install()
    page.on("pageerror", lambda exc: failures.append(str(exc)))
    page.goto(base + "/dashboard#preferences", wait_until="domcontentloaded")
    page.locator(".fresh-lines").wait_for()
    expect(page.locator(".live-badge")).to_have_attribute("title", "Live")
    picker = page.locator(".account-picker")
    BUS.publish("accounts", accounts)
    BUS.publish("positions", book)
    expect(picker).to_contain_text("Open positions · 0")
    BUS.publish("positions", {})
    expect(picker).to_contain_text("Open positions · Unavailable")
    BUS.publish("positions", populated)
    expect(page.locator(".position-card")).to_contain_text("QA/USDT")
    BUS.publish("accounts", {**accounts, "error": "HTTP 429"})
    expect(picker.get_by_text("Account not reported")).to_be_visible()
    expect(page.locator(".fresh-lines .age.bad").first).to_contain_text("HTTP 429")
    BUS.publish("accounts", accounts)
    expect(picker.get_by_text("Account not reported")).to_have_count(0)
    expect(page.locator(".position-card")).to_contain_text("QA/USDT")
    BUS.publish("positions", {**book, "as_of": old})
    expect(picker).to_contain_text("Open positions · Unavailable")
    expect(page.locator(".fresh-lines p").nth(1)).to_contain_text("Stale")
    BUS.publish("positions", book)
    expect(picker).to_contain_text("Open positions · 0")
    # A producer can stop publishing while healthy SSE heartbeats continue.
    page.clock.fast_forward(151_000)
    expect(picker).to_contain_text("Open positions · Unavailable")

    page.route("**/api/positions", lambda route: route.fulfill(json={}))
    page.goto(base + "/dashboard#assets", wait_until="domcontentloaded")
    page.locator(".sections button").nth(1).click()
    expect(page.get_by_role("alert")).to_contain_text("Worker snapshot unavailable")

    page.route("**/api/accounts", lambda route: route.fulfill(status=401, json={"error": "unauthorized"}))
    page.goto(base + "/dashboard#preferences", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("input[type=password]")).to_be_visible()
    assert not failures, failures
    context.close()


def layout(browser, base, password, output):
    """Capture the real no-credentials worker. No routes are intercepted here."""
    output.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(reduced_motion="reduce", timezone_id="Asia/Jakarta")
    page = context.new_page()
    errors = []
    results = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(base + "/dashboard", wait_until="domcontentloaded")
    page.locator("input[type=password]").fill(password)
    page.locator("form button").last.click()
    page.locator('a[href="#preferences"]').first.wait_for(state="attached")
    for lang in ("en", "id"):
        if lang == "id":
            page.locator(".language-button").click()
        for width in (320, 360, 390, 768, 1024, 1440, 1920):
            page.set_viewport_size({"width": width, "height": 844 if width < 600 else 960})
            for route in ("overview", "signals", "assets", "screener", "preferences"):
                page.locator(f'a[href="#{route}"]:visible').first.click()
                page.wait_for_selector(f'a[href="#{route}"][aria-current=page]', state="attached")
                page.evaluate("document.fonts.ready")
                tabs = page.locator(".sections button")
                for tab in range(3 if route in ("assets", "preferences") else 1):
                    if route in ("assets", "preferences"):
                        tabs.nth(tab).click()
                        expect(tabs.nth(tab)).to_have_attribute("aria-pressed", "true")
                    if route == "preferences" and tab == 0:
                        page.locator(".fresh-lines").wait_for()
                        if lang == "en" and width in (390, 1440):
                            page.locator(".fresh-lines").scroll_into_view_if_needed()
                            page.screenshot(path=str(output / f"after-{width}.png"))
                    excess = page.evaluate("document.documentElement.scrollWidth - innerWidth")
                    results.append((lang, width, route, tab, excess))
    context.close()
    overflow = [row for row in results if row[-1] > 0]
    print(f"{len(results)} layouts, {len(overflow)} with horizontal overflow, {len(errors)} page errors")
    for row in overflow:
        print(*row)
    if overflow or errors:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, metavar="SCREENSHOT_DIR")
    args = parser.parse_args()
    password = secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        "WARP_DASHBOARD_PASSWORD": password,
        "WARP_SESSION_SECRET": secrets.token_urlsafe(32),
        "WARP_WEB_DIST": str(Path(__file__).resolve().parents[1] / "dist"),
    }):
        cfg = Settings(mode="observe", db_path=str(Path(directory) / "qa.db"))
        journal = OrderJournal(cfg.db_path)
        # Real no-credentials startup snapshot. No network clients or execution loop.
        engine = Engine(cfg, None, None, journal, None)
        engine.refresh_readonly()
        publish(cfg, journal, engine)
        with ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH"),
                                                         args=["--no-zygote", "--disable-gpu"])
                    base = f"http://127.0.0.1:{server.server_port}"
                    if args.layout:
                        layout(browser, base, password, args.layout)
                    else:
                        run(browser, base, password)
                    browser.close()
            finally:
                server.shutdown()
                thread.join()
                journal.close()
    print("Account read browser regressions passed.")


if __name__ == "__main__":
    main()
