"""P4A evidence and responsive checks on a credential-free local OBSERVE worker."""
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from account_reads import main
from playwright.sync_api import expect


def verify(browser, base, password):
    context = browser.new_context(timezone_id="Asia/Jakarta", reduced_motion="reduce")
    assert context.request.post(base + "/api/login", data={"password": password}).ok
    page = context.new_page()
    writes, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: writes.append(request.method) if request.method != "GET" else None)
    status = context.request.get(base + "/api/status").json()
    assert status["mode"] == "observe"
    now = datetime.now(timezone.utc)
    page.clock.install(time=now)
    at = now.isoformat()
    recorded = (now - timedelta(minutes=2)).isoformat()
    worker = {**status, "published_at": at, "universe": ["SOLUSDT", "BTCUSDT"],
              "pipeline": {**status["pipeline"], "screening": {"state": "ready", "source": "binance_spot_public", "count": 2}}}
    first = {"symbol": "SOLUSDT", "action": "BUY", "model_action": "BUY", "source": "llm evidence snapshot",
             "entry": "120.50", "stop": "118.00", "take": "125.00", "score": {"total": 82}, "fresh": True,
             "thesis": "Closed-candle trend passed the market check.", "as_of": at, "evidence_as_of": (now - timedelta(minutes=25)).isoformat(),
             "backtest_passed": True, "backtest_reason": "ok"}
    second = {**first, "symbol": "BTCUSDT", "score": {"total": 95}, "action": "WAIT", "backtest_passed": False,
              "entry": None, "backtest_reason": "too_few_trades", "thesis": None}
    payloads = {
        "status": worker, "opportunities": {"cards": [first, second]},
        "decisions": {"decisions": [{"symbol": "BTCUSDT", "status": "VETO", "reasons": ["spread", "secret_internal_code"], "warnings": []}]},
        "signals": {"decisions": [
            {"symbol": "BTCUSDT", "stage": "broker", "reason": "spread", "detail": "not_submitted", "recorded_at": recorded},
            {"symbol": "ETHUSDT", "stage": "universe", "reason": "outside_allowlist", "detail": "rejected", "recorded_at": recorded},
        ]},
        "screener": {"source": "tradingview", "fetched_at": at, "hits": [{"symbol": "ETHUSDT", "source": "tradingview"}],
                     "filters": context.request.get(base + "/api/screener").json()["filters"]},
    }
    page.route("**/api/stream", lambda route: route.abort())
    def serve(route):
        assert route.request.method == "GET"
        route.fulfill(json=payloads[route.request.url.rsplit("/", 1)[-1]])

    for path in payloads:
        page.route("**/api/" + path, serve)
    page.goto(base + "/dashboard#signals", wait_until="domcontentloaded")
    cards = page.locator(".p4-card")
    drawer = page.get_by_role("dialog")
    output = os.getenv("WARP_QA_SCREENSHOTS")
    for lang, width in (("en", 320), ("en", 390), ("en", 1280), ("id", 320)):
        page.set_viewport_size({"width": width, "height": 960 if width > 600 else 844})
        if lang == "id":
            page.locator(".language-button").click()
        en = lang == "en"
        expect(cards.locator("h2")).to_have_text(["SOLUSDT", "BTCUSDT"])
        expect(cards.first).to_contain_text("PROPOSED / NOT SUBMITTED" if en else "DIUSULKAN / BELUM DIKIRIM")
        expect(cards.last).to_contain_text("Not reported" if en else "Belum dilaporkan")
        expect(cards.first).to_contain_text("Rubric score" if en else "Skor rubrik")
        line = cards.first.locator(".p4-status")
        expect(line).to_contain_text("Fresh at worker publication" if en else "Terkini saat publikasi worker")
        expect(line.locator("time")).to_have_attribute("datetime", at)
        expect(line).to_contain_text("Evidence age 25 min" if en else "Usia bukti 25 menit")
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        clickable = page.locator("a, button, summary, [role=button], input[type=submit], [onclick]")
        for control in clickable.all():
            label = control.get_attribute("aria-label") or ""
            assert not re.search(r"order|submit|buy now|start live", control.inner_text() + " " + label, re.I)
        if output:
            directory = Path(output)
            directory.mkdir(parents=True, exist_ok=True)
            # Mark the capture itself so it cannot be mistaken for production telemetry.
            page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
            tag = page.evaluate_handle("""() => {
              const tag = document.createElement('span');
              tag.textContent = 'QA fixture · OBSERVE';
              tag.style.cssText = 'position:fixed;top:0;right:0;z-index:20000;background:#253442;color:white;padding:2px 7px;font:11px monospace;pointer-events:none';
              document.body.append(tag); return tag;
            }""")
            page.screenshot(path=str(directory / f"P4A-{width}-{lang.upper()}.png"))
            tag.evaluate("tag => tag.remove()")
        trigger = cards.last.get_by_role("button", name="Inspect AEGIS" if en else "Periksa AEGIS")
        trigger.focus()
        page.keyboard.press("Enter")
        expect(drawer).to_contain_text("VETO")
        expect(drawer).to_contain_text("Unlisted code (hidden)" if en else "Kode tidak terdaftar (disembunyikan)")
        expect(drawer).not_to_contain_text("secret_internal_code")
        expect(drawer.locator(".p4-facts code").first).to_have_text("spread")
        expect(drawer.locator(".p4-facts dt", has_text="Assessment time" if en else "Waktu penilaian").locator("+ dd")
               ).to_have_text("Not reported" if en else "Belum dilaporkan")
        event = drawer.locator(".p4-record", has_text="spread")
        expect(event.locator("time")).to_have_text(re.compile(r"GMT\+7|WIB"))
        expect(event.locator("time")).to_have_attribute("datetime", recorded)
        assert drawer.evaluate("el => el.scrollWidth <= el.clientWidth")
        page.keyboard.press("Escape")
        expect(trigger).to_be_focused()
    page.clock.fast_forward(60_000)
    expect(cards.first.locator(".p4-status")).to_contain_text("Usia bukti 26 menit")
    page.locator('nav:visible a[href="#screener"]').click()
    auto = page.locator(".p4-panel").first
    expect(auto.locator(".symbols")).to_contain_text("SOLUSDT")
    expect(auto.locator(".symbols")).not_to_contain_text("ETHUSDT")
    expect(auto.locator(".p4-record", has_text="ETHUSDT")).to_contain_text("outside_allowlist")
    page.locator(".p4-link summary").click()
    expect(page.locator(".p4-link a")).to_have_attribute("href", re.compile("BINANCE%3AETHUSDT"))
    worker["published_at"] = (now - timedelta(hours=2)).isoformat()
    page.goto(base + "/dashboard#signals", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    expect(cards.first.locator(".p4-status")).to_contain_text("Kedaluwarsa")
    expect(cards.locator(".badge-good")).to_have_count(0)
    expect(cards.get_by_text("DIUSULKAN / BELUM DIKIRIM")).to_have_count(0)
    assert not writes and not errors, (writes, errors)
    context.close()


if __name__ == "__main__":
    main(verify)
