# TradingDesk 2.0 P5: release-gate packet

P5 is an evidence lane. It adds tests, QA suites, read-only scripts and documents. It changes no file under `src/`, `web/src/`, `fly.toml`, `Dockerfile` or `.github/`, and it ran nothing against production or staging. The decision document is [RELEASE_CANDIDATE_REPORT.md](RELEASE_CANDIDATE_REPORT.md). The per-row matrix is in [TRADINGDESK_2_QA.md](TRADINGDESK_2_QA.md). The rollback procedure is in [ROLLBACK.md](ROLLBACK.md).

**Base** `91d3c38f7581d6bf3954d01c0f9f895b5d0e8c75` (P4C, PR #26). Product source is byte-identical to the base: a clean build from the base produces the same files as a build from this branch, and screenshots of the two builds are pixel-identical (see [Before and after](#before-and-after)).

## What was added

| Area | File | Purpose |
| --- | --- | --- |
| Contract tests | `tests/test_p5_release_contracts.py` | 47 tests. Gaps in the contract matrix, the read-only boundary guards, two known gaps recorded as expected failures, and guards on these documents (no volume-removal command in the rollback, links resolve, screenshots are webp). |
| Token contrast | `web/p5-contrast.test.ts` | 6 tests. WCAG ratios read from `web/src/index.css`, so a palette edit is measured. |
| Shared QA helpers | `web/qa/p5_common.py` | Loopback-only request recorder, sign-in, snapshot guard, broker fixtures. The recorder fails a run on any off-host request. |
| SSE matrix | `web/qa/p5_sse.py` | 7 cases against the real worker's stream and the real `BUS`. |
| Accessibility | `web/qa/p5_a11y.py` | Names, 44 px targets, focus, keyboard reach, contrast, with a negative control. |
| Layout extension | `web/qa/p5_layout.py` | 176 layouts on top of the existing 168. |
| Flows | `web/qa/p5_flows.py` | One signed-in journey plus a keyboard-only journey, with a state-change ledger. |
| Performance | `web/qa/p5_perf.py` | Emulated low-end Android, 4x CPU, Slow 3G, SSE burst, listener counts. |
| Scripts | `scripts/qa/{secret_scan,bundle_report,diff_review}.py`, `run_suites.sh` | Read-only local checks and a one-command runner. |

Every fixture lives in `tests/**` or `web/qa/**`. No fixture is reachable from `src/` or from the built bundle (the secret scan and bundle report both run on the built output).

## How the suites avoid being vacuous

- **Positive controls.** The assertion "nothing reads as permitted" is paired with a case where a PAPER worker publishes a permit and the strip does read "Execution permitted". The listener-leak gate is paired with a run that injects five extra `hashchange` listeners and checks they are counted. The accessibility audit has a probe page with a known low-contrast, unnamed, undersized control and fails if the audit misses it.
- **Known findings are pinned in both directions.** A finding that is allow-listed fails the run if it stops reproducing, so the list cannot go stale. A new violation outside the list fails the run.
- **Known gaps are expected failures that assert the safe behaviour.** When the product gains the control, unittest reports an unexpected success and the marker is removed.
- **Mutation checks.** Five product mutations were applied in a scratch worktree (never committed) and each was caught by the named suite:

| Mutation of `web/src` (scratch copy only) | Caught by |
| --- | --- |
| Stream label always "Live" | `p5_sse.py`: expected "Reconnecting" |
| Remove the OBSERVE clause from the execution gate in `StatusStrip` | `p5_sse.py`: expected "Blocked" |
| `resumeGate` always returns "fresh" | `p5_flows.py`: expected "Awaiting fresh snapshot" |
| Remove the sign-out button's accessible name | `p5_a11y.py`: `name:button` finding |
| Shrink the sign-out button to 28 px | `p5_a11y.py`: `target:button.icon-button` findings (EN and ID) |

## SSE and transport matrix

All cases drive the real worker (`HealthHandler`, `BUS`, session auth) in-process on loopback. Status: PASS means the suite asserts it and passed.

| Case | Asserted | Result |
| --- | --- | --- |
| Initial handshake | Badge is not "Live" until a valid hello arrives. A hello without a source time is not a handshake. HTTP reads work while waiting. | PASS |
| Disconnect | Badge reads "Reconnecting", incident strip appears after 5 s, no panel reads green. | PASS |
| Reconnect and backoff | Attempts back off (about 1 s, 2 s, 4 s, gap ratio above 1.4). Fallback HTTP polls run while the stream is down and stop after reconnect. The incident clears. | PASS |
| Auth loss (401 mid-session) | The worker ends the stream, the app signs out, stops reconnecting, and no cached worker data, mode badge or green state survives. Signing in again restores a Live stream. | PASS |
| Old frames | An envelope older than the one applied is dropped even when its body says HALT. A frame whose producer time is older than held evidence is dropped. | PASS |
| Repeat events | Identical frames are idempotent: no flapping, no errors, no extra reads. A repeated HALT keeps earlier snapshot fields. | PASS |
| Beyond the TTL | A page that only ever receives a producer time past the 15-minute ceiling is stale on every surface. | PASS |
| Route errors | Empty frame reads "Awaiting worker", a bad row reads "Invalid snapshot", a book error is never "Confirmed", a status frame of the wrong shape reads "Not reported" and never "Execution permitted". | PASS |
| HTTP route errors | With the stream down, a failing status read is an incident and never an allow. | PASS |
| Stale producer, live transport | Heartbeats do not refresh data. The desk goes stale while the stream is Live. | PASS |

One property that the matrix exposed and that is by design: with an OBSERVE worker the strip shows "Blocked" even if the producer claims a permit. A permit is rendered only for PAPER or LIVE with a current snapshot, HALT clear and `execution_permitted` true.

## Responsive and accessibility

Targets, each in EN and ID across 12 route and tab views (Overview, Trading Desk, Signals, three Bots tabs, Screener, and the five Settings tabs).

| Target | Layouts | Overflow | Page errors |
| --- | --- | --- | --- |
| Existing audit: 320, 360, 390, 768, 1024, 1440, 1920 px | 168 | 0 | 0 |
| Pixel-class Android 412x915, DPR 2.625, touch, mobile UA | 24 | 0 | 0 |
| Galaxy-class 360x800, DPR 3, touch, mobile UA | 24 | 0 | 0 |
| Desktop Chrome 1280x720 | 24 | 0 | 0 |
| Desktop Chrome 2560x1440 | 24 | 0 | 0 |
| 200% browser zoom (640 css px) | 24 | 0 | 0 |
| 400% reflow (320 css px) | 24 | 0 | 0 |
| Room webp blocked at 320, 390, 768, 1440 px | 16 | 0 | 0 |
| All room art blocked at 320, 390, 768, 1440 px | 16 | 0 | 0 |
| Reduced motion against ambient telemetry | gate | 0 running animations with `reduce`, 6 with `no-preference` | 0 |
| **Gate total (existing + new)** | **344** | **0** | **0** |
| 200% text-only emulation, 390 and 1024 px (reported, outside the gate) | 48 | 14 known | 0 |

The gate is 168 + 176 = 344 layouts. The 14 text-only overflows are the two known findings below. Text-only emulation multiplies every element's pixel font size, because the stylesheet sizes text in pixels and a root `font-size` change has no effect.

Accessibility audit (192 view loads, Chrome's computed accessibility tree): 4256 accessibility nodes, 19,931 text nodes checked for WCAG AA contrast, 1,931 keyboard focus stops checked for a visible indicator, 96 touch-target views. Findings: 0. 107 text nodes sit on a background the probe cannot resolve (image fills) and are counted as unknown, not as pass.

Keyboard-only: the flows suite runs a second journey with no pointer input. It signs in by keyboard, uses the skip link, reaches every route from the rail by Enter and checks that the page heading takes focus, switches Settings tabs by Space, reaches the HALT typed confirmation and cancels it (no `/api/pause` request is sent), opens a room station drawer by Enter, closes it with Escape and checks that focus returns to its trigger, then signs out.

Slow 3G is measured in the performance section, not in the layout audit.

### New findings (not fixed in this lane)

| ID | Severity | Where | Reproduce | Notes |
| --- | --- | --- | --- | --- |
| F-P5-1 | Low | `web/src/tradingdesk/Shell.tsx` `StatusStrip` and the bottom nav at 200% text | `python3 web/qa/p5_layout.py` (text-only emulation, ID, 390 px) | The stream cell is 6 px too wide in ID, which causes document overflow on all 12 views. In the screenshot the status labels overlap and the bottom-nav labels break mid-word ("Overv/iew"). Real Android font scaling is unverified, because the emulation is an approximation. |
| F-P5-2 | Low | Settings and risk > Activity at 1024 px with 200% text | same | Source-state badge and its notes overflow (EN 1 px, ID 80 px). |

Evidence: [`p5-text-200pct-390-overview-en.webp`](screenshots/p5/p5-text-200pct-390-overview-en.webp).

## Performance (emulated low-end Android)

Pixel-class 412x915 viewport, CDP `Emulation.setCPUThrottlingRate` 4x, headless Chrome 157 on a 4-core VM, software raster (`--disable-gpu`). Paint cost is overstated against a phone GPU and timing varies by machine, so treat these as order-of-magnitude. Structural gates (no growth, one stream, compositor-only animation) are hard. Timings carry only sanity limits. The suite ran twice, and both runs are quoted where they differ.

| Measurement | Result |
| --- | --- |
| Overview idle, 5 s main-thread busy | 1.4 % (script 0.7 %) |
| Room idle (OBSERVE), 5 s | 1.5 %, 0 running animations |
| Room under ambient telemetry, 5 s | 1.8 to 2.0 % (two runs), 2 stations, animations `room-optics` (opacity) and `station-idle` (transform) only |
| Frame rate, normal cadence, 5 s | 60 fps, max gap 16.8 ms, 0 gaps over 50 ms |
| SSE burst: 900 frames sent through `BUS.publish` in 0.61 s | Browser received 384 (the per-subscriber queue holds 32 and drops the oldest), 60 fps, max gap 16.8 ms, 0 long tasks, about 100 DOM mutations |
| Same burst with the room open | 60 fps, 0 long tasks, about 111 DOM mutations |
| Long tasks during load, route switching and bursts | 1 at load (51 ms), 0 elsewhere |
| Listener, timer, stream and DOM counts: baseline, after 10, after 50 route switches | 169 JS listeners, 3 global listeners, 6 intervals, 1 open EventSource, 1,060 DOM nodes. Identical at all three points. The worker sees 1 stream subscriber after the switches. |
| Listener-leak negative control | Five injected `hashchange` listeners were counted, so the gate can see a leak |
| Time to first position list, 4x CPU (median of 3, two runs) | 292 ms and 350 ms (first run: FCP 128 ms, LCP 264 ms, worst long task 51 ms) |
| Time to first position list, 4x CPU + Slow 3G (400 ms latency, 500 kbit/s down; median of 2, two runs) | 8,851 ms and 8,848 ms (first run: FCP 8,216 ms, LCP 8,814 ms) |
| Time to first position list, unthrottled (median of 3, two runs) | 93 ms and 102 ms |
| First-load transfer as served | 527,768 bytes across 6 files, not compressed (the static handler sends no `Content-Encoding`) |

### Bundle weight

Measured by `python3 scripts/qa/bundle_report.py --expect-index-kb 352.93`. "Vite kB" is the character count that `vite build` prints. "kB" is transferred bytes, which is larger because of non-ASCII text. gzip here is zlib level 9, so it does not match Vite's own gzip figure (111.21 kB for the index chunk).

| File | Role | Vite kB | kB (bytes) | gzip kB |
| --- | --- | --- | --- | --- |
| `index-*.js` | first-load | 352.93 | 353.06 | 110.67 |
| `index-*.css` | first-load | 63.30 | 63.30 | 13.88 |
| `Activity-*.js` | lazy | 7.92 | 7.93 | 2.98 |
| Inter, Space Grotesk, JetBrains Mono `.woff2` | font (first view) | | 48.26 / 22.29 / 40.40 | not compressible |
| `control-room-*.webp` | image on demand | | 259.75 | not compressible |
| `room/*.svg` (6) | image on demand | | 0.77 to 0.83 each | |

First-load JS is 352.93 kB by Vite's count, equal to the base. First load as the worker serves it is about 528 kB because nothing is compressed, so Slow 3G is dominated by transfer. That is a deployment finding (F-P5-3 in the report), not a front-end regression, and no optimisation was made.

## Flows

`web/qa/p5_flows.py` signs in on a real local worker and walks the following. The state-change ledger records every non-GET request and fails the run on anything outside `{login, logout, bots/preview, bots/template, pause, resume}`. It also fails on any request to `warp-bots/action`, `paper/roundtrip`, `accounts/select` or `accounts/budget`, on any response claiming an order was placed, and on any request that leaves loopback.

| Flow | Result |
| --- | --- |
| Wrong password gives no session and no data, then a successful login. Logout ends the SPA state, the cookie and the stream | PASS |
| Navigation: 5 bottom links on a phone and 6 rail links on desktop, browser back and forward, skip link. The heading takes focus on route change | PASS |
| Signals: proposals are labelled `PROPOSED / NOT SUBMITTED` and no buy, sell, execute, place, submit, order or trade control exists | PASS |
| Account switching filters positions, `by_account` agrees with rows, Unassigned stays reachable | PASS |
| View-only external bots (VORTEQ, grid rows): no control rendered | PASS |
| Preset save (`/api/bots/template`) and risk preview (`/api/bots/preview`) | PASS: `PROPOSED / NOT SUBMITTED`, no order |
| HALT with typed confirmation, RESUME with typed confirmation | PASS |
| RESUME acknowledged: "Awaiting fresh snapshot" until the worker publishes a newer snapshot | PASS |
| OBSERVE chip: "Observe mode · entries blocked by design", neutral, no incident | PASS |
| Keyboard-only second journey (see Responsive and accessibility) | PASS |

## Security and diff review

Details and exact output are in the report. In short: `scripts/qa/secret_scan.py` found no value-shaped secret in `web/dist`, in the committed screenshots, or in docs and QA sources. The only hits are the names of environment variables inside dashboard help text, and unit-test fixture constants that predate this lane; each is listed with its reason. `scripts/qa/diff_review.py` shows that across PRs #19 to #26 the only reviewed file that changed is `src/warp3c/engine.py` (3 insertions, 1 deletion), and that the broker write call sites (11), the worker POST routes (12) and the browser write sites (11) are identical at both ends.

## Before and after

Product source is unchanged, so the honest comparison is main against this branch. Both were built, served by the in-process worker, and captured with the same fixtures, reduced motion and a masked clock. The images are pixel-identical (bounding box of the difference: none).

| Viewport | Main `91d3c38` | This branch |
| --- | --- | --- |
| 390 x 844 | [before](screenshots/p5/before-main-vs-p5-390.webp) | [after](screenshots/p5/after-main-vs-p5-390.webp) |
| 1440 x 960 | [before](screenshots/p5/before-main-vs-p5-1440.webp) | [after](screenshots/p5/after-main-vs-p5-1440.webp) |

## Evidence index

| Evidence | File |
| --- | --- |
| Stream disconnected, Reconnecting, incident strip | [`p5-sse-disconnected-390-en.webp`](screenshots/p5/p5-sse-disconnected-390-en.webp) |
| 401 mid-session, signed out | [`p5-sse-auth-loss-390-en.webp`](screenshots/p5/p5-sse-auth-loss-390-en.webp) |
| Live transport, stale producer | [`p5-sse-stale-producer-390-en.webp`](screenshots/p5/p5-sse-stale-producer-390-en.webp) |
| Signals flow | [`p5-flow-signals-390-en.webp`](screenshots/p5/p5-flow-signals-390-en.webp) |
| Accounts flow, Unassigned and external bots | [`p5-flow-accounts-390-en.webp`](screenshots/p5/p5-flow-accounts-390-en.webp) |
| RESUME awaiting fresh snapshot | [`p5-flow-resume-awaiting-390-en.webp`](screenshots/p5/p5-flow-resume-awaiting-390-en.webp) |
| OBSERVE chip | [`p5-flow-observe-chip-390-en.webp`](screenshots/p5/p5-flow-observe-chip-390-en.webp) |
| Populated desk, ID, 1440 px | [`p5-a11y-populated-1440-id.webp`](screenshots/p5/p5-a11y-populated-1440-id.webp) |
| Pixel-class Android, Overview EN and Settings ID | [`overview`](screenshots/p5/p5-pixel-412x915-dpr2.625-touch-overview-en.webp), [`settings`](screenshots/p5/p5-pixel-412x915-dpr2.625-touch-preferences-id.webp) |
| 400% reflow at 320 px | [`overview`](screenshots/p5/p5-zoom-400pct-320-overview-en.webp), [`settings ID`](screenshots/p5/p5-zoom-400pct-320-preferences-id.webp) |
| Room webp blocked | [`390`](screenshots/p5/p5-no-webp-390-desk-en.webp), [`1440`](screenshots/p5/p5-no-webp-1440-desk-en.webp) |
| All room art blocked | [`390`](screenshots/p5/p5-no-room-art-390-desk-en.webp), [`1440`](screenshots/p5/p5-no-room-art-1440-desk-en.webp) |
| 200% text-only emulation (F-P5-1) | [`p5-text-200pct-390-overview-en.webp`](screenshots/p5/p5-text-200pct-390-overview-en.webp) |

All screenshots come from the in-process worker with fixtures defined in `web/qa/p5_common.py`. No login form, token, cookie or password appears in any of them, and the secret scan covers the folder.

## How to reproduce

```bash
pip install --user httpx fastapi uvicorn playwright pillow ruff
(cd web && npm ci)
CHROMIUM_PATH=/usr/bin/google-chrome scripts/qa/run_suites.sh
```

The runner prints one line per step and exits with the number of failed steps. Logs go to `/tmp/p5-qa-logs`. `--skip-browser` runs the Python, TypeScript and script checks only. Set `WARP_QA_SCREENSHOTS=DIR` for the browser suites to write the evidence webp files.
