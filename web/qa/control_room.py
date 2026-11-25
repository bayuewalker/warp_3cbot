"""Verify the dedicated desk against the authenticated local OBSERVE worker."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from os import getenv
from pathlib import Path

from account_reads import main
from playwright.sync_api import expect

from warp3c.live import BUS
from warp3c.operator import RUNTIME, publish


def verify(browser, base, password):
    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce",
                                  timezone_id="Asia/Jakarta", device_scale_factor=1)
    page = context.new_page()
    page.clock.install()
    errors, mutations, streams = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base + "/dashboard#desk", wait_until="domcontentloaded")
    page.locator("input[type=password]").fill(password)
    def track(request):
        if request.method != "GET" and not request.url.endswith("/api/login"):
            mutations.append(request.method)
        if request.url.endswith("/api/stream"):
            streams.append(request.url)

    page.on("request", track)
    page.get_by_role("button", name="Sign in", exact=True).click()
    room = page.locator(".trading-room")
    wall = room.locator(".market-wall")
    expect(page).to_have_url(base + "/dashboard#desk")
    expect(room.locator(".station")).to_have_count(6)
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE")
    expect(page.locator(".live-badge")).to_have_text("Live")
    expect(wall.locator("svg[role=img]")).to_have_count(0)
    expect(wall.locator(".market-wait")).to_contain_text("Price feed has not returned a quote")
    assert not room.evaluate("el => el.getAnimations({subtree: true}).some(a => a.playState === 'running')")
    backdrop = room.locator(".control-room-scene").evaluate(
        r"""async el => {
          const css = getComputedStyle(el).backgroundImage;
          const url = css.match(/url\("([^"]+)"\)/)?.[1];
          if (!url) return false;
          const response = await fetch(url);
          const image = await createImageBitmap(await response.blob());
          return response.ok && image.width > 0;
        }""")
    assert backdrop
    for lang in ("EN", "ID"):
        if lang == "ID":
            page.locator(".language-button").click()
            expect(page.locator("h1")).to_have_text("Terminal trading")
        for width in (320, 390, 1440):
            page.set_viewport_size({"width": width, "height": 1000 if width > 900 else 844})
            floor = room.locator(".control-room-scene")
            seats = floor.get_by_role("button")
            expect(seats).to_have_count(6)
            bounds = floor.bounding_box()
            for seat in seats.all():
                role = seat.get_attribute("data-seat")
                box = seat.bounding_box()
                assert box["width"] >= 44 and box["height"] >= 44
                assert bounds["x"] <= box["x"] and box["x"] + box["width"] <= bounds["x"] + bounds["width"]
                assert bounds["y"] <= box["y"] and box["y"] + box["height"] <= bounds["y"] + bounds["height"]
                seat.click()
                drawer = page.get_by_role("dialog")
                expect(drawer).to_have_accessible_name(role)
                page.keyboard.press("Escape")
                expect(seat).to_be_focused()
            page.mouse.click(1, 1)
            page.evaluate("window.scrollTo(0, 0)")
            assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
            if getenv("WARP_QA_SCREENSHOTS"):
                folder = Path(getenv("WARP_QA_SCREENSHOTS"))
                folder.mkdir(parents=True, exist_ok=True)
                page.evaluate("document.fonts.ready")
                page.screenshot(path=str(folder / f"WARP-Office-{width}-{lang}.png"))
    page.locator(".language-button").click()
    page.set_viewport_size({"width": 390, "height": 844})

    # These are isolated localhost transport fixtures. The worker makes no broker calls.
    now = datetime.now(timezone.utc)
    for age, value in ((2, "110"), (1, "111")):
        BUS.publish("prices", {"as_of": (now - timedelta(seconds=age)).isoformat(), "limit_seconds": 30,
                               "symbols": {"BTCUSDT": {"last": value, "change_pct": "0"}}})
        expect(wall.locator(".market-quote strong")).to_have_text(value + ".00")
    expect(wall.locator(".market-trace polyline")).to_have_count(1)
    expect(wall.locator(".market-quote small")).to_have_text("0.00%")
    BUS.publish("prices", {"as_of": now.isoformat(), "limit_seconds": 30, "error": "unavailable",
                           "symbols": {"BTCUSDT": {"last": "111", "change_pct": "0"}}})
    expect(wall.locator(".market-wall-head > span")).to_have_text("Stale")
    expect(wall.locator(".market-trace polyline")).to_have_count(0)
    BUS.publish("prices", {"as_of": now.isoformat(), "limit_seconds": 30, "symbols": {}})
    expect(wall.locator(".market-quote")).to_have_count(0)
    expect(wall.locator(".market-trace polyline")).to_have_count(0)

    page.locator('nav:visible a[href="#overview"]').click()
    expect(page.locator(".overview .trading-room")).to_have_count(1)
    page.get_by_role("link", name="Open Trading Desk").click()
    expect(page.locator("h1")).to_be_focused()
    expect(room.locator(".station")).to_have_count(6)
    verify_research_expiry(page, base)
    assert len(streams) == 1, streams
    assert not mutations and not errors, (mutations, errors)
    context.close()


def verify_research_expiry(page, base):
    page.clock.pause_at(datetime.now(timezone.utc) + timedelta(seconds=1))
    now = datetime.fromtimestamp(page.evaluate("Date.now()") / 1000, timezone.utc)
    deadline = now + timedelta(seconds=3)
    cfg = replace(RUNTIME["cfg"], max_signal_age=4)
    engine = RUNTIME["engine"]
    engine.last_opportunities = [{
        "symbol": "ETHUSDT", "action": "WAIT", "as_of": (now - timedelta(seconds=20)).isoformat(),
        "evidence_as_of": (deadline - timedelta(minutes=cfg.max_signal_age)).isoformat(),
        "thesis": "Retained research fixture",
    }]
    publish(cfg, RUNTIME["journal"], engine)
    result = page.request.get(base + "/api/opportunities").json()["cards"][0]
    assert result["fresh"] is True and datetime.fromisoformat(result["expires_at"]) == deadline
    research = page.locator(".desk-research")
    expect(research).to_contain_text("ETHUSDT · WAIT")
    expect(research.get_by_text("Fresh", exact=True)).to_be_visible()
    BUS.publish("heartbeat", {"ts": now.isoformat()})
    page.clock.run_for(3000)
    expect(research.get_by_text("Fresh", exact=True)).to_be_visible()
    page.clock.run_for(1)
    expect(research.get_by_text("Stale", exact=True)).to_be_visible()
    expect(research).to_contain_text("Retained research fixture")
    expect(page.locator(".live-badge")).to_have_text("Live")


if __name__ == "__main__":
    main(verify)
