"""Exercise the command shell against a temporary authenticated OBSERVE worker."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from account_reads import main
from playwright.sync_api import expect

from warp3c.operator import RUNTIME, publish


def verify(browser, base, password):
    context = browser.new_context(reduced_motion="reduce", timezone_id="Asia/Jakarta")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(base + "/dashboard")
    page.locator("input[type=password]").fill(password)
    page.locator("form button").last.click()
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE")
    expect(page.locator(".status-strip").get_by_text("Blocked", exact=True)).to_be_visible()
    expect(page.locator(".room-summary")).to_have_count(0)
    expect(page.locator(".live-badge")).to_have_text("Live")
    for width in (320, 360, 390, 768, 900, 901, 1024, 1440, 1920):
        height = 844 if width < 600 else 960
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("document.fonts.ready")
        nav = page.locator("nav:visible")
        expect(nav).to_have_count(1)
        mobile = width <= 900
        expect(nav.locator("a")).to_have_count(5 if mobile else 6)
        expect(nav.locator('a[href="#desk"]')).to_have_count(0 if mobile else 1)
        expect(page.locator(".bottom-nav" if mobile else ".rail")).to_be_visible()
        if width in (320, 360, 390):
            assert nav.locator("a").evaluate_all(
                "links => links.every(link => parseFloat(getComputedStyle(link).fontSize) >= 12)"
            ), width
            page.get_by_role("link", name="Open Trading Desk").click()
            expect(page.locator(".desk-workspace")).to_be_visible()
            expect(page.locator(".bottom-nav a")).to_have_count(5)
            nav.locator('a[href="#overview"]').click()
        for control in [*nav.locator("a").all(), *page.locator(".toolbar button").all()]:
            box = control.bounding_box()
            assert box and box["width"] >= 44 and box["height"] >= 44, (width, box)
            assert box["x"] >= 0 and box["x"] + box["width"] <= width + 0.1, (width, box)
        if width in (320, 390):
            box = page.locator(".metrics").bounding_box()
            assert box["y"] + box["height"] < height - 60, (width, box)
        if width in (320, 390, 1440) and os.getenv("WARP_QA_SCREENSHOTS"):
            output = Path(os.environ["WARP_QA_SCREENSHOTS"])
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / f"after-{width}.png"))
    page.set_viewport_size({"width": 320, "height": 844})
    page.get_by_role("link", name="Open Trading Desk").click()
    expect(page.locator(".room-summary dt")).to_have_count(6)
    page.locator(".language-button").click()
    expect(page.locator(".room-summary dd", has_text="Kosong terkonfirmasi").first).to_be_visible()
    page.evaluate("document.fonts.ready")
    for width, columns in ((320, 2), (359, 2), (360, 3)):
        page.set_viewport_size({"width": width, "height": 844})
        assert page.locator(".room-summary dl").evaluate(
            "el => getComputedStyle(el).gridTemplateColumns.split(' ').length"
        ) == columns
    page.set_viewport_size({"width": 320, "height": 844})
    # An element can fit horizontally while one word spans two line boxes.
    assert page.locator(".room-summary dd").evaluate_all(r"""items => items.every(item => {
      if (item.scrollWidth > item.clientWidth) return false;
      const text = item.firstChild;
      return Array.from(text.textContent.matchAll(/\S+/g)).every(match => {
        const range = document.createRange();
        range.setStart(text, match.index);
        range.setEnd(text, match.index + match[0].length);
        return range.getClientRects().length === 1;
      });
    })""")
    page.locator(".language-button").click()
    page.locator('nav:visible a[href="#signals"]').focus()
    page.keyboard.press("Enter")
    expect(page.locator("h1")).to_be_focused()
    page.go_back()
    expect(page.locator(".room-summary")).to_be_visible()

    page.locator('nav:visible a[href="#overview"]').click()
    page.get_by_role("button", name="HALT new entries").click()
    confirm = page.locator(".confirm-box")
    expect(confirm.get_by_role("button", name="HALT", exact=True)).to_be_disabled()
    confirm.locator("input").fill("HALT")
    expect(confirm.get_by_role("button", name="HALT", exact=True)).to_be_enabled()
    confirm.get_by_role("button", name="Cancel").click()
    page.locator('.bottom-nav a[href="#assets"]').click()
    page.get_by_role("button", name="Bot preset").click()
    page.get_by_role("button", name="Aggressive", exact=False).click()
    expect(page.get_by_role("button", name="Aggressive", exact=False)).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Aggressive uses more of the cap", exact=False)).to_be_visible()
    page.get_by_role("button", name="Conservative", exact=False).click()
    expect(page.get_by_role("button", name="Conservative", exact=False)).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Saved. Nothing was sent to the exchange.")).to_be_visible()
    page.locator('.bottom-nav a[href="#screener"]').click()
    page.get_by_label("Maximum names").fill("7")
    page.get_by_role("button", name="Save filters").click()
    expect(page.get_by_text("Saved. Nothing was sent to the exchange.")).to_be_visible()
    # This harness does not run a worker loop. Publish its persisted settings
    # through the same projection used on the next normal worker cycle.
    publish(RUNTIME["cfg"], RUNTIME["journal"], RUNTIME["engine"])
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_label("Maximum names")).to_have_value("7")

    # Keep broker and execution paths untouched. Only the local status read is
    # intercepted to exercise the alert banner and expired/unknown source times.
    status = context.request.get(base + "/api/status").json()
    assert status["mode"] == "observe"
    snapshot = {**status, "halt": True}
    page.route("**/api/stream", lambda route: route.abort())
    page.route("**/api/status", lambda route: route.fulfill(json=snapshot))
    page.reload(wait_until="domcontentloaded")
    halt = page.locator(".halt-strip")
    expect(halt).to_contain_text("HALT")
    bell = page.locator("button[aria-controls=desk-alerts]")
    for lang in ("en", "id"):
        if lang == "id":
            page.locator(".language-button").click()
        for link in page.locator("nav:visible a").all():
            link.click()
            expect(link).to_have_attribute("aria-current", "page")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            bell.click()
            panel = page.locator("#desk-alerts")
            expect(panel).to_be_visible()
            bounds = panel.bounding_box()
            banner = halt.bounding_box()
            assert banner["y"] >= 0 and banner["y"] + banner["height"] <= bounds["y"] + 1
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 320
            page.keyboard.press("Escape")
            expect(bell).to_be_focused()
            expect(panel).to_be_hidden()
    page.locator(".language-button").click()
    snapshot.update(halt=False, published_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".status-strip time")).to_contain_text("Stale")
    snapshot["published_at"] = None
    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".status-strip dt", has_text="Worker age").locator("+ dd")).to_have_text("Not reported")
    # A delayed/absent read after reload must not show the mode from a prior page.
    snapshot.clear()
    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".command-identity .badge")).to_contain_text("Not reported")
    expect(page.locator(".status-strip dt", has_text="Execution").locator("+ dd")).to_have_text("Not reported")
    page.evaluate("document.documentElement.style.fontSize='200%'")
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    verify_stale_position_count(context, base)
    page.get_by_role("button", name="Sign out").click()
    expect(page.locator("input[type=password]")).to_be_visible()
    assert not errors, errors
    context.close()


def verify_stale_position_count(context, base):
    page = context.new_page()
    page.route("**/api/stream", lambda route: route.abort())
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    strategy = {"id": "qa-strategy", "kind": "strategy", "take_profits": []}
    retained_book = [strategy, {"id": "qa-dca", "kind": "dca", "take_profits": []}]
    page.route("**/api/account-positions", lambda route: route.fulfill(json={
        "as_of": old, "positions": retained_book, "stale": True, "errors": ["strategies_stale"],
    }))
    page.route("**/api/positions", lambda route: route.fulfill(json={"as_of": old, "positions": [strategy]}))
    page.goto(base + "/dashboard", wait_until="domcontentloaded")
    for heading, label, stale, partial in (
        ("Accounts & capital", "Open positions", "Stale", "Partial read"),
        ("Akun & modal", "Posisi terbuka", "Kedaluwarsa", "Data sebagian"),
    ):
        card = page.locator("section.surface").filter(has=page.get_by_role("heading", name=heading, exact=True))
        # Wait for the retained book to reach PORTFOLIO before asserting the count.
        expect(card.locator(".ledger", has_text="PORTFOLIO").locator("b")).to_have_text(partial)
        expect(card.locator(".ledger", has_text=label).locator("b")).to_have_text(stale)
        if heading == "Accounts & capital":
            page.locator(".language-button").click()
    page.close()


if __name__ == "__main__":
    main(verify)
