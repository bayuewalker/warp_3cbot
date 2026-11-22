"""P1 transport integration on a real local OBSERVE worker with isolated browser faults."""
import time
from datetime import datetime, timedelta, timezone

from account_reads import main
from playwright.sync_api import expect

from warp3c.live import BUS


def verify(browser, base, password):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    assert context.request.post(base + "/api/login", data={"password": password}).ok
    page = context.new_page()
    faults = []
    page.on("pageerror", lambda error: faults.append(str(error)))
    page.goto(base + "/dashboard", wait_until="domcontentloaded")
    inspector = page.locator("details.telemetry")
    inspector.locator("summary").click()
    expect(inspector.locator("dt")).to_have_count(10)
    expect(inspector.locator("dt").get_by_text("EXECUTOR", exact=True)).to_be_visible()
    expect(page.locator(".live-badge")).to_have_attribute("title", "Live")
    role = inspector.locator("dt", has_text="PORTFOLIO").locator("+ dd")
    now = datetime.now(timezone.utc)
    frame = {"positions": [], "as_of": now.isoformat(), "errors": []}
    BUS.publish("accounts", {"accounts": [], "as_of": now.isoformat()})
    BUS.publish("positions", frame)
    expect(role).to_contain_text("Confirmed empty")
    BUS.publish("positions", {**frame, "errors": ["timeout"]})
    expect(role).to_contain_text("Partial read")
    BUS.publish("positions", {})
    expect(role).to_contain_text("Awaiting worker")
    BUS.publish("positions", {**frame, "positions": [None]})
    expect(role).to_contain_text("Invalid snapshot")
    BUS.publish("positions", frame)
    expect(role).to_contain_text("Confirmed empty")
    BUS.publish("snapshot", {"routes": {"/api/account-positions": {**frame, "as_of": (now - timedelta(hours=1)).isoformat()}}})
    BUS.publish("accounts", {"accounts": [], "as_of": now.isoformat()})
    expect(role).to_contain_text("Confirmed empty")

    # Drop the transport on a new authenticated document, count actual REST
    # fallback requests, then allow the browser's reconnect to reach the server.
    poll_times = []
    streaming = False

    def stream(route):
        if streaming:
            route.continue_()
        else:
            route.abort()

    def book(route):
        poll_times.append(time.monotonic())
        route.fulfill(json=frame)

    page.route("**/api/stream", stream)
    page.route("**/api/account-positions", book)
    page.route("**/api/accounts", lambda route: route.fulfill(json={"accounts": [], "as_of": now.isoformat()}))
    page.reload(wait_until="domcontentloaded")
    page.locator("details.telemetry summary").click()
    expect(role).to_contain_text("Confirmed empty")
    expect(page.locator(".live-badge")).to_have_attribute("title", "Reconnecting")
    with page.expect_request("**/api/account-positions", timeout=8000):
        pass
    expect(role).to_contain_text("Confirmed empty")
    assert len(poll_times) >= 2 and 4.5 <= poll_times[-1] - poll_times[-2] < 8, poll_times
    streaming = True
    expect(page.locator(".live-badge")).to_have_attribute("title", "Live", timeout=12000)
    BUS.publish("positions", {**frame, "errors": ["HTTP 503"]})
    expect(role).to_contain_text("Partial read")

    page.route("**/api/status", lambda route: route.fulfill(status=401, json={"error": "unauthorized"}))
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("input[type=password]")).to_be_visible()
    expect(page.get_by_role("alert")).to_have_text("Session expired. Sign in again.")
    expect(inspector).to_have_count(0)
    assert not faults, faults
    context.close()


if __name__ == "__main__":
    main(verify)
