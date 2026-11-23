"""Verify the dedicated desk against the authenticated local OBSERVE worker."""

from datetime import datetime, timedelta, timezone
from os import getenv
from pathlib import Path

from account_reads import main
from playwright.sync_api import expect

from warp3c.live import BUS


def verify(browser, base, password):
    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce",
                                  timezone_id="Asia/Jakarta", device_scale_factor=1)
    page = context.new_page()
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
    for lang, width in (("EN", 390), ("EN", 1440), ("ID", 320)):
        page.set_viewport_size({"width": width, "height": 1000 if width > 900 else 844})
        if lang == "ID":
            page.locator(".language-button").click()
            expect(page.locator("h1")).to_have_text("Terminal trading")
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        if getenv("WARP_QA_SCREENSHOTS"):
            folder = Path(getenv("WARP_QA_SCREENSHOTS"))
            folder.mkdir(parents=True, exist_ok=True)
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(folder / f"WARP-Desk-{width}-{lang}.png"), full_page=True)
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

    page.locator('nav:visible a[href="#overview"]').click()
    expect(room).to_have_count(0)
    page.get_by_role("link", name="Open Trading Desk").click()
    expect(page.locator("h1")).to_be_focused()
    expect(room.locator(".station")).to_have_count(6)
    assert len(streams) == 1, streams
    assert not mutations and not errors, (mutations, errors)
    context.close()


if __name__ == "__main__":
    main(verify)
