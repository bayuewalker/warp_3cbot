"""Verify workstation telemetry, motion, keyboard and artwork in a local OBSERVE session."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from account_reads import main
from playwright.sync_api import expect

from warp3c.live import BUS

ROUTES = {
    "SCAN": "screener", "ANALYST": "opportunities", "AEGIS": "decisions",
    "EXECUTOR": "journal", "PORTFOLIO": "account-positions", "WATCHER": "alerts",
}


def verify(browser, base, password):
    context = browser.new_context(viewport={"width": 1440, "height": 960}, timezone_id="Asia/Jakarta",
                                  reduced_motion="no-preference")
    assert context.request.post(base + "/api/login", data={"password": password}).ok
    page = context.new_page()
    errors, writes, streams = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def record(request):
        if request.method != "GET":
            writes.append(request.method)
        if request.url.endswith("/api/stream"):
            streams.append(request.url)

    page.on("request", record)
    page.goto(base + "/dashboard", wait_until="domcontentloaded")
    room = page.locator(".trading-room")
    drawer = page.get_by_role("dialog")
    expect(room.locator("img")).to_have_count(6)
    expect(page.locator(".live-badge")).to_have_text("Live")
    expect(room.locator("#state-EXECUTOR")).to_have_text("OBSERVE")
    expect(room.locator("#state-AEGIS")).to_have_text("Blocked")
    expect(room.locator("#state-PORTFOLIO")).to_have_text("Unavailable")
    expect(room.locator(".station-event")).to_have_count(0)
    output = os.getenv("WARP_QA_SCREENSHOTS")
    for width in (1440, 390, 320):
        page.set_viewport_size({"width": width, "height": 960 if width == 1440 else 844})
        for role, route in ROUTES.items():
            trigger = room.get_by_role("button", name=role, exact=True)
            box = trigger.bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44
            trigger.focus()
            page.keyboard.press("Enter")
            expect(drawer.get_by_role("button", name="Close details")).to_be_focused()
            expect(drawer.locator(".room-records code")).to_have_text("/api/" + route)
            expect(drawer.get_by_text("Latest recorded success")).to_be_visible()
            expect(drawer.get_by_text("Latest recorded failure")).to_be_visible()
            expect(drawer.locator(".room-records p").first).not_to_contain_text("Loading")
            bounds = drawer.bounding_box()
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
            assert (bounds["x"] > 0) if width == 1440 else (bounds["y"] > 0)
            if role == "EXECUTOR" and output and width != 320:
                folder = Path(output)
                folder.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(folder / f"detail-{width}.png"))
            page.keyboard.press("Escape")
            expect(drawer).to_have_count(0)
            expect(trigger).to_be_focused()
    assert len(streams) == 1
    # A new event animates a single receipt. A publication or heartbeat alone cannot create one.
    now = datetime.now(timezone.utc)
    event = {"event": "observe_candidate", "ts": now.isoformat(), "trace_id": "qa-receipt"}
    BUS.publish("snapshot", {"routes": {"/api/events": {"events": [event], "as_of": now.isoformat()}}})
    expect(room.locator('[data-role=EXECUTOR] .station-event')).to_have_count(1)
    expect(room.locator('[data-role=AEGIS] .station-event')).to_have_count(0)
    room.get_by_role("button", name="EXECUTOR", exact=True).click()
    expect(drawer.get_by_text("observe_candidate", exact=False)).to_contain_text("qa-receipt")
    expect(drawer.get_by_text("Latest recorded success").locator("+ dd")).to_have_text("Not reported")
    page.keyboard.press("Escape")

    sprite = room.locator("img").first
    page.emulate_media(reduced_motion="reduce")
    assert sprite.evaluate("e => getComputedStyle(e).animationName") == "none"
    page.emulate_media(reduced_motion="no-preference")
    expect(room).to_have_attribute("data-motion", "on")
    page.evaluate("scrollTo(0, document.body.scrollHeight)")
    expect(room).to_have_attribute("data-motion", "off")
    room.scroll_into_view_if_needed()
    settings = room.get_by_role("button", name="Room display")
    settings.click()
    room.get_by_label("Ambient motion").uncheck()
    room.get_by_label("Text only").check()
    expect(room.locator("img")).to_have_count(0)
    page.reload(wait_until="domcontentloaded")
    settings.click()
    expect(room.get_by_label("Ambient motion")).not_to_be_checked()
    expect(room.get_by_label("Text only")).to_be_checked()
    expect(room.locator("img")).to_have_count(0)
    page.route("**/assets/room/*.svg", lambda route: route.abort())
    room.get_by_label("Text only").uncheck()
    expect(room.locator("img")).to_have_count(0)
    room.get_by_role("button", name="SCAN", exact=True).click()
    expect(drawer).to_be_visible()
    page.keyboard.press("Escape")

    status = context.request.get(base + "/api/status").json()
    assert status["mode"] == "observe"
    page.route("**/api/stream", lambda route: route.abort())
    snapshot = {**status, "published_at": (now - timedelta(hours=2)).isoformat(), "halt": True}
    page.route("**/api/status", lambda route: route.fulfill(json=snapshot))
    page.reload(wait_until="domcontentloaded")
    expect(room.locator("#state-SCAN")).to_have_text("Stale")
    expect(room.locator('[data-role=SCAN] .station-clock')).not_to_contain_text("symbols")
    expect(room.locator("#state-WATCHER")).to_contain_text("Reconnecting")
    room.get_by_role("button", name="WATCHER", exact=True).click()
    expect(drawer.get_by_text("Reconnecting")).to_be_visible()
    expect(drawer.get_by_role("alert")).to_contain_text("HALT")
    page.keyboard.press("Escape")
    snapshot.clear()
    page.reload(wait_until="domcontentloaded")
    expect(room.locator("#state-SCAN")).to_contain_text("Awaiting worker")
    expect(room.locator(".station-event")).to_have_count(0)
    assert not writes and not errors, (writes, errors)
    context.close()


if __name__ == "__main__":
    main(verify)
