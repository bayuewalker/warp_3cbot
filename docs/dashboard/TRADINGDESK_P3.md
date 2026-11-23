# P3 · Six-agent Trading Room

The Overview now opens six read-only workstations. Each station has an original SVG avatar, a role reading and a time. Tap or press Enter for the evidence behind the reading. Escape or the close button returns focus to the station.

## What a station shows

| Station | State source | Records in its drawer |
| --- | --- | --- |
| SCAN | Worker screening projection | Screener symbols and recommendations |
| ANALYST | Worker analysis projection | Opportunity symbols and model actions |
| AEGIS | Worker risk projection and HALT | Decision symbols and verdicts |
| EXECUTOR | Worker mode and permission | Submission symbols and journal states |
| PORTFOLIO | Combined accounts and position-book reads | Position pairs and kinds |
| WATCHER | Worker event window, plus stream connection | Alert types and delivery states |

The tile clock uses the latest relevant timestamped event when available. Otherwise it shows the source time of the P1 observation. The drawer identifies the source route, worker publication or source timestamp, browser receipt, freshness and reason. SCAN and ANALYST display a count only when the corresponding worker stage supplies a nonnegative integer and its observation is fresh and confirmed. When SCAN is blocked, the tile hides the configured universe count and shows the blocked state and its timestamp.

The history section examines the worker's recent 20-event window. An explicit strategy confirmation can establish an EXECUTOR success. A research block or provider failure can establish an ANALYST failure. An observed candidate does neither. An unavailable success, failure or schedule reads “Not reported.” The records section displays up to five rows from the indicated read. Some lists have no collection timestamp. Each such read shows unknown age. A stale or incomplete combined book carries a visible qualification.

## Art and motion

Six original vector silhouettes represent a radar operator, research analyst, shield guard, execution arm, portfolio clerk and lookout. Motion requires a connected stream, a worker publication at most 15 minutes old and a fresh, confirmed role reading. The worker clock uses the P1 five-second tolerance for forward skew. Each station permits motion only for its applicable positive reading (ready, pass, permitted or reported). EXECUTOR also checks the displayed mode and gate.

The SCAN dial needs a qualifying SCAN lifecycle event in the latest 12 seconds. The current event mapping identifies a blocked market-data read as a failure. It supplies no successful scan lifecycle event, so even a ready projection alone leaves the dial stationary. EXECUTOR can sweep for a recent `strategy_confirmed` receipt in paper or live mode. Observe candidates and duplicate skips remain available in history. WATCHER can acknowledge a new feed receipt independently.

Room display settings persist on the device. Text-only mode skips sprite requests and is the initial choice when the browser advertises data saving. A missing image leaves a focusable, named station. Reduced motion disables animations. The room pauses its ambience outside the viewport, in a hidden document or while the evidence drawer is open.

## QA

The base was the PR #21 merge at `7d9d7321cc3807e0567311f620406d135207160f`. Screenshots and the animation show a local authenticated OBSERVE startup with an empty journal, without broker credentials or a running trading loop. Screenshots precede fault injection.

| Command | Result |
| --- | --- |
| `cd web && npm ci && npx tsc --noEmit && npx vite build && npm test` | Build succeeded. 26 tests passed. JS 91.93 kB and CSS 11.61 kB gzipped. |
| `PYTHONPATH=src python -m unittest discover -s tests` | 148 tests passed with shell proxy variables unset. |
| `uvx ruff check .` | All checks passed. |
| `python web/qa/account_reads.py` | Browser regressions passed. |
| `python web/qa/telemetry.py` | Browser regressions passed, including SSE reconnect and five-second REST fallback. |
| `python web/qa/shell.py` | Browser regressions passed, including all four KPI cards above mobile navigation at 320 and 390 px. |
| `python web/qa/room.py` | All six drawers at 320, 390 and 1440 px. Keyboard focus restoration, read-only requests, one stream, OBSERVE, HALT, unavailable accounts, isolated event receipt, expired and missing snapshots, reconnect, missing art, reduced motion, offscreen pause and persistent text-only preference. |
| `python web/qa/account_reads.py --layout /tmp/f22-layout` | 126 layouts, 0 with horizontal overflow, 0 page errors. Seven widths from 320 to 1920 px in EN and ID. |

Browser runs used `PYTHONPATH=src` and `CHROMIUM_PATH` pointing to a local headless Chromium executable. The room uses three columns on phones starting at 360 px. Below 360 px the P2 two-column layout preserves Indonesian word boundaries. Physical Android, Safari, screen-reader output and production broker activity remain unverified.

The motion follow-up measures running animations using the browser animation API. With motion enabled, a blocked SCAN has zero running animations and no count. A ready SCAN without a qualifying event keeps its dial static. The room has zero running animations with reduced motion and with an expired worker publication plus disconnected stream. In the latter case, QA restores the sprites and enables motion before measuring. A display-only paper confirmation exercises the EXECUTOR sweep. Returning the displayed mode to OBSERVE suppresses it even with that confirmation still present. The local worker runs in OBSERVE throughout.

## Visual review

These captures show the initial P3 review at `8c67851`. They predate the motion and blocked-count corrections. The follow-up verification above covers the revised behavior.

[Animated workstation capture](screenshots/p3/room-motion.webp)

| View | Merged P2 | P3 room | P3 evidence |
| --- | --- | --- | --- |
| 320 × 844 | [Before](screenshots/p3/before-320.webp) | [After](screenshots/p3/after-320.webp) | |
| 390 × 844 | [Before](screenshots/p3/before-390.webp) | [After](screenshots/p3/after-390.webp) | [Bottom sheet](screenshots/p3/detail-390.webp) |
| 1440 × 960 | [Before](screenshots/p3/before-1440.webp) | [After](screenshots/p3/after-1440.webp) | [Drawer](screenshots/p3/detail-1440.webp) |
