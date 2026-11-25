"""P5 performance measurements on an emulated low-end Android (4x CPU throttle) against the in-process OBSERVE worker.

Measures, and reports as numbers (no product optimisation is made here):
  - room animation CPU while idle, and under ambient telemetry, with the properties each animation touches
  - long tasks during load, route switching and an SSE burst
  - frame churn under an SSE burst injected through warp3c.live.BUS.publish
  - listener, timer, EventSource and DOM counts before and after 10 and 50 route switches
  - time to first position list on throttled CPU, with and without Slow 3G
  - first-load transfer as the worker actually serves it (no compression)

Hard gates are structural (no unbounded growth, one stream, compositor-only room animation, the UI converges on the
last frame). Timings vary by machine, so they are reported with generous sanity limits only.
Run: python web/qa/p5_perf.py   (WARP_QA_REPORT=FILE writes the numbers as JSON)
"""
import json
import os
import statistics
import time

from account_reads import main
from p5_common import (
    Recorder,
    SnapshotGuard,
    broker_fixtures,
    healthy_routes,
    healthy_status,
    put,
    serve_fixtures,
    stamp,
    wait_subscribers,
)
from playwright.sync_api import expect

from warp3c.live import BUS

PIXEL = dict(viewport={"width": 412, "height": 915}, device_scale_factor=2.625, is_mobile=True, has_touch=True,
             user_agent=("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/157.0.0.0 Mobile Safari/537.36"))
SLOW_3G = dict(offline=False, latency=400, downloadThroughput=int(500 * 1024 / 8), uploadThroughput=int(400 * 1024 / 8))
COMPOSITOR = {"transform", "opacity", "filter", "offset", "translate", "scale", "rotate", "composite", "easing"}

INIT = """
(() => {
  const p5 = window.__p5 = { longtasks: [], events: {}, es: { created: 0, open: 0 }, intervals: new Map(),
                              mutations: 0, frames: [], firstList: null, firstPicker: null, lcp: null };
  try {
    new PerformanceObserver(list => { for (const e of list.getEntries()) p5.longtasks.push([e.startTime, e.duration]); })
      .observe({ type: 'longtask', buffered: true });
    new PerformanceObserver(list => { for (const e of list.getEntries()) p5.lcp = e.startTime; })
      .observe({ type: 'largest-contentful-paint', buffered: true });
  } catch (_) {}
  const Native = window.EventSource;
  window.EventSource = class extends Native {
    constructor(...args) {
      super(...args);
      p5.es.created++; p5.es.open++;
      const add = this.addEventListener.bind(this);
      this.addEventListener = (type, fn, options) => add(type, (event) => {
        p5.events[type] = (p5.events[type] || 0) + 1; fn(event);
      }, options);
      const close = this.close.bind(this);
      let closed = false;
      this.close = () => { if (!closed) { closed = true; p5.es.open--; } close(); };
    }
  };
  const setI = window.setInterval.bind(window), clearI = window.clearInterval.bind(window);
  window.setInterval = (fn, ms, ...rest) => { const id = setI(fn, ms, ...rest); p5.intervals.set(id, ms); return id; };
  window.clearInterval = (id) => { p5.intervals.delete(id); clearI(id); };
  new MutationObserver(records => {
    p5.mutations += records.length;
    if (p5.firstPicker === null && document.querySelector('.account-picker')) p5.firstPicker = performance.now();
    if (p5.firstList === null && document.querySelector('.position-card')) p5.firstList = performance.now();
  }).observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
})();
"""

START_FRAMES = """
(ms) => {
  const out = window.__frames = { stamps: [], done: false };
  const stop = performance.now() + ms;
  const tick = (now) => { out.stamps.push(now); now < stop ? requestAnimationFrame(tick) : (out.done = true); };
  requestAnimationFrame(tick);
}
"""


def frames_done(page):
    page.wait_for_function("window.__frames && window.__frames.done", timeout=30000)
    return page.evaluate("window.__frames.stamps")


ANIMATIONS = """
() => document.getAnimations().filter(a => a.playState === 'running').map(a => ({
  name: a.animationName || a.constructor.name,
  props: [...new Set((a.effect && a.effect.getKeyframes ? a.effect.getKeyframes() : []).flatMap(k => Object.keys(k)))]
    .filter(k => !['offset', 'computedOffset', 'easing', 'composite'].includes(k)),
}))
"""


def session(browser, base, password, *, throttle=4, network=None, lang="en"):
    context = browser.new_context(reduced_motion="no-preference", timezone_id="Asia/Jakarta", **PIXEL)
    recorder = Recorder(context, base)
    context.add_init_script(INIT)
    page = context.new_page()
    cdp = context.new_cdp_session(page)
    cdp.send("Performance.enable")
    if throttle:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": throttle})
    if network:
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", network)
    return context, page, cdp, recorder


def login(page, base, password):
    page.goto(base + "/dashboard", wait_until="domcontentloaded")
    page.locator("input[type=password]").fill(password)
    page.locator("form button").last.click()
    expect(page.locator(".command-identity .badge")).to_contain_text("OBSERVE", timeout=60000)
    expect(page.locator(".live-badge")).to_have_text("Live", timeout=60000)


def metrics(cdp):
    return {item["name"]: item["value"] for item in cdp.send("Performance.getMetrics")["metrics"]}


def cpu_window(page, cdp, seconds):
    """Main-thread busy fraction over a wall-clock window, from CDP Performance counters."""
    before, started = metrics(cdp), time.monotonic()
    page.wait_for_timeout(int(seconds * 1000))
    after, wall = metrics(cdp), time.monotonic() - started
    delta = lambda name: after[name] - before[name]  # noqa: E731
    return {
        "wall_s": round(wall, 2),
        "main_thread_busy_pct": round(100 * delta("TaskDuration") / wall, 1),
        "script_pct": round(100 * delta("ScriptDuration") / wall, 1),
        "layout_pct": round(100 * delta("LayoutDuration") / wall, 1),
        "style_pct": round(100 * delta("RecalcStyleDuration") / wall, 1),
    }


def counters(cdp):
    cdp.send("HeapProfiler.collectGarbage")
    dom = cdp.send("Memory.getDOMCounters")
    out = {"documents": dom["documents"], "nodes": dom["nodes"], "js_event_listeners": dom["jsEventListeners"]}
    listeners = {}
    for expression in ("window", "document"):
        handle = cdp.send("Runtime.evaluate", {"expression": expression})["result"]["objectId"]
        for item in cdp.send("DOMDebugger.getEventListeners", {"objectId": handle})["listeners"]:
            key = f"{expression}.{item['type']}"
            listeners[key] = listeners.get(key, 0) + 1
    out["global_listeners"] = dict(sorted(listeners.items()))
    return out


def probe(page):
    return page.evaluate(
        """() => ({ intervals: window.__p5.intervals.size, es_open: window.__p5.es.open, es_created: window.__p5.es.created,
                    longtasks: window.__p5.longtasks.length, events: {...window.__p5.events},
                    mutations: window.__p5.mutations })"""
    )


def long_task_stats(page, since_ms=0):
    tasks = page.evaluate("window.__p5.longtasks")
    tasks = [duration for start, duration in tasks if start >= since_ms]
    return {"count": len(tasks), "max_ms": round(max(tasks), 0) if tasks else 0, "total_ms": round(sum(tasks), 0)}


def frame_stats(stamps):
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    if not gaps:
        return {"frames": 0}
    return {"frames": len(stamps), "fps": round(1000 * len(gaps) / sum(gaps), 1), "median_gap_ms": round(statistics.median(gaps), 1),
            "max_gap_ms": round(max(gaps), 1), "gaps_over_50ms": sum(1 for g in gaps if g > 50),
            "gaps_over_100ms": sum(1 for g in gaps if g > 100)}


def room_cpu(browser, base, password, report):
    context, page, cdp, recorder = session(browser, base, password)
    put(healthy_routes())
    login(page, base, password)
    page.goto(base + "/dashboard#overview")
    page.wait_for_timeout(1500)
    report["overview_idle"] = cpu_window(page, cdp, 5)
    page.goto(base + "/dashboard#desk")
    expect(page.locator(".trading-room")).to_be_visible()
    page.wait_for_timeout(1500)
    report["room_idle_observe"] = cpu_window(page, cdp, 5)
    report["room_idle_observe"]["animations"] = page.evaluate(ANIMATIONS)

    paper = {**healthy_status(), "mode": "paper", "execution_permitted": True, "gating": "current"}

    def feed():
        now = stamp()
        BUS.publish("snapshot", {"routes": {"/api/status": {**paper, "published_at": now},
                                            "/api/events": {"events": [{"event": "strategy_confirmed", "ts": now}], "as_of": now}},
                                 "published_at": now})

    feed()
    page.wait_for_timeout(1000)
    ambient = page.locator("[data-ambient=on]").count()
    assert ambient > 0, "ambient telemetry never reached the room"
    report["room_ambient"] = cpu_window(page, cdp, 5)
    report["room_ambient"]["ambient_stations"] = ambient
    animations = page.evaluate(ANIMATIONS)
    report["room_ambient"]["animations"] = animations
    properties = {prop for item in animations for prop in item["props"]}
    report["room_ambient"]["animated_properties"] = sorted(properties)
    assert animations, "no running animation under ambient telemetry"
    assert properties <= COMPOSITOR, f"room animation touches layout or paint properties: {sorted(properties - COMPOSITOR)}"
    recorder.assert_clean()
    context.close()


def burst(browser, base, password, report, hash_="overview", key="sse_burst"):
    context, page, cdp, recorder = session(browser, base, password)
    put(healthy_routes())
    login(page, base, password)
    page.goto(base + "/dashboard#" + hash_)
    page.wait_for_timeout(1500)
    if hash_ == "desk":
        expect(page.locator(".trading-room")).to_be_visible()
    else:
        page.goto(base + "/dashboard#overview")
    before = probe(page)
    started = page.evaluate("performance.now()")

    # Normal producer cadence for reference: one snapshot per second, plus prices every 3 seconds.
    page.evaluate(START_FRAMES, 5200)
    for index in range(5):
        put({"status": healthy_status(stamp(), equity=f"{1000 + index}.00", pnl_today="1.00")})
        page.wait_for_timeout(1000)
    normal = frame_stats(frames_done(page))
    mid = probe(page)

    # Burst: 300 snapshots plus 300 price frames and 300 position frames in under two seconds.
    page.evaluate(START_FRAMES, 4500)
    sent = 0
    began = time.monotonic()
    for index in range(300):
        now = stamp(index / 1000)
        BUS.publish("snapshot", {"routes": {"/api/status": healthy_status(now, equity=f"{2000 + index}.00", pnl_today=f"{index}.00")},
                                 "published_at": now})
        BUS.publish("prices", {"symbols": {"BTCUSDT": {"last": f"{60000 + index}.10", "change_pct": "0.5"}},
                               "as_of": now, "error": None, "limit_seconds": 30})
        BUS.publish("positions", {"positions": [], "as_of": now, "errors": []})
        sent += 3
        if index % 25 == 24:
            time.sleep(0.05)
    burst_s = time.monotonic() - began
    page.wait_for_timeout(2500)
    churn = frame_stats(frames_done(page))
    after = probe(page)

    received = sum(after["events"].values()) - sum(mid["events"].values())
    report[key] = {
        "normal_cadence": {**normal, "long_tasks": mid["longtasks"] - before["longtasks"],
                           "dom_mutations": mid["mutations"] - before["mutations"]},
        "burst_frames_sent": sent, "burst_send_s": round(burst_s, 2), "burst_events_received_by_browser": received,
        "burst": churn, "long_tasks_during_burst": after["longtasks"] - mid["longtasks"],
        "dom_mutations_during_burst": after["mutations"] - mid["mutations"],
        "long_task_summary_all": long_task_stats(page, started),
    }
    # The UI converges on the last frame it was sent, so dropping or coalescing frames never leaves it behind.
    if hash_ == "overview":
        expect(page.locator(".metrics")).to_contain_text("2,299")
    else:
        expect(page.locator(".status-strip")).to_be_visible()
    assert after["es_open"] == 1, after
    assert not recorder.errors, recorder.errors
    recorder.assert_clean()
    context.close()


def leaks(browser, base, password, report):
    context, page, cdp, recorder = session(browser, base, password, throttle=4)
    put(healthy_routes())
    serve_fixtures(page, base, broker_fixtures())
    login(page, base, password)
    routes = ("signals", "assets", "screener", "preferences", "desk", "overview")
    for route in routes:  # warm every lazy chunk and cache once, then take the baseline
        page.locator(f'nav:visible a[href="#{route}"], .overview-room-link').first.click() if route != "desk" else page.goto(base + "/dashboard#desk")
        page.wait_for_timeout(250)
    page.goto(base + "/dashboard#overview")
    page.wait_for_timeout(800)
    wait_subscribers(1)
    base_count, base_probe = counters(cdp), probe(page)
    rows = {"baseline": {**base_count, **{k: base_probe[k] for k in ("intervals", "es_open", "es_created")}}}
    done = 0
    for target in (10, 50):
        while done < target:
            route = routes[done % len(routes)]
            if route == "desk":
                page.goto(base + "/dashboard#desk")
            else:
                page.evaluate("route => { location.hash = route }", route)
            page.wait_for_timeout(120)
            done += 1
        page.goto(base + "/dashboard#overview")
        page.wait_for_timeout(600)
        wait_subscribers(1)
        count, state = counters(cdp), probe(page)
        rows[f"after_{target}_switches"] = {**count, **{k: state[k] for k in ("intervals", "es_open", "es_created")}}
        rows[f"after_{target}_switches"]["bus_subscribers"] = len(BUS._subs)
    report["listeners"] = rows
    for key in ("after_10_switches", "after_50_switches"):
        later = rows[key]
        assert later["es_open"] == 1 and later["es_created"] == rows["baseline"]["es_created"], (key, later)
        assert later["intervals"] <= rows["baseline"]["intervals"] + 1, (key, later["intervals"], rows["baseline"]["intervals"])
        assert later["js_event_listeners"] <= rows["baseline"]["js_event_listeners"] * 1.10 + 20, (key, later, rows["baseline"])
        assert later["nodes"] <= rows["baseline"]["nodes"] * 1.25 + 200, (key, later, rows["baseline"])
        assert later["global_listeners"] == rows["baseline"]["global_listeners"], (key, later["global_listeners"])
        assert later["documents"] <= rows["baseline"]["documents"] + 1
    # Negative control: a listener leak of five handlers must be visible to the same counters.
    page.evaluate("for (let i = 0; i < 5; i++) window.addEventListener('hashchange', () => {})")
    leaked = counters(cdp)
    assert leaked["global_listeners"]["window.hashchange"] == rows["baseline"]["global_listeners"]["window.hashchange"] + 5, leaked
    assert leaked["js_event_listeners"] >= rows["baseline"]["js_event_listeners"] + 5
    report["listener_detector_control"] = "five injected hashchange listeners were counted"
    growth = rows["after_50_switches"]["js_event_listeners"] - rows["after_10_switches"]["js_event_listeners"]
    assert growth <= 20, f"listeners still growing between 10 and 50 switches: +{growth}"
    recorder.assert_clean()
    context.close()


def first_list(browser, base, password, label, throttle, network, report, runs=3):
    """Time from navigation to the first position row, on a cold HTTP cache."""
    results = []
    for _ in range(runs):
        context, page, cdp, recorder = session(browser, base, password, throttle=throttle, network=network)
        serve_fixtures(page, base, broker_fixtures())
        assert context.request.post(base + "/api/login", data={"password": password}).ok
        page.goto(base + "/dashboard#preferences", wait_until="commit")
        page.locator(".position-card").first.wait_for(timeout=120000)
        timing = page.evaluate(
            """() => { const nav = performance.getEntriesByType('navigation')[0]; const paint = performance.getEntriesByName('first-contentful-paint')[0];
                       return { first_list_ms: Math.round(window.__p5.firstList), first_picker_ms: Math.round(window.__p5.firstPicker),
                                fcp_ms: paint ? Math.round(paint.startTime) : null, lcp_ms: window.__p5.lcp ? Math.round(window.__p5.lcp) : null,
                                dom_content_loaded_ms: Math.round(nav.domContentLoadedEventEnd), load_ms: Math.round(nav.loadEventEnd) }; }"""
        )
        timing["long_tasks"] = long_task_stats(page)
        results.append(timing)
        recorder.assert_clean()
        context.close()

    def median(key):
        values = [item[key] for item in results if item.get(key) is not None]
        return statistics.median(values) if values else None

    report[label] = {"runs": runs, "median_first_list_ms": median("first_list_ms"), "median_fcp_ms": median("fcp_ms"),
                     "median_lcp_ms": median("lcp_ms"), "median_load_ms": median("load_ms"),
                     "worst_long_task_ms": max(item["long_tasks"]["max_ms"] for item in results), "all": results}


def transfer(browser, base, password, report):
    """What a phone downloads on first load, as served: raw bytes and the Content-Encoding header."""
    context = browser.new_context(**PIXEL)
    assert context.request.post(base + "/api/login", data={"password": password}).ok
    page = context.new_page()
    sizes = {}

    def record(response):
        path = response.url.removeprefix(base).split("?")[0]
        if path.startswith("/assets/") or path in {"/dashboard"}:
            try:
                body = response.body()
            except Exception:
                return
            sizes[path] = {"bytes": len(body), "encoding": response.headers.get("content-encoding"),
                           "type": response.headers.get("content-type"), "cache": response.headers.get("cache-control")}

    page.on("response", record)
    page.goto(base + "/dashboard#overview", wait_until="networkidle")
    page.wait_for_timeout(500)
    total = sum(item["bytes"] for item in sizes.values())
    report["first_load_transfer"] = {"files": sizes, "total_bytes": total,
                                     "compressed": any(item["encoding"] for item in sizes.values())}
    context.close()


def verify(browser, base, password):
    report: dict = {"cpu_throttle": 4, "device": "Pixel-class 412x915 DPR 2.625, touch, mobile UA",
                    "gpu": "headless software raster (--disable-gpu): paint cost is overstated versus a phone GPU",
                    "host_cpus": os.cpu_count()}
    with SnapshotGuard():
        room_cpu(browser, base, password, report)
        burst(browser, base, password, report)
        burst(browser, base, password, report, "desk", "sse_burst_room")
        leaks(browser, base, password, report)
        first_list(browser, base, password, "first_list_cpu4x", 4, None, report)
        first_list(browser, base, password, "first_list_cpu4x_slow3g", 4, SLOW_3G, report, runs=2)
        first_list(browser, base, password, "first_list_unthrottled", 0, None, report)
        transfer(browser, base, password, report)
    print(json.dumps(report, indent=1))
    path = os.getenv("WARP_QA_REPORT")
    if path:
        with open(path, "w") as handle:
            json.dump(report, handle, indent=1)
    # Sanity limits only. They catch a hang, not a regression of a few percent.
    assert report["room_ambient"]["main_thread_busy_pct"] < 95, report["room_ambient"]
    for key in ("sse_burst", "sse_burst_room"):
        assert report[key]["burst"]["max_gap_ms"] < 5000, report[key]["burst"]


if __name__ == "__main__":
    main(verify)
