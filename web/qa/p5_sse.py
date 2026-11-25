"""P5 SSE and transport matrix against the real in-process OBSERVE worker.

Cases: initial handshake, malformed handshake, auth loss mid-session (a real 401 from the worker),
disconnect, reconnect with backoff, fallback polls, old frames, repeat events, malformed route frames,
healthy heartbeats with a stale producer. Every case asserts that connectivity is reported truthfully
and that nothing reads green after a failure.

Fixtures exist only in this process. Run: python web/qa/p5_sse.py
"""
import re
import time
from unittest.mock import patch

from account_reads import main
from p5_common import (
    Recorder,
    SnapshotGuard,
    capture,
    healthy_routes,
    healthy_status,
    put,
    sign_in,
    stamp,
    wait_subscribers,
)
from playwright.sync_api import expect

from warp3c.live import BUS, encode_event

STREAM = re.compile(r".*/api/stream$")
OFF = re.compile(r"^off$", re.I)


def new_context(browser, **extra):
    return browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce", **extra)


def badge(page):
    return page.locator(".live-badge")


def risk_badge(page):
    return page.locator(".risk-page .card-head .badge").first


def open_risk(page):
    page.goto(page.url.split("#")[0] + "#preferences")
    page.get_by_role("button", name="Risk & health", exact=True).click()
    expect(page.get_by_role("button", name="Risk & health", exact=True)).to_have_attribute("aria-pressed", "true")


def case_handshake(browser, base, password):
    """The badge is not Live until a valid hello arrives, and HTTP reads work meanwhile."""
    put(healthy_routes())
    context = new_context(browser)
    recorder = Recorder(context, base)
    held = []
    context.route(STREAM, lambda route: held.append(route))
    page = sign_in(context, base, password)
    expect(badge(page)).to_have_text("Reconnecting")
    expect(badge(page)).to_have_attribute("data-tone", "warn")
    assert held, "the browser never opened the stream"
    # A hello without a valid source time is not a handshake.
    held[0].fulfill(status=200, headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
                    body=encode_event("hello", {"ts": "not-a-time"}))
    expect(badge(page)).to_have_text("Reconnecting")
    # A real hello from the worker is.
    context.unroute(STREAM)
    expect(badge(page)).to_have_text("Live", timeout=15000)
    expect(badge(page)).to_have_attribute("data-tone", "good")
    page.close()
    recorder.assert_clean()
    context.close()


def case_disconnect_reconnect(browser, base, password):
    """Drop, back off, poll over HTTP, reconnect, stop polling. No green while the transport is down."""
    put(healthy_routes())
    context = new_context(browser)
    recorder = Recorder(context, base)
    link = {"mode": "hello_then_end", "attempts": []}

    def stream(route):
        link["attempts"].append(time.monotonic())
        if link["mode"] == "live":
            route.continue_()
        elif link["mode"] == "hello_then_end":
            link["mode"] = "drop"
            route.fulfill(status=200, headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
                          body=encode_event("hello", {"ts": stamp()}))
        else:
            route.abort()

    context.route(STREAM, stream)
    page = sign_in(context, base, password)
    page.goto(base + "/dashboard#overview")
    # The scripted stream ended. Transport is down and says so.
    expect(badge(page)).to_have_text("Reconnecting", timeout=10000)
    expect(badge(page)).to_have_attribute("data-tone", "warn")
    # More than five seconds down raises the incident strip and no panel is green.
    expect(page.locator(".incident-strip [data-incident=stream]")).to_be_visible(timeout=20000)
    open_risk(page)
    expect(risk_badge(page)).to_have_text("Incident")
    expect(page.get_by_text("No incident reported")).to_have_count(0)
    # Backoff grows (1 s, 2 s, 4 s ...) rather than hammering the worker.
    while len(link["attempts"]) < 4:
        page.wait_for_timeout(500)
    gaps = [b - a for a, b in zip(link["attempts"], link["attempts"][1:], strict=False)]
    assert gaps[1] > gaps[0] * 1.4 and gaps[2] > gaps[1] * 1.4, gaps
    assert 0.8 <= gaps[0] <= 2.5 and 1.8 <= gaps[1] <= 3.5, gaps
    # Fallback polling: the app keeps reading over HTTP while the stream is down.
    before = recorder.count("/api/status")
    page.wait_for_timeout(6000)
    assert recorder.count("/api/status") > before, "no fallback poll while the stream was down"
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE")
    capture(page, "p5-sse-disconnected-390-en")
    # Reconnect: the badge returns to Live, the incident clears, and polling stops.
    link["mode"] = "live"
    expect(badge(page)).to_have_text("Live", timeout=30000)
    expect(badge(page)).to_have_attribute("data-tone", "good")
    expect(risk_badge(page)).to_have_text("No incident reported", timeout=10000)
    settled = recorder.count("/api/status")
    page.wait_for_timeout(7500)
    assert recorder.count("/api/status") == settled, "HTTP polling continued after the stream returned"
    assert recorder.count("/api/stream") >= 5
    page.close()
    recorder.assert_clean()
    context.close()
    wait_subscribers(0)


def case_auth_loss(browser, base, password):
    """A real 401 mid-session: the worker rejects the session, the app signs out and stops reconnecting."""
    put(healthy_routes())
    context = new_context(browser)
    recorder = Recorder(context, base)
    page = sign_in(context, base, password)
    expect(badge(page)).to_have_text("Live", timeout=15000)
    streams = recorder.count("/api/stream")
    real = __import__("warp3c.httpapi", fromlist=["authorized"]).authorized
    with patch("warp3c.httpapi.authorized", lambda *_a, **_k: False):
        # The worker pushes a frame. The server notices the session is gone and ends the stream.
        put(healthy_routes())
        expect(page.locator("input[type=password]")).to_be_visible(timeout=30000)
        expect(page.get_by_role("alert")).to_have_text("Session expired. Sign in again.")
        # No cached worker data, mode badge or green connectivity survives sign-out.
        expect(page.locator(".command-identity")).to_have_count(0)
        expect(page.locator(".live-badge")).to_have_count(0)
        expect(page.locator("[data-tone=good], .badge-good")).to_have_count(0)
        stopped = recorder.count("/api/stream")
        page.wait_for_timeout(5000)
        assert recorder.count("/api/stream") == stopped, "the app kept reconnecting after sign-out"
        assert stopped >= streams
        capture(page, "p5-sse-auth-loss-390-en")
    assert real is not None
    # Signing in again restores a Live stream with a fresh session.
    page.locator("input[type=password]").fill(password)
    page.locator("form button").last.click()
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE")
    expect(badge(page)).to_have_text("Live", timeout=20000)
    page.close()
    recorder.assert_clean()
    context.close()
    wait_subscribers(0)


def case_old_and_repeat_frames(browser, base, password):
    """Old frames, repeated frames and frames beyond the 15 minute publication ceiling."""
    put(healthy_routes())
    context = new_context(browser)
    recorder = Recorder(context, base)
    page = sign_in(context, base, password)
    expect(badge(page)).to_have_text("Live", timeout=15000)
    page.goto(base + "/dashboard#overview")
    strip = page.locator(".status-strip")
    age = strip.locator("dt", has_text="Worker age").locator("+ dd")
    halt = strip.locator("dt", has_text="HALT").locator("+ dd")
    put(healthy_routes())
    expect(halt).to_have_text(OFF)
    expect(age).not_to_contain_text("Stale")

    # An envelope older than the one already applied is discarded, even though its body says HALT.
    newer = {"routes": {"/api/status": healthy_status(stamp(2))}, "published_at": stamp(2)}
    BUS.publish("snapshot", newer)
    older = {"routes": {"/api/status": healthy_status(stamp(-30), halt=True, reason="halt")}, "published_at": stamp(-30)}
    BUS.publish("snapshot", older)
    BUS.publish("snapshot", newer)  # barrier: the next applied frame proves the older one was processed
    expect(halt).to_have_text(OFF)
    expect(page.locator(".halt-strip")).to_have_count(0)

    # Repeated identical frames are idempotent: no flapping, no errors, no extra HTTP reads.
    reads = len(recorder.requests)
    for _ in range(60):
        BUS.publish("snapshot", newer)
    expect(halt).to_have_text(OFF)
    expect(page.locator(".halt-strip")).to_have_count(0)
    assert len(recorder.requests) == reads, "repeated stream frames triggered HTTP reads"

    # A repeated halt event is idempotent as well, and it keeps the earlier snapshot fields.
    for _ in range(5):
        BUS.publish("halt", {"halt": True})
    expect(page.locator(".halt-strip")).to_be_visible()
    expect(page.locator(".status-strip dt", has_text="Execution").locator("+ dd")).to_have_text("Blocked")
    BUS.publish("snapshot", {"routes": {"/api/status": healthy_status(stamp(3))}, "published_at": stamp(3)})
    expect(page.locator(".halt-strip")).to_have_count(0)

    # A frame whose producer time is older than the evidence already held is dropped, even when it is
    # delivered now with a fresh envelope time. It must not make the desk look stale or healthy.
    ancient = {"status": healthy_status(stamp(-1500)), "breakers": healthy_routes()["breakers"], "risk": healthy_routes()["risk"]}
    put(ancient)
    BUS.publish("snapshot", newer)  # barrier
    expect(age).not_to_contain_text("Stale")
    expect(page.locator(".incident-strip [data-incident=statusStale]")).to_have_count(0)

    # A new page that only ever receives a producer time beyond the 15 minute ceiling is stale on every surface.
    page.close()
    fresh = new_context(browser)
    fresh_recorder = Recorder(fresh, base)
    put(ancient)
    page = sign_in(fresh, base, password)
    page.goto(base + "/dashboard#overview")
    age = page.locator(".status-strip dt", has_text="Worker age").locator("+ dd")
    expect(badge(page)).to_have_text("Live", timeout=15000)
    expect(age).to_contain_text("Stale")
    expect(age).to_have_attribute("data-tone", "warn")
    expect(page.locator(".incident-strip [data-incident=statusStale]")).to_be_visible()
    open_risk(page)
    expect(risk_badge(page)).not_to_have_text("No incident reported")
    expect(page.get_by_text("No incident reported")).to_have_count(0)
    capture(page, "p5-sse-stale-producer-390-en")
    put(healthy_routes())
    page.close()
    fresh_recorder.assert_clean()
    fresh.close()
    recorder.assert_clean()
    context.close()


def case_route_errors(browser, base, password):
    """Malformed, empty and failing route frames never render as healthy data."""
    put(healthy_routes())
    context = new_context(browser)
    recorder = Recorder(context, base)
    page = sign_in(context, base, password)
    expect(badge(page)).to_have_text("Live", timeout=15000)
    page.goto(base + "/dashboard#overview")
    put(healthy_routes())
    page.locator("details.telemetry summary").click()
    inspector = page.locator("details.telemetry")
    portfolio = inspector.locator("dt", has_text="PORTFOLIO").locator("+ dd")
    now = stamp()
    BUS.publish("accounts", {"accounts": [], "as_of": now})
    BUS.publish("positions", {"positions": [], "as_of": now, "errors": []})
    expect(portfolio).to_contain_text("Confirmed empty")
    # An empty frame is awaiting the first snapshot, never an empty book.
    BUS.publish("positions", {})
    expect(portfolio).to_contain_text("Awaiting worker")
    # A frame with a bad row is an invalid snapshot, not a partial success.
    BUS.publish("positions", {"positions": [None], "as_of": now, "errors": []})
    expect(portfolio).to_contain_text("Invalid snapshot")
    # A book error keeps the rows but cannot read as confirmed.
    BUS.publish("positions", {"positions": [], "as_of": now, "errors": ["HTTP 503"], "error": "HTTP 503"})
    expect(portfolio).not_to_contain_text("Confirmed")
    # Positive control: the strip does render an allow when a PAPER worker publishes one (browser fixture only).
    execution = page.locator(".status-strip dt", has_text="Execution").locator("+ dd")
    put({"status": healthy_status(mode="paper", reason="paper", execution_permitted=True)})
    expect(execution).to_have_text("Execution permitted")
    # An OBSERVE worker that claims a permit is still shown as blocked.
    put({"status": healthy_status(execution_permitted=True)})
    expect(execution).to_have_text("Blocked")
    # A status frame with the wrong shape is invalid. Nothing on screen turns into an allow.
    put({"status": {"mode": 5, "published_at": stamp()}})
    expect(execution).to_have_text("Not reported")
    expect(page.get_by_text(re.compile(r"execution permitted", re.I))).to_have_count(0)
    put(healthy_routes())
    page.close()
    recorder.assert_clean()
    context.close()


def case_http_route_errors(browser, base, password):
    """With the stream down, a failing status read is an incident and never an allow."""
    put(healthy_routes())
    context = new_context(browser)
    recorder = Recorder(context, base)
    context.route(STREAM, lambda route: route.abort())
    state = {"status": 200}

    def status(route):
        if state["status"] == 200:
            route.continue_()
        else:
            route.fulfill(status=state["status"], json={"error": "fixture"})

    context.route(re.compile(r".*/api/status$"), status)
    page = sign_in(context, base, password)
    expect(badge(page)).to_have_text("Reconnecting")
    state["status"] = 500
    page.goto(base + "/dashboard#overview")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".incident-strip [data-incident=statusFailed]")).to_be_visible(timeout=20000)
    open_risk(page)
    expect(risk_badge(page)).not_to_have_text("No incident reported")
    expect(page.locator("[data-tone=good]")).to_have_count(0)
    expect(page.locator(".status-strip dt", has_text="Execution").locator("+ dd")).not_to_have_text("Permitted")
    page.close()
    recorder.assert_clean()
    context.close()


def case_stale_producer_with_heartbeats(browser, base, password):
    """The transport is healthy, the producer stopped. Heartbeats must not refresh data."""
    put(healthy_routes())
    context = new_context(browser)
    recorder = Recorder(context, base)
    page = context.new_page()
    page.clock.install()
    page.goto(base + "/dashboard", wait_until="domcontentloaded")
    page.locator("input[type=password]").fill(password)
    page.locator("form button").last.click()
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE")
    page.set_default_timeout(8000)
    put(healthy_routes())
    page.goto(base + "/dashboard#overview")
    expect(badge(page)).to_have_text("Live")
    age = page.locator(".status-strip dt", has_text="Worker age").locator("+ dd")
    expect(age).not_to_contain_text("Stale")
    page.clock.fast_forward(960_000)
    BUS.publish("heartbeat", {"ts": stamp()})
    expect(badge(page)).to_have_text("Live")
    expect(age).to_contain_text("Stale")
    expect(page.locator(".incident-strip [data-incident=statusStale]")).to_be_visible()
    open_risk(page)
    expect(risk_badge(page)).not_to_have_text("No incident reported")
    page.close()
    recorder.assert_clean()
    context.close()


def verify(browser, base, password):
    with SnapshotGuard():
        for case in (case_handshake, case_disconnect_reconnect, case_auth_loss, case_old_and_repeat_frames,
                     case_route_errors, case_http_route_errors, case_stale_producer_with_heartbeats):
            started = time.monotonic()
            case(browser, base, password)
            print(f"ok {case.__name__} {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main(verify)
