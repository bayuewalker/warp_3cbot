# Trading Desk room on Overview

The Trading Desk control room is rendered on Overview again, directly under the page heading and the incident strip. It is the same `TradingRoom` component that `DeskPage` uses, fed by the same `useDeskTelemetry` read model that Overview already called. There is no second copy of the room logic and no new request.

## What changed

| File | Change |
| --- | --- |
| `web/src/monitor.tsx` | `Overview` renders `<TradingRoom>` (inside the existing `.control-desk` wrapper so the existing room CSS applies unchanged) above the KPI cards. The "Open Trading Desk" link keeps its `overview-room-link` class and sits under the room. The room also renders while Overview is awaiting the first snapshot, loading or in error, as it did before the dedicated page existed, and as `DeskPage` does. |
| `web/qa/shell.py` | See "QA assertions changed". |
| `web/qa/control_room.py` | See "QA assertions changed". |

Nothing else under `web/src`, `src/warp3c`, `tests`, `fly.toml`, `Dockerfile` or `.github` changed. The `#desk` page, the 5-item bottom nav, the 6-item rail, the HALT and RESUME code, the typed confirmation words, the RESUME awaiting-fresh-snapshot gate and `observeGate` are untouched.

## Layout at 390 px (OBSERVE worker, incident strip showing)

| Element | Top (px from page top) | Bottom |
| --- | --- | --- |
| Incident strip | 251 | 342 |
| Room (header, scene, wall, stations) | 354 | 1160 |
| Room scene | 408 | 768 |
| Station list | 900 | 1150 |
| KPI cards | 1232 | 1438 |

Viewport 844 px, bottom nav from 796 px. The whole scene clears the bottom nav before any scroll. The station list starts in the second viewport and ends inside it. At 320 px the scene is 426 to 716 and the station list 848 to 1089. F24-4 is not closed: the station list still starts just below the first screen because the scene is a square. Closing it would mean changing the scene aspect or the room markup, which this change does not do.

Screenshots: `screenshots/overview-room/overview-room-390-screen1-en.webp`, `overview-room-390-screen2-en.webp` and `overview-room-1440-en.webp`. They show the real local OBSERVE snapshot. Values that the worker does not report show as "Not reported" or "Unavailable".

## QA assertions changed

Both encoded "the room is only on `#desk`" or "the KPI cards are in the first screen", which the new requirement reverses.

1. `web/qa/shell.py`, after sign-in: `.room-summary` count `0` became `1`.
2. `web/qa/shell.py`, at 320 and 390 px: `.metrics` bottom `< height - 60` was replaced by `overview_room_is_first_screens`. The new check asserts the room scene fits above the bottom nav with no scroll, the six stations and their list end inside two viewports, the KPI cards end inside two viewports, the bottom nav has exactly 5 items and no `#desk` item, the "Open Trading Desk" link still opens a `.desk-workspace` with its own room and six stations, and Overview returns.
3. `web/qa/control_room.py`, after returning from `#desk`: `expect(room).to_have_count(0)` became `expect(page.locator(".overview .trading-room")).to_have_count(1)`.

Every other suite, including the 344-layout gate, the accessibility audit and the performance suite, passed with no edit.

## Performance

The room component and its CSS were already in the first-load bundle (`monitor.tsx` imported them for `DeskPage`), so the JS delta is only the extra JSX.

| Measure | Base `d7ee9fe` | This change | Delta |
| --- | --- | --- | --- |
| Index JS, Vite count | 352.93 kB | 353.19 kB | +0.26 kB |
| Index JS gzip, Vite count | 111.21 kB | 111.25 kB | +0.04 kB |
| Index JS gzip, `bundle_report.py` (zlib 9) | 110.67 kB | 110.71 kB | +0.04 kB |
| CSS | 63.30 kB | 63.30 kB | 0 |
| Activity chunk | 7.92 kB | 7.92 kB | 0 |

`control-room.webp` (259.75 kB) is an image, not first-load JS, but Overview now requests it on first load where before only `#desk` did. The ambient motion gates are unchanged: `data-motion` is on only when the room is on screen (IntersectionObserver), the tab is visible, the motion preference is on and text mode is off, and the CSS animations are inside `prefers-reduced-motion: no-preference`.

`scripts/qa/run_suites.sh` passes `--expect-index-kb 352.93` to `bundle_report.py`. That script is outside the files this change may edit, so its expectation is now stale and the `bundle` step reports a mismatch until it is raised to 353.19.
