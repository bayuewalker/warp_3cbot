# Dedicated Trading Desk

Open `/dashboard#desk` from the desktop rail or the Overview link. The mobile bottom navigation keeps the five P2 destinations. A direct Desk link survives sign-in. Overview keeps account KPIs, the pipeline and existing controls.

The trading floor is an original cyberpunk office interior with a rain-lit city window and industrial consoles for six seated robot agents. Tap a desk nameplate to inspect the corresponding worker evidence. The illustration stays the base art. A lazy overlay adds decorative motion and data-driven desk states on top of it. The adjacent status list supplies the actual agent state and opens the same detail from the keyboard. On phones, the market screen and status list sit below the office. OBSERVE, HALT and connection status remain visible in the shared header. Text-only mode hides the set and illustrations. Reduced motion, a hidden tab, an offscreen room, the motion switch, and an open detail panel hold the overlay on a static frame.

## Data on the desk

| Display | Source | Freshness and fallback |
| --- | --- | --- |
| Market screen | Existing `/api/prices` observation | Uses the reported quote clock, freshness limit and P1 skew tolerance. A retained quote carries its stale or error label and its trace is hidden. Absent prices remain unavailable. |
| Session trace | Up to 60 distinct source times per currently listed symbol | Plots received Binance quotes after two valid samples. Repeated or older source timestamps do not append. History is local to the mounted desk. No candle history is assumed. |
| Six stations and KPIs | Existing P1 telemetry adapter and P3 room | Missing balances, positions or readings remain labelled. Confirmed zeros remain visible. |
| Latest ranked research | First card in the worker's `/api/opportunities` sequence | The worker publishes `expires_at` from the older of `as_of` and `evidence_as_of` plus `WARP_MAX_SIGNAL_AGE_MINUTES`. Desk requires both clocks, the worker's freshness flag and a current publication. It expires retained research at the deadline, including while SSE heartbeats continue. A legacy card with no deadline has unknown freshness. The Signals link opens the P4A evidence view. |
| Worker events | `/api/events` | Lists recent events with server timestamps and refresh status. |

## Alive overlay

The backdrop file is unchanged. `RoomAlive` is a lazy layer inside `.control-room-scene`, and `room-life.ts` loads with that chunk. Its CSS animates only `transform` and `opacity`, and only while `data-play` is on. Rain, window neon, lamp flicker, floor shimmer, machinery LEDs, and the idle eye breath are decorative and screen-blended, so a still of the idle room still reads as the original art. They do not report a worker state. The monitor glow, typing sparks, speech bubbles, the red sheen, the halt wash, the stamp, the radar, and the label LED do. Nothing opaque is painted over the monitor glass.

Coordinates are percentages of the square scene, measured on the 1152×1152 art.

| Desk | Monitor left, top, size | Eye | Lamp | Keys | Bubble |
| --- | --- | --- | --- | --- | --- |
| SCAN | 18.2, 21.6, 8.6×7.2 | 17.6, 28.0 | 11.4, 23.4 | 20.5, 31.6 | 4, 29.2 |
| ANALYST | 44.2, 20.8, 9.6×7.4 | 47.2, 30.2 | 40.2, 21.6 | 50, 31.8 | 38, 29.4 |
| AEGIS | 76.4, 20.8, 9.2×7.0 | 72.2, 28.4 | 91.2, 25.8 | 79, 31.2 | 73, 29.2 |
| EXECUTOR | 12.4, 43.2, 13.2×8.4 | 14.2, 49.6 | 5.8, 42.4 | 18, 53.6 | 1, 57 |
| PORTFOLIO | 46.6, 42.2, 11.4×8.6 | 49.4, 51.0 | 43.6, 46.8 | 52, 53.8 | 36, 58 |
| WATCHER | 78.6, 42.6, 11.2×8.4 | 78.2, 50.2 | 73.8, 42.2 | 83, 53.6 | 66, 57.5 |

Rain covers the window band at left 7%, top 5%, 88%×16%, as thin screen-blended streaks. The floor shimmer sits at top 63%, height 14%, also screen-blended and faint. Four machinery LEDs sit at 21%×95.5%, 38%×90%, 72%×89% and 88%×94%. Each monitor carries a soft radial in the role colour, screen-blended, breathing between about 0.06 and 0.18 opacity while idle. Working brightens that same radial. A scanline sheen crosses the glass about every 6 seconds and peaks near 0.12 opacity. Sleep leaves the painted screen alone and only dims the eye. Each nameplate keeps its dark backing and carries a 5px LED inside the existing line, so the front-row band at 360–430px does not grow.

| Visual | When | Source |
| --- | --- | --- |
| Working | Newest mapped event for that desk is inside 12s and is not a failure | `/api/events`. The mapped non-failure names are `observe`, `observe_candidate`, `duplicate_skipped`, and `strategy_confirmed`, all on EXECUTOR. The bubble is `reason` when it is a real sentence, otherwise a human label such as "Strategy confirmed", clipped to 28 characters, plus the event time in WIB. |
| Idle | No in-window event, and the observation is not stale, missing, or in error | Confirmed, partial, empty, unauthorized, or `loading`. Unauthorized stays idle because it is a denial, not a sleeping worker. `loading` stays idle because the worker has not reported a stale or failed read. |
| Alert | Newest in-window event is `market_data_blocked`, `research_blocked`, `llm_provider_unhealthy`, `halt`, `entries_paused`, `alert_failed`, or `cycle_blocked` | That event. `alert_failed` and `cycle_blocked` prefer `alert_event`, then `reason`, then a human label. AEGIS and WATCHER also run a faint red sheen. |
| AEGIS alert from status | No newer AEGIS event, publication is inside 15 minutes, and `halt` is true or `risk_permitted` is false or `pipeline.risk.state` is `blocked` | `status.reason`, then `pipeline.risk.reason`. The words `ok` and blank are not used as a sentence. The fallback label is "Halted" or "Blocked". |
| Halt wash | `status.halt` is true and the publication is inside 15 minutes | `/api/status`. A stale publication does not tint the room. |
| Sleep | The desk observation is missing, `freshness` is `stale`, or `state` is `stale`, `unavailable`, `error`, or `awaiting_first_snapshot` | That observation. The bubble is the letters zzz. A fresh event in the 12s window wins over sleep. `loading` is not sleep. |
| Stamp | The newest EXECUTOR event in the window is `strategy_confirmed` and `mode` is `paper` or `live` | A small screen-blended flash on that monitor, not a box around the glass or the room. Same guard as `sweep()`. `observe` never stamps. |
| SCAN radar | `pipeline.screening` state, count, and source change to a key whose state is `ready` | The previous key held on the client. The first sample does not sweep. A change to `blocked` does not sweep. No scan-complete event is published on `/api/events`. |
| Not drawn | XAU market-closed sleep | This art has no XAU seat, and the classic room's `Status` type has no `xau` field. Nothing is invented for it. |

The classic scene does not draw a ratio bar. `clampPercent(used, cap)` is the clamp for a bar width: a sample count above its cap, a negative count, and a zero cap all stay inside 0–100. The 3D view still prints a basis count as text, not as a width.

## Backdrop provenance

Codex generated the replacement `web/src/tradingdesk/art/control-room.webp` on 2026-11-23 using OpenAI ImageGen. The text brief described a rain-lit office, two rows of three original robot workstations and their placement based on the app's clickable desk grid. Generation used that brief alone, with no Vembit artwork, sprites or composition as input. Codex resized the 1254 × 1254 source PNG to 1152 × 1152 and encoded it with ImageMagick WebP quality 84.

| File | SHA-256 |
| --- | --- |
| Source PNG, `exec-3faab780-ba36-4182-a03a-34bcae2229ce.png` in the review evidence | `536bd7ed51e4db2916a93a08f73b6cc9818e22e306f358b8d3f0151f36c7f15c` |
| Repository WebP | `73c2c58293c1f922cd1437053b34fda3523ccc1819bd2f6193a25151ad02437c` |

Under the OpenAI Terms of Use, OpenAI assigns its rights, if any, in the generated output to the account holder to the extent permitted by applicable law. The backdrop was created for use in this repository. This provenance record grants no separate public license. [OpenAI Terms of Use](https://openai.com/policies/terms-of-use/)

## QA

Base `81fd1f9` includes P4A. Local Chromium uses an authenticated OBSERVE worker with a temporary SQLite journal. Screenshots capture its startup state, and isolated stream fixtures supply quotes and research for the regression tests.

| Check | Output |
| --- | --- |
| Frontend | `npm ci`, TypeScript, Vite and 36 frontend tests. |
| Python | 148 tests passed with shell proxy variables unset. |
| Ruff | All checks passed. |
| Browser | Account reads, telemetry, shell, room, P4A opportunities and dedicated desk. The dedicated desk checks sign-in, quote loss, local research expiry during live SSE, zero mutations, six telemetry dialogs and 44 px touch targets. |
| Responsive matrix | 140 layouts, 0 with horizontal overflow, 0 page errors. Covers seven widths and both languages. |

The assets are served locally. The backdrop is approximately 260 kB.
