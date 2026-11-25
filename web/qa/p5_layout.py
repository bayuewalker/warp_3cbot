"""P5 layout audit extension. Adds targets to the 168 in `account_reads.py --layout`, which this file leaves untouched.

New targets, each in EN and ID across the 12 route/tab views:
  Pixel-class Android (412x915, DPR 2.625, touch, mobile), Galaxy-class (360x800, DPR 3), desktop Chrome 1280 and 2560,
  200% browser zoom (640 css px) and 400% reflow (320 css px), 200% root text size at 390 and 1024,
  missing room webp, missing all room art, and reduced motion against ambient telemetry.
A target passes when the document has no horizontal overflow, no page error and every route renders.

Run: python web/qa/p5_layout.py [--shots]   (WARP_QA_SCREENSHOTS=DIR for WebP evidence)
"""
import sys

from account_reads import main
from p5_common import (
    Recorder,
    SnapshotGuard,
    capture,
    healthy_routes,
    healthy_status,
    overflow,
    put,
    sign_in,
    stamp,
)
from playwright.sync_api import expect

from warp3c.live import BUS

VIEWS = (
    ("overview", None), ("desk", None), ("signals", None),
    ("assets", 0), ("assets", 1), ("assets", 2),
    ("screener", None),
    ("preferences", 0), ("preferences", 1), ("preferences", 2), ("preferences", 3), ("preferences", 4),
)
# Text-only 200% emulation findings. Recorded, not fixed: this lane may not edit web/src.
#   F-P5-1  ID, 390 px: the status strip stream cell ("Koneksi / Langsung", Shell.tsx StatusStrip) is 6 px too wide.
#   F-P5-2  1024 px, Settings & risk > Activity: the source-state badge and its notes overflow (EN 1 px, ID 80 px).
# The run fails if a view outside this set overflows, or if a listed view stops overflowing (remove it then).
KNOWN_TEXT_ZOOM_OVERFLOW = {
    *(("text-200pct-390", "id", 390, route, tab) for route, tab in (
        ("overview", None), ("desk", None), ("signals", None), ("assets", 0), ("assets", 1), ("assets", 2),
        ("screener", None), ("preferences", 0), ("preferences", 1), ("preferences", 2), ("preferences", 3),
        ("preferences", 4))),
    ("text-200pct-1024", "en", 1024, "preferences", 3),
    ("text-200pct-1024", "id", 1024, "preferences", 3),
}
ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/157.0.0.0 Mobile Safari/537.36")

PROFILES = {
    "pixel-412x915-dpr2.625-touch": dict(viewport={"width": 412, "height": 915}, device_scale_factor=2.625,
                                          is_mobile=True, has_touch=True, user_agent=ANDROID),
    "galaxy-360x800-dpr3-touch": dict(viewport={"width": 360, "height": 800}, device_scale_factor=3,
                                       is_mobile=True, has_touch=True, user_agent=ANDROID),
    "desktop-1280x720": dict(viewport={"width": 1280, "height": 720}),
    "desktop-2560x1440": dict(viewport={"width": 2560, "height": 1440}),
    "zoom-200pct-640": dict(viewport={"width": 640, "height": 360}, device_scale_factor=2),
    "zoom-400pct-320": dict(viewport={"width": 320, "height": 200}, device_scale_factor=4),
}


def go(page, route, tab, touch):
    def press(locator):
        locator.tap() if touch else locator.click()

    if route == "desk" and page.viewport_size["width"] <= 900:
        press(page.locator(".overview-room-link"))
        expect(page.locator(".desk-workspace")).to_be_visible()
    else:
        link = page.locator(f'nav:visible a[href="#{route}"]')
        press(link)
        expect(link).to_have_attribute("aria-current", "page")
    if tab is not None:
        buttons = page.locator(".sections button")
        press(buttons.nth(tab))
        expect(buttons.nth(tab)).to_have_attribute("aria-pressed", "true")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(80)


def audit(browser, base, password, label, options, *, views=VIEWS, widths=None, setup=None, shots=()):
    context = browser.new_context(reduced_motion="reduce", timezone_id="Asia/Jakarta", **options)
    recorder = Recorder(context, base)
    if setup:
        setup(context)
    page = sign_in(context, base, password)
    touch = bool(options.get("has_touch"))
    rows = []
    for lang in ("en", "id"):
        if lang == "id":
            (page.locator(".language-button").tap() if touch else page.locator(".language-button").click())
            expect(page.locator("html")).to_have_attribute("lang", "id")
        for width in widths or [None]:
            if width:
                page.set_viewport_size({"width": width, "height": 844 if width < 600 else 960})
            for route, tab in views:
                go(page, route, tab, touch)
                excess = overflow(page)
                rows.append((label, lang, width or page.viewport_size["width"], route, tab, excess))
                if (lang, route, tab) in shots:
                    capture(page, f"p5-{label}-{route}-{lang}")
    # A missing resource is expected when art is blocked on purpose, so only script errors fail a target.
    page_errors = list(recorder.errors)
    recorder.errors.clear()
    recorder.assert_clean()
    context.close()
    return rows, page_errors


SCALE_TEXT = """factor => {
  const all = [...document.body.querySelectorAll('*')];
  const sizes = all.map(el => el.dataset.p5fs ? parseFloat(el.dataset.p5fs) : parseFloat(getComputedStyle(el).fontSize));
  all.forEach((el, i) => { el.dataset.p5fs = sizes[i]; el.style.setProperty('font-size', (sizes[i] * factor) + 'px', 'important'); });
  return all.length;
}"""


def run_text_zoom(browser, base, password, width, factor=2):
    """Text-only zoom. Every element's computed px size is multiplied, the layout viewport stays the same.

    Setting the root font size does nothing here: the product CSS sizes text in px, so it would test nothing.
    """
    context = browser.new_context(viewport={"width": width, "height": 844 if width < 600 else 960},
                                  reduced_motion="reduce", timezone_id="Asia/Jakarta")
    recorder = Recorder(context, base)
    page = sign_in(context, base, password)
    label = f"text-{factor * 100}pct-{width}"
    rows = []
    for lang in ("en", "id"):
        if lang == "id":
            page.locator(".language-button").click()
        for route, tab in VIEWS:
            go(page, route, tab, False)
            page.evaluate(SCALE_TEXT, factor)
            page.wait_for_timeout(60)
            rows.append((label, lang, width, route, tab, overflow(page)))
            if (lang, route, tab, width) == ("en", "overview", None, 390):
                capture(page, "p5-text-200pct-390-overview-en")
    errors = list(recorder.errors)
    recorder.errors.clear()
    recorder.assert_clean()
    context.close()
    return rows, errors


def running_animations(page):
    return page.evaluate("document.getAnimations().filter(a => a.playState === 'running').map(a => a.animationName)")


def reduced_motion_case(browser, base, password):
    """With ambient telemetry present, reduce stops every running animation. No-preference runs the idle loop."""
    paper = {**healthy_status(), "mode": "paper", "execution_permitted": True, "gating": "current"}
    results = {}
    for preference in ("reduce", "no-preference"):
        context = browser.new_context(viewport={"width": 1440, "height": 960}, reduced_motion=preference)
        recorder = Recorder(context, base)
        page = sign_in(context, base, password)
        page.goto(base + "/dashboard#desk")
        room = page.locator(".trading-room")
        expect(room).to_be_visible()
        BUS.publish("snapshot", {"routes": {"/api/status": {**paper, "published_at": stamp()},
                                            "/api/events": {"events": [{"event": "strategy_confirmed", "ts": stamp()}],
                                                            "as_of": stamp()}},
                                 "published_at": stamp()})
        page.wait_for_timeout(600)
        results[preference] = (running_animations(page), room.get_attribute("data-motion"),
                               page.locator("[data-ambient=on]").count())
        recorder.assert_clean()
        context.close()
    reduce, normal = results["reduce"], results["no-preference"]
    assert reduce[0] == [], reduce
    assert normal[2] > 0, f"ambient telemetry never reached the room: {normal}"
    assert normal[0], f"no running animation under no-preference, the control is not sensitive: {normal}"
    return results


def verify(browser, base, password):
    summary, errors, emulation = [], [], []

    def add(rows, page_errors):
        (emulation if rows[0][0].startswith("text-") else summary).extend(rows)
        errors.extend(page_errors)
        started = rows[0][0]
        bad = [row for row in rows if row[-1] > 0]
        print(f"{started}: {len(rows)} layouts, {len(bad)} overflow, {len(page_errors)} page errors")

    with SnapshotGuard():
        put(healthy_routes())
        for label, options in PROFILES.items():
            shots = {("en", "overview", None), ("id", "preferences", 0)} if label.startswith(("pixel", "zoom-400")) else ()
            add(*audit(browser, base, password, label, options, shots=shots))

        for width in (390, 1024):
            add(*run_text_zoom(browser, base, password, width))

        # Missing artwork: the desk must stay usable and nothing may overflow.
        room_views = (("overview", None), ("desk", None))

        def block_webp(context):
            context.route("**/*.webp", lambda route: route.abort())

        def block_art(context):
            context.route("**/*.webp", lambda route: route.abort())
            context.route("**/assets/room/*.svg", lambda route: route.abort())

        for label, setup in (("no-webp", block_webp), ("no-room-art", block_art)):
            for width in (320, 390, 768, 1440):
                options = dict(viewport={"width": width, "height": 844 if width < 600 else 960})
                shots = {("en", "desk", None)} if width in (390, 1440) else ()
                rows, page_errors = audit(browser, base, password, f"{label}-{width}", options, views=room_views,
                                          setup=setup, shots=shots)
                add(rows, page_errors)

        motion = reduced_motion_case(browser, base, password)
        print("reduced motion:", {key: (len(value[0]), value[1], value[2]) for key, value in motion.items()})

    bad = [row for row in summary if row[-1] > 0]
    print(f"P5 layout extension: {len(summary)} layouts, {len(bad)} with horizontal overflow, {len(errors)} page errors")
    overflowing = {row[:-1] for row in emulation if row[-1] > 0}
    print(f"Text-only 200% emulation (outside the gate): {len(emulation)} layouts, {len(emulation) - len(overflowing)} clean, "
          f"{len(overflowing)} overflow ({len(overflowing & KNOWN_TEXT_ZOOM_OVERFLOW)} known findings F-P5-1, F-P5-2)")
    for row in bad:
        print("overflow", *row)
    for item in errors:
        print("page error", item)
    unknown = overflowing - KNOWN_TEXT_ZOOM_OVERFLOW
    resolved = KNOWN_TEXT_ZOOM_OVERFLOW - overflowing
    for row in sorted(unknown, key=str):
        print("NEW text-zoom overflow", *row)
    for row in sorted(resolved, key=str):
        print("known finding no longer reproduces", *row)
    if bad or errors or unknown or resolved:
        raise SystemExit(1)


if __name__ == "__main__":
    sys.argv = [sys.argv[0]]
    main(verify)
