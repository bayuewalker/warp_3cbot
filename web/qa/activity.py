"""Verify Activity, Risk & health, the incident strip and the HALT/RESUME gate against the real OBSERVE worker.

Rows reach the browser through the worker's own snapshot store, HTTP routes and SSE stream.
Only the failure cases use request interception, because the worker cannot be made to fail on demand.
"""

import re
import tempfile
from datetime import datetime, timedelta, timezone
from os import getenv
from pathlib import Path

from account_reads import main
from playwright.sync_api import expect

from warp3c import operator
from warp3c.httpapi import broadcast_live
from warp3c.live import encode_event
from warp3c.operator import HEALTH, SNAPSHOT

KNOWN_GETS = {*operator.ROUTES, "/api/rules", "/api/stream", "/api/session"}
LONG_ID = "warp-client-" + "x" * 90


def stamp(seconds: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="microseconds")


def healthy_status(published: str | None = None) -> dict:
    return {
        "mode": "observe", "reason": "observe", "halt": False, "risk_permitted": True, "execution_permitted": False,
        "dependencies_healthy": True, "market_data_fresh": True, "published_at": published or stamp(),
        "providers": {"nvidia": {"state": "ok", "model": "qa-model"}},
    }


def healthy_routes() -> dict:
    return {
        "status": healthy_status(),
        "breakers": {"halt": False, "risk_permitted": True, "reason": "observe"},
        "risk": {"reason": "observe", "risk_permitted": True, "execution_permitted": False,
                 "equity": "1000.00", "equity_reliable": True},
    }


def observe_routes(reason: str) -> dict:
    return {
        "status": {**healthy_status(), "risk_permitted": False, "reason": reason},
        "breakers": {"halt": False, "risk_permitted": False, "reason": reason},
        "risk": {"reason": reason, "risk_permitted": False, "execution_permitted": False,
                 "equity": None, "equity_reliable": False},
    }


def timeline_routes() -> dict:
    return {
        **healthy_routes(),
        "events": {"as_of": stamp(), "events": [
            {"ts": stamp(-60), "event": "worker_started", "mode": "observe"},
            {"ts": stamp(-50), "event": "zz_never_seen_kind", "reason": "because_reasons"},
            {"ts": stamp(-40), "event": "strategy_confirmed", "symbol": "BTCUSDT", "strategy_id": "strat-777"},
            {"ts": "not-a-time", "event": "bad_time_event"},
            {"ts": stamp(3600), "event": "future_event"},
        ]},
        "alerts": {"alerts": [
            {"id": 12, "event": "worker_stalled", "state": "pending", "attempts": 3,
             "last_error": "HTTP 500 from webhook", "created_at": stamp(-30), "delivered_at": None},
            {"id": 11, "event": "halt", "state": "delivered", "attempts": 1, "last_error": None,
             "created_at": stamp(-300), "delivered_at": stamp(-299)},
        ]},
        "journal": {"submissions": [{"client_id": LONG_ID, "symbol": "ETHUSDT", "state": "unknown",
                                     "strategy_id": None, "created_at": "2026-11-24 09:59:00"}]},
        "signals": {"decisions": [{"recorded_at": stamp(-20), "symbol": "*", "stage": "universe",
                                   "reason": "no_eligible_market", "detail": "fail_closed"}]},
    }


def put(routes: dict) -> None:
    for name, value in routes.items():
        SNAPSHOT["/api/" + name] = value
    broadcast_live()


def sign_in(context, base, password, lang="en"):
    page = context.new_page()
    page.goto(base + "/dashboard")
    page.locator("input[type=password], .command-identity").first.wait_for()
    if page.locator("input[type=password]").count():
        page.locator("input[type=password]").fill(password)
        page.locator("form button").last.click()
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE")
    expect(page.locator(".live-badge")).to_have_text("Live")
    if lang == "id":
        page.locator(".language-button").click()
        expect(page.locator("html")).to_have_attribute("lang", "id")
    return page


def sign_in_polling(context, base, password):
    page = context.new_page()
    page.goto(base + "/dashboard")
    page.locator("input[type=password]").fill(password)
    page.locator("form button").last.click()
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE")
    return page


def open_tab(page, name):
    page.goto(page.url.split("#")[0] + "#preferences")
    page.get_by_role("button", name=name, exact=True).click()
    expect(page.get_by_role("button", name=name, exact=True)).to_have_attribute("aria-pressed", "true")


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def small_targets(page, selector):
    return page.evaluate(
        """selector => [...document.querySelectorAll(selector)].filter(el => el.offsetParent)
              .map(el => [el.textContent.trim().slice(0, 30), Math.round(el.getBoundingClientRect().height)])
              .filter(([, h]) => h < 44)""", selector)


def flt(page, name):
    return page.locator(".act-filters label", has=page.locator(".field-label", has_text=re.compile(f"^{name}$"))).locator("select")


def kinds(page):
    return page.locator(".act-list .act-kind").all_text_contents()


def verify(browser, base, password):
    original = {key: SNAPSHOT.get(key) for key in (f"/api/{name}" for name in
                ("status", "events", "alerts", "journal", "signals", "breakers", "risk"))}
    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce",
                                  timezone_id="Asia/Jakarta")
    errors, mutations, paths = [], [], set()
    context.on("page", lambda p: p.on("pageerror", lambda exc: errors.append(str(exc))))

    def track(request):
        path = request.url.split(base)[-1].split("?")[0]
        if request.method == "GET":
            paths.add(path)
        elif not path.endswith("/api/login"):
            mutations.append((request.method, path))

    context.on("request", track)
    page = sign_in(context, base, password)
    put(timeline_routes())

    # Timeline from the worker's own routes
    open_tab(page, "Activity")
    expect(page.locator(".act-item")).to_have_count(10)
    timed, untimed = page.locator(".act-item[data-timed=yes]"), page.locator(".act-item[data-timed=no]")
    expect(timed).to_have_count(6)
    expect(untimed).to_have_count(4)
    assert kinds(page)[:6] == ["universe", "worker stalled", "strategy confirmed", "zz never seen kind",
                               "worker started", "halt"], kinds(page)
    expect(page.locator(".act-time time").first).to_contain_text("GMT+7")
    for text in untimed.locator(".act-time").all_text_contents():
        assert text == "Time not reported", text
    expect(page.get_by_role("heading", name="Time not reported")).to_be_visible()
    expect(page.locator(".act-item", has_text="bad time event")).to_have_attribute("data-timed", "no")
    expect(page.locator(".act-item", has_text="future event")).to_have_attribute("data-timed", "no")
    stalled = page.locator(".act-item", has_text="worker stalled")
    expect(stalled).to_contain_text("HTTP 500 from webhook")
    expect(stalled).to_contain_text("Alert ID")
    expect(stalled.locator(".badge")).to_have_text("Warning")
    unknown = page.locator(".act-item", has_text="zz never seen kind")
    expect(unknown).to_contain_text("because reasons")
    expect(unknown.locator("dl div", has_text="Severity").locator("dd")).to_have_text("Not reported")
    expect(page.locator(".act-item", has_text="strategy confirmed")).to_contain_text("strat-777")
    submission = page.locator(".act-item", has_text="submission")
    expect(submission).to_contain_text("Client ID")
    expect(submission).not_to_contain_text("Strategy ID")
    expect(submission.locator("code.mono-break")).to_have_text(LONG_ID)
    expect(page.locator(".act-item", has_text="universe").locator("dl div", has_text="Reference")
           .locator("dd")).to_have_text("Not reported")
    ids = set(page.locator(".act-item code.mono-break").all_text_contents())
    assert ids == {"12", "11", "strat-777", LONG_ID}, ids
    breaker = page.locator(".act-item[data-source=breakers]")
    expect(breaker).to_have_count(1)
    expect(breaker.locator(".act-time")).to_have_text("Time not reported")

    sources = page.locator(".act-sources li")
    expect(sources).to_have_count(5)
    expect(sources.nth(0)).to_contain_text("Latest 5 reported")
    expect(sources.nth(1)).to_contain_text("Latest 2 reported")
    expect(sources.nth(2)).to_contain_text("Latest 1 reported")
    expect(page.locator(".act-sources li[data-state=confirmed]")).to_have_count(5)
    expect(page.locator(".act-count")).to_contain_text("Showing 10 / 10 · in loaded window")

    # Local filters, counts stay labelled as the loaded window
    flt(page, "Source").select_option("alerts")
    expect(page.locator(".act-item")).to_have_count(2)
    expect(page.locator(".act-count")).to_contain_text("Showing 2 / 10 · in loaded window")
    flt(page, "Outcome").select_option("pending")
    expect(page.locator(".act-item")).to_have_count(1)
    page.get_by_role("button", name="Clear filters").click()
    expect(page.locator(".act-item")).to_have_count(10)
    flt(page, "Kind").select_option("universe")
    expect(page.locator(".act-item")).to_have_count(1)
    page.get_by_role("button", name="Clear filters").click()
    assert not small_targets(page, ".act-filters select, .act-clear, .sections button")

    # A confirmed empty source and a failed source are reported differently
    put({"alerts": {"alerts": []}, "events": {"as_of": stamp(), "events": []}})
    expect(page.locator(".act-sources li").nth(1)).to_contain_text("Confirmed empty")
    expect(page.locator(".act-item[data-source=alerts]")).to_have_count(0)
    put(timeline_routes())

    # Overview log links to Activity
    page.goto(base + "/dashboard#overview")
    page.get_by_role("link", name="Open Activity").click()
    expect(page.get_by_role("button", name="Activity", exact=True)).to_have_attribute("aria-pressed", "true")

    # Risk & health from the real stream: clear only when every read agrees
    put(healthy_routes())
    open_tab(page, "Risk & health")
    expect(page.locator(".risk-page .card-head .badge").first).to_have_text("No incident reported")
    expect(page.locator(".rk-incidents")).to_have_count(0)
    put({**healthy_routes(), "breakers": {"halt": True, "risk_permitted": False, "reason": "halt"}})
    expect(page.locator(".rk-incidents")).to_contain_text("HALT is active")
    expect(page.locator(".risk-page .card-head .badge").first).to_have_text("Incident")
    put({**healthy_routes(), "status": {**healthy_status(), "providers": {"nvidia": {
        "state": "cooldown", "model": "qa-model", "until": stamp(120)}}}})
    expect(page.locator(".rk-incidents")).to_contain_text("AI provider needs attention")
    put(healthy_routes())
    expect(page.locator(".risk-page .card-head .badge").first).to_have_text("No incident reported")

    # OBSERVE blocks entries by design: neutral chip, no incident, never "clear"
    chip = page.get_by_text("Observe mode · entries blocked by design")
    for reason in ("observe_without_account", "observe"):
        put(observe_routes(reason))
        page.goto(base + "/dashboard#overview")
        expect(page.locator(".incident-strip")).to_have_count(0)
        open_tab(page, "Risk & health")
        expect(chip).to_be_visible()
        expect(page.locator(".rk-incidents")).to_have_count(0)
        expect(page.locator(".risk-page .card-head .badge").first).to_have_text("Not verified")
        expect(page.get_by_text("No incident reported")).to_have_count(0)
        if reason == "observe_without_account" and getenv("WARP_QA_SCREENSHOTS"):
            page.screenshot(path=str(Path(getenv("WARP_QA_SCREENSHOTS")) / "p4c-risk-observe-390-en.png"))
    for width in (320, 390):
        page.set_viewport_size({"width": width, "height": 844})
        assert overflow(page) <= 0, width
        box = chip.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (width, box)
    page.set_viewport_size({"width": 320, "height": 844})
    page.locator(".language-button").click()
    expect(page.get_by_text("Mode observe · entri diblokir sesuai desain")).to_be_visible()
    assert overflow(page) <= 0
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator(".language-button").click()
    expect(page.locator("html")).to_have_attribute("lang", "en")
    put({**observe_routes("observe_without_account"), "status": {**observe_routes("observe_without_account")["status"], "providers": {
        "nvidia": {"state": "cooldown", "model": "qa-model", "until": stamp(120)}}}})
    page.goto(base + "/dashboard#overview")
    expect(page.locator(".incident-strip [data-incident=provider]")).to_be_visible()
    expect(page.locator(".incident-strip [data-incident=risk]")).to_have_count(0)
    expect(page.locator(".incident-strip").get_by_text("Observe mode · entries blocked by design")).to_be_visible()
    if getenv("WARP_QA_SCREENSHOTS"):
        page.screenshot(path=str(Path(getenv("WARP_QA_SCREENSHOTS")) / "p4c-incident-observe-390-en.png"))
    page.set_viewport_size({"width": 320, "height": 844})
    assert overflow(page) <= 0
    assert not small_targets(page, ".incident-strip a")
    page.set_viewport_size({"width": 390, "height": 844})
    paper = observe_routes("observe")
    put({**paper, "status": {**paper["status"], "mode": "paper"}})
    expect(page.locator(".incident-strip [data-incident=risk]")).to_be_visible()
    expect(page.locator(".incident-strip").get_by_text("Observe mode · entries blocked by design")).to_have_count(0)
    open_tab(page, "Risk & health")
    expect(chip).to_have_count(0)
    expect(page.locator(".risk-page .card-head .badge").first).to_have_text("Incident")
    put(healthy_routes())

    # Incident strip appears and disappears with the real state, and every entry opens the risk tab
    page.goto(base + "/dashboard#overview")
    expect(page.locator(".incident-strip")).to_have_count(0)
    put({"status": {**healthy_status(), "halt": True, "risk_permitted": False, "reason": "halt"}})
    strip = page.locator(".incident-strip")
    expect(strip.locator("[data-incident=halt]")).to_be_visible()
    assert not small_targets(page, ".incident-strip a")
    strip.locator("[data-incident=halt]").click()
    expect(page.get_by_role("button", name="Risk & health", exact=True)).to_have_attribute("aria-pressed", "true")
    page.goto(base + "/dashboard#overview")
    put(healthy_routes())
    expect(page.locator(".incident-strip")).to_have_count(0)

    # HALT keeps its typed confirmation
    open_tab(page, "Risk & health")
    page.get_by_role("button", name="HALT new entries", exact=True).click()
    confirm = page.locator(".confirm-box").get_by_role("button", name="HALT", exact=True)
    expect(confirm).to_be_disabled()
    page.locator(".confirm-box input").fill("HAL")
    expect(confirm).to_be_disabled()
    page.locator(".confirm-box input").fill("RESUME")
    expect(confirm).to_be_disabled()
    page.locator(".confirm-box input").fill("halt")
    expect(confirm).to_be_enabled()
    assert not mutations, mutations
    page.get_by_role("button", name="Cancel", exact=True).click()

    # HALT then RESUME: no ready state from the ack alone
    with tempfile.TemporaryDirectory() as directory:
        HEALTH["halt_path"] = str(Path(directory) / "HALT")
        try:
            put(healthy_routes())
            page.get_by_role("button", name="HALT new entries", exact=True).click()
            page.locator(".confirm-box input").fill("HALT")
            page.locator(".confirm-box").get_by_role("button", name="HALT", exact=True).click()
            expect(page.get_by_text("HALT is on.", exact=False)).to_be_visible()
            expect(page.locator(".rk-incidents")).to_contain_text("HALT is active")
            page.get_by_role("button", name="Resume entries", exact=True).click()
            page.locator(".confirm-box input").fill("RESUME")
            confirm = page.locator(".confirm-box").get_by_role("button", name="RESUME", exact=True)
            expect(confirm).to_be_enabled()
            confirm.click()
            expect(page.get_by_text("HALT is off.", exact=False)).to_be_visible()
            gate = page.locator(".ledger:not(.rk-row)", has_text="Entry gate").locator("b")
            expect(gate).to_have_text("Awaiting fresh snapshot")
            expect(page.locator(".ledger:not(.rk-row)", has_text="Execution").locator("b")).to_have_text("Awaiting fresh snapshot")
            expect(page.locator(".risk-page .card-head .badge").first).to_have_text("Awaiting fresh snapshot")
            expect(page.locator(".risk-page .card-head .badge").first).not_to_have_text("No incident reported")
            put({"status": healthy_status(stamp(1))})
            expect(gate).not_to_have_text("Awaiting fresh snapshot")
            expect(page.locator(".risk-awaiting")).to_have_count(0)
            expect(page.locator(".risk-page .card-head .badge").first).to_have_text("No incident reported")
        finally:
            HEALTH.pop("halt_path", None)
    assert [m for m in mutations if m[0] != "POST"] == [], mutations
    assert {m[1] for m in mutations} == {"/api/pause", "/api/resume"}, mutations

    # Settings notes and readable save errors
    open_tab(page, "Rules")
    expect(page.locator(".setting-note")).to_contain_text("A saved setting is not an order")
    context.route(re.compile(r".*/api/rules$"), lambda route: route.fulfill(status=400, json={"error": "invalid_rules"})
                  if route.request.method == "POST" else route.continue_())
    page.get_by_role("button", name="Save rules").click()
    expect(page.get_by_text("The worker rejected these rule values.")).to_be_visible()
    context.unroute(re.compile(r".*/api/rules$"))
    page.get_by_role("button", name="Accounts & capital", exact=True).click()
    expect(page.locator(".setting-note")).to_contain_text("A saved setting is not an order")
    page.close()

    # A dropped stream is an incident, never a ready state, and clears when the stream returns
    put(healthy_routes())
    link = {"mode": "hello"}

    def stream_route(route):
        if link["mode"] == "live":
            route.continue_()
        elif link["mode"] == "hello":
            link["mode"] = "drop"
            route.fulfill(status=200, headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
                          body=encode_event("hello", {"ts": stamp()}))
        else:
            route.abort()

    dropping = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    dropping.route(re.compile(r".*/api/stream$"), stream_route)
    page = sign_in_polling(dropping, base, password)
    page.goto(base + "/dashboard#overview")
    expect(page.locator(".incident-strip [data-incident=stream]")).to_be_visible(timeout=20000)
    expect(page.locator(".live-badge")).not_to_have_text("Live")
    open_tab(page, "Risk & health")
    expect(page.locator(".risk-page .card-head .badge").first).to_have_text("Incident")
    expect(page.locator(".risk-page .card-head", has_text="Live stream").locator(".badge")).to_have_text(
        "Disconnected · HTTP polling")
    link["mode"] = "live"
    expect(page.locator(".live-badge")).to_have_text("Live", timeout=45000)
    expect(page.locator(".risk-page .card-head .badge").first).to_have_text("No incident reported")
    page.goto(base + "/dashboard#overview")
    expect(page.locator(".incident-strip")).to_have_count(0)
    dropping.close()

    SNAPSHOT["/api/status"] = healthy_status(stamp(-1500))
    stale = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    page = sign_in(stale, base, password)
    page.goto(base + "/dashboard#overview")
    expect(page.locator(".incident-strip [data-incident=statusStale]")).to_be_visible()
    open_tab(page, "Risk & health")
    expect(page.locator(".risk-page .card-head .badge").first).not_to_have_text("No incident reported")
    stale.close()
    put(healthy_routes())

    polling = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    polling.route(re.compile(r".*/api/(stream|alerts)$"), lambda route: route.abort() if route.request.url.endswith("stream")
                  else route.fulfill(status=503, json={"error": "down"}))
    page = sign_in_polling(polling, base, password)
    open_tab(page, "Activity")
    expect(page.locator(".act-sources li").nth(1)).to_contain_text("Refresh failed")
    expect(page.locator(".act-sources li").nth(1)).not_to_contain_text("Confirmed")
    expect(page.locator(".act-sources li").nth(2)).to_contain_text("Confirmed")
    expect(page.locator(".act-item[data-source=alerts]")).to_have_count(0)
    polling.close()

    failing = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    failing.route(re.compile(r".*/api/(stream|status)$"), lambda route: route.abort() if route.request.url.endswith("stream")
                  else route.fulfill(status=503, json={"error": "down"}))
    page = failing.new_page()
    page.goto(base + "/dashboard#overview")
    page.locator("input[type=password]").fill(password)
    page.locator("form button").last.click()
    expect(page.locator(".incident-strip [data-incident=statusFailed]")).to_be_visible(timeout=20000)
    open_tab(page, "Risk & health")
    expect(page.locator(".risk-page .card-head .badge").first).not_to_have_text("No incident reported")
    expect(page.locator(".risk-page .card-head .badge").first).not_to_have_text("Awaiting fresh snapshot")
    failing.close()

    # Layout, language and navigation with populated rows
    page = sign_in(context, base, password)
    put(timeline_routes())
    shots = getenv("WARP_QA_SCREENSHOTS")
    for lang in ("en", "id"):
        if lang == "id":
            page.locator(".language-button").click()
            expect(page.locator("html")).to_have_attribute("lang", "id")
        for width in (320, 390, 1280):
            page.set_viewport_size({"width": width, "height": 900 if width > 900 else 844})
            for tab in (("Activity", "Aktivitas"), ("Risk & health", "Risiko & kesehatan")):
                open_tab(page, tab[lang == "id"])
                page.evaluate("document.fonts.ready")
                assert overflow(page) <= 0, (lang, width, tab)
                assert not small_targets(page, ".act-filters select, .act-clear, .risk-page .btn, button.btn"), (lang, width, tab)
                text = page.locator("main").inner_text()
                assert not re.search(r"\b(act|rk|inc)[A-Z][A-Za-z]+\b", text), (lang, width)
                if lang == "id" and tab[0] == "Activity":
                    expect(page.locator(".act-count")).to_contain_text("di jendela yang dimuat")
                if shots and tab[0] == "Activity" and (lang, width) in {("en", 320), ("en", 390), ("id", 320), ("en", 1280)}:
                    page.screenshot(path=str(Path(shots) / f"p4c-activity-{width}-{lang}.png"), full_page=True)
            nav = page.locator("nav:visible")
            expect(nav).to_have_count(1)
            expect(nav.locator("a")).to_have_count(5 if width <= 900 else 6)
    page.set_viewport_size({"width": 390, "height": 844})
    if shots:
        page.locator(".language-button").click()
        put({"status": {**healthy_status(), "halt": True, "risk_permitted": False, "reason": "halt"},
             "breakers": {"halt": True, "risk_permitted": False, "reason": "halt"}})
        page.goto(base + "/dashboard#overview")
        expect(page.locator(".incident-strip [data-incident=halt]")).to_be_visible()
        page.screenshot(path=str(Path(shots) / "p4c-incident-390-en.png"))
    page.close()

    for path in paths:
        assert path in KNOWN_GETS or not path.startswith("/api/"), path
    assert not errors, errors
    for key, value in original.items():
        if value is not None:
            SNAPSHOT[key] = value
    assert operator.HEALTH.get("halt_path") is None
    context.close()


if __name__ == "__main__":
    main(verify)
