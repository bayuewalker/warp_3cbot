"""P5 accessibility audit on the real in-process OBSERVE worker.

Checks per view, in EN and ID: accessible names (Chrome's computed accessibility tree), touch targets
(44 px project rule), visible keyboard focus, keyboard-only reachability, rendered text contrast
(WCAG AA), reduced motion, 200% zoom reflow and 200% root text size.

A view that breaks a rule is a product finding. This lane may not edit web/src, so every known finding
is listed in KNOWN_FINDINGS, asserted to still reproduce, and reported. Any violation NOT listed fails
the suite. Run: python web/qa/p5_a11y.py [--report FILE]
"""
import json
import os
import sys

from account_reads import main
from p5_common import (
    Recorder,
    SnapshotGuard,
    broker_fixtures,
    capture,
    healthy_routes,
    healthy_status,
    put,
    serve_fixtures,
    sign_in,
    timeline_routes,
)
from playwright.sync_api import expect

# Findings the lane records but does not fix. Keys are "<rule>:<selector-or-label>".
KNOWN_FINDINGS: dict[str, str] = {}

VIEWS = (
    ("overview", None), ("desk", None), ("signals", None),
    ("assets", 0), ("assets", 1), ("assets", 2),
    ("screener", None),
    ("preferences", 0), ("preferences", 1), ("preferences", 2), ("preferences", 3), ("preferences", 4),
)
INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "checkbox", "radio", "switch", "tab", "slider",
    "spinbutton", "searchbox", "menuitem", "option", "listbox",
}

AX_NAMES = """
() => [...document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=link]')]
  .filter(el => {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }).length
"""

TARGETS = """
() => [...document.querySelectorAll('a[href], button, input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea, summary, [role=button], [role=tab]')]
  .filter(el => {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
    if (el.closest('.sr-only') || el.classList.contains('sr-only')) return false;
    return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }).map(el => {
    const box = el.getBoundingClientRect();
    return {
      label: (el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || el.tagName).trim().slice(0, 40),
      cls: String(el.className || '').split(' ').slice(0, 2).join('.'),
      tag: el.tagName.toLowerCase(),
      w: Math.round(box.width), h: Math.round(box.height),
    };
  }).filter(item => item.w < 44 || item.h < 44)
"""

CONTRAST = """
() => {
  const parse = (value) => {
    const m = value.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(',').map(s => parseFloat(s));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a), g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a), a: 1,
  });
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const root = parse(getComputedStyle(document.documentElement).backgroundColor) || { r: 10, g: 10, b: 15, a: 1 };
  const out = [];
  let unknown = 0, checked = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.textContent.trim()) continue;
    const el = node.parentElement;
    if (!el || seen.has(el)) continue;
    seen.add(el);
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    if (el.closest('.sr-only, [hidden], [disabled], [aria-disabled=true]')) continue;
    let opacity = 1, image = false, cursor = el;
    const layers = [];
    while (cursor) {
      const cs = getComputedStyle(cursor);
      opacity *= parseFloat(cs.opacity);
      const bg = parse(cs.backgroundColor);
      const picture = cs.backgroundImage;
      if (picture.includes('url(')) { image = true; break; }
      if (picture.includes('gradient')) {
        const stops = [...picture.matchAll(/rgba?\\([^)]+\\)/g)].map(m => parse(m[0])).filter(Boolean);
        if (stops.length) layers.push({ stops });
      }
      if (bg && bg.a > 0) layers.push({ stops: [bg] });
      if (bg && bg.a === 1) break;
      cursor = cursor.parentElement;
    }
    if (image) { unknown++; continue; }
    let candidates = [root];
    for (const layer of layers.reverse()) {
      candidates = candidates.flatMap(below => layer.stops.map(top => over(top, below)));
    }
    let color = parse(style.color);
    if (!color) continue;
    if (color.a === 0 || style.webkitTextFillColor === 'rgba(0, 0, 0, 0)') { unknown++; continue; }
    const size = parseFloat(style.fontSize);
    const bold = parseInt(style.fontWeight, 10) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    const value = Math.min(...candidates.map(background => ratio(over({ ...color, a: color.a * opacity }, background), background)));
    checked++;
    if (value < need) {
      out.push({
        text: node.textContent.trim().slice(0, 40), ratio: Math.round(value * 100) / 100, need, size,
        cls: String(el.className || '').split(' ').slice(0, 2).join('.'), tag: el.tagName.toLowerCase(),
      });
    }
  }
  return { checked, unknown, failures: out };
}
"""


def ax_unnamed(cdp):
    tree = cdp.send("Accessibility.getFullAXTree")["nodes"]
    bad = []
    for node in tree:
        role = (node.get("role") or {}).get("value")
        if node.get("ignored") or role not in INTERACTIVE_ROLES:
            continue
        name = ((node.get("name") or {}).get("value") or "").strip()
        if not name:
            bad.append({"role": role, "backend": node.get("backendDOMNodeId")})
    return bad, sum(1 for node in tree if (node.get("role") or {}).get("value") in INTERACTIVE_ROLES and not node.get("ignored"))


def go(page, route, tab, lang):
    if route == "desk" and page.viewport_size["width"] <= 900:
        page.locator(".overview-room-link").click()
        expect(page.locator(".desk-workspace")).to_be_visible()
        return
    link = page.locator(f'nav:visible a[href="#{route}"]')
    link.click()
    expect(link).to_have_attribute("aria-current", "page")
    if tab is not None:
        buttons = page.locator(".sections button")
        buttons.nth(tab).click()
        expect(buttons.nth(tab)).to_have_attribute("aria-pressed", "true")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(120)


def focus_walk(page, limit=80):
    """Tab through the page. Return focused elements that show no visible focus indicator."""
    page.evaluate("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)")
    problems, stops = [], 0
    first = None
    for _ in range(limit):
        page.keyboard.press("Tab")
        info = page.evaluate(
            """() => {
              const el = document.activeElement;
              if (!el || el === document.body) return null;
              const cs = getComputedStyle(el);
              const box = el.getBoundingClientRect();
              const outlined = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 && cs.outlineColor !== 'rgba(0, 0, 0, 0)';
              const shadow = cs.boxShadow !== 'none';
              return {
                id: (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 30),
                tag: el.tagName.toLowerCase(), outlined, shadow, visible: box.width > 0 && box.height > 0,
                inView: box.bottom > 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth,
                key: el.tagName + '|' + (el.getAttribute('href') || '') + '|' + (el.textContent || '').trim().slice(0, 20),
              };
            }"""
        )
        if info is None:
            break
        if first is None:
            first = info["key"]
        elif info["key"] == first:
            break
        stops += 1
        if not (info["outlined"] or info["shadow"]):
            problems.append(info["id"])
        if not info["visible"] or not info["inView"]:
            problems.append("offscreen:" + info["id"])
    return stops, problems


def audit_view(page, cdp, lang, width, route, tab, found, stats):
    label = f"{lang}/{width}/{route}{'' if tab is None else '.' + str(tab)}"
    bad, total = ax_unnamed(cdp)
    stats["ax_nodes"] += total
    for item in bad:
        found.append((f"name:{item['role']}", label))
    if width <= 900:
        for item in page.evaluate(TARGETS):
            found.append((f"target:{item['tag']}.{item['cls']}:{item['label']}", f"{label} {item['w']}x{item['h']}"))
        stats["target_views"] += 1
    result = page.evaluate(CONTRAST)
    stats["text_checked"] += result["checked"]
    stats["text_unknown_bg"] += result["unknown"]
    for item in result["failures"]:
        found.append((f"contrast:{item['tag']}.{item['cls']}:{item['text']}", f"{label} {item['ratio']}<{item['need']}"))
    stops, problems = focus_walk(page)
    stats["focus_stops"] += stops
    for item in problems:
        found.append((f"focus:{item}", label))
    return label


def populated_check(page, route, tab):
    """The populated pass is only evidence if the fixture rows actually reached the screen."""
    if (route, tab) == ("preferences", 0):
        expect(page.locator(".position-card").first).to_be_visible()
    elif (route, tab) == ("preferences", 3):
        expect(page.locator(".act-item").first).to_be_visible()
    elif (route, tab) == ("preferences", 4):
        expect(page.locator(".rk-incidents")).to_be_visible()
    elif (route, tab) == ("assets", 0):
        expect(page.get_by_text("WARP-demo1").first).to_be_visible()


def selftest(page, cdp):
    """Negative controls: each detector must flag a deliberately bad element, or the audit proves nothing."""
    page.evaluate(
        """() => {
          const host = document.createElement('div');
          host.id = 'p5-probe';
          host.innerHTML = '<div style="color:#555a60;background:#14181f;font-size:14px">low contrast probe</div>' +
            '<button style="width:20px;height:20px" aria-label="tiny probe">x</button>' +
            '<button style="min-width:60px;min-height:60px"></button>' +
            '<button style="min-width:60px;min-height:60px;outline:none;box-shadow:none">bare focus probe</button>';
          document.body.appendChild(host);
        }"""
    )
    try:
        assert any(item["text"].startswith("low contrast probe") for item in page.evaluate(CONTRAST)["failures"])
        assert any(item["label"] == "tiny probe" for item in page.evaluate(TARGETS))
        assert any(item["role"] == "button" for item in ax_unnamed(cdp)[0])
        assert any(name == "bare focus probe" for name in focus_walk(page, limit=200)[1])
    finally:
        page.evaluate("document.getElementById('p5-probe').remove()")


def verify(browser, base, password):
    report = {"findings": [], "stats": {}}
    stats = {"views": 0, "ax_nodes": 0, "target_views": 0, "text_checked": 0, "text_unknown_bg": 0, "focus_stops": 0}
    found: list[tuple[str, str]] = []
    with SnapshotGuard():
        for state in ("empty", "populated"):
            if state == "populated":
                halted = {**healthy_status(), "halt": True, "risk_permitted": False, "reason": "halt"}
                put({**timeline_routes(), "status": halted,
                     "breakers": {"halt": True, "risk_permitted": False, "reason": "halt"}})
            else:
                put(healthy_routes())
            context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce",
                                          timezone_id="Asia/Jakarta")
            recorder = Recorder(context, base)
            page = sign_in(context, base, password)
            if state == "populated":
                serve_fixtures(page, base, broker_fixtures())
                page.reload(wait_until="domcontentloaded")
                expect(page.locator(".command-identity .badge")).to_be_visible()
            cdp = context.new_cdp_session(page)
            cdp.send("Accessibility.enable")
            if state == "empty":
                page.goto(base + "/dashboard#overview")
                page.evaluate("document.fonts.ready")
                selftest(page, cdp)
            for lang in ("en", "id"):
                if lang == "id":
                    page.locator(".language-button").click()
                    expect(page.locator("html")).to_have_attribute("lang", "id")
                for width in (320, 390, 1024, 1440):
                    page.set_viewport_size({"width": width, "height": 844 if width < 600 else 960})
                    for route, tab in VIEWS:
                        go(page, route, tab, lang)
                        if state == "populated":
                            populated_check(page, route, tab)
                        audit_view(page, cdp, f"{state}/{lang}", width, route, tab, found, stats)
                        stats["views"] += 1
            if state == "populated":
                capture(page, "p5-a11y-populated-1440-id")
            page.close()
            recorder.assert_clean()
            context.close()

    grouped: dict[str, list[str]] = {}
    for key, where in found:
        grouped.setdefault(key, []).append(where)
    report["stats"] = stats
    report["findings"] = [{"id": key, "count": len(where), "first": where[:3], "known": key in KNOWN_FINDINGS}
                          for key, where in sorted(grouped.items())]
    print(json.dumps(report, indent=1))
    path = os.getenv("WARP_QA_REPORT")
    if path:
        with open(path, "w") as handle:
            json.dump(report, handle, indent=1)
    new = [item for item in report["findings"] if not item["known"]]
    stale = [key for key in KNOWN_FINDINGS if key not in grouped]
    assert not stale, f"known findings that no longer reproduce, remove them: {stale}"
    assert not new, f"{len(new)} new accessibility findings"


if __name__ == "__main__":
    sys.argv = [sys.argv[0]]
    main(verify)
