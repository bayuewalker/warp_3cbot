# TradingDesk live office

Read-only view inside the existing Trading Room. Classic stays the default. The bottom nav is unchanged.

## Toggle

The room header has a `Classic | Live` switch, next to the existing display settings. The choice is stored in `localStorage` under `warp.room.view` (`2d` by default, `3d` for the live office). The live view is a pixel-art canvas and does not need WebGL. While that chunk loads, and if the canvas throws, the classic room is shown. A render error returns to classic for the rest of the page session and logs once to the console. The saved preference is not cleared, so a reload can try the live view again.

Eight status cards sit above the room. A value is a real field, or a dash when that field is missing or the read is stale or unknown. Tap a card or a desk to open the existing room detail for SCAN, ANALYST, AEGIS, EXECUTOR, PORTFOLIO and WATCHER. XAU and Scorer open a read-only drawer listing the fields the pose used. A visually hidden list of desk buttons repeats the role, the bubble text and the time.

## Truthfulness

Every pose, lamp, prop and number comes from a field on an already loaded read, or from a change between two consecutive frames of that read. The first frame after load, including the first frame after a read goes from loading to present, shows the steady pose. There is no `Math.random` and no `setInterval` in `web/src/tradingdesk/office3d/`. While room motion is on, the render clock only adds idle shimmer, a plant sway, blinking server lights, breathing and blinking. Those offsets do not read or change the pose, the lamp or the bubble. Reduced motion, a hidden tab, or an open detail leaves the poses static. A character types only when the pose is already typing or thinking. A signal flag, an observe paper ticket, or an order flag walks a paper to the chief seat. The phone, the risk alarm, XAU sleep, and the scorer stamp come from those poses and props. The wall chart is the retained last-price series from `/api/prices`. One sample is a label, not a drawn candle.

A stale or unauthorized read wins over an active pose: the character goes to the unknown posture and the lamp goes off. `mode: "observe"` never shows an order flag. An observe candidate inside the 12 second event window is a paper ticket stamped OBSERVE.

Screening "new publication" is a change in `pipeline.screening` (`state`, `count`, `source`). `published_at` moves on every worker publication, so it is not treated as a new scan.

## Desk to signal

| Desk | Sources | What the scene does |
|---|---|---|
| Market Scanner (SCAN) | `status.pipeline.screening`, `status.market_data_fresh`, `market_data_blocked` | Ready and fresh: typing only when the screening block changed since the previous frame, otherwise idle, lamp green. Blocked or `market_data_fresh === false`: alert, red. Publication older than 15 minutes, or a stale/unauthorized/loading read: unknown, lamp off. |
| Research Analyst (ANALYST) | `status.research`, `status.pipeline.analysis`, `providers.budget` | `research.running === true`: thinking, blue. `ok` and not running: coffee when `next_attempt_at` is in the future, otherwise idle, lamp green. `budget_wait` or `capped`: coffee, amber, bubble `calls_used/calls_cap` when both numbers are present. `backoff` or `failed`: alert, red, bubble has `error_code` and `next_attempt_at` in WIB. `pipeline.analysis.state === "blocked"` on a fresh publication does not turn the desk unknown when the agent observation is unavailable: backoff and failed stay red, capped and budget wait stay amber. A stale, unauthorized or loading read still wins. Missing `research` with analysis not blocked: unknown. |
| Risk Officer (AEGIS) | `status.halt`, `status.risk_permitted`, `status.reason`, `pipeline.risk`, `/api/breakers` | Halt on status or a successful breaker read: alert, red, alarm. `risk_permitted === false`, breaker `risk_permitted === false`, or risk state `blocked`: alert, red. `insufficient_free_usdt` says "Free USDT below sleeve need" and does not invent a number. Risk `pass` with `risk_permitted === true`: idle, green. |
| DCA Trader (EXECUTOR) | `status.mode`, `execution_permitted`, `gating`, events | `gating` `halt`: away, red. `awaiting_worker`: unknown, lamp off. `observe`: blue lamp and an OBSERVE plate; `observe_candidate` inside 12 seconds: stamping with a paper ticket. `paper` or `live` with `strategy_confirmed` inside 12 seconds: stamping with an order flag. Never an order flag when mode is observe. |
| Ledger (PORTFOLIO) | the existing portfolio observation | Confirmed and fresh: idle, green. Partial: idle, amber. Stale, unavailable, unauthorized or loading: unknown, lamp off. The bubble uses the same read labels as the 2D station. |
| XAU Desk | `status.xau` | `enabled` false or missing on a fresh status: empty desk, lamp off. Window `closed` or `off`: sleeping, lamp off. `warming` or state `warming`: warming, amber, bubble `basis_samples/10` only when `basis_samples` is a number (10 is `MIN_SAMPLES` in the basis book). `running` in London/NY KZ or EMA: typing when `last_bar_age_s` is a number and at most 180, otherwise idle, amber. `degraded`, `feed_unavailable`, `config_error`: alert, red, bubble has the state and a trimmed `last_error`. `halt.xau` or `halt.global`: alarm. `today.signals_sent` increased, or `open_signal` appeared, after a previous frame that already had an XAU block: signal flag for 12 seconds. A stale status publication wins over all of these. |
| Scorer (FORWARD) | `/api/forward` | `scored` increased after a previous frame that already had a number: stamping, scorecard. `pending > 0` with no increase: idle, green, a paper stack of `min(4, floor(log10(pending)) + 1)`. The bubble shows the exact `pending`. `pending === 0`: coffee. A read error or a missing number: unknown. The forward payload has no source time, so freshness stays unknown while the numbers are shown. |
| Comms (WATCHER) | `/api/alerts`, `/api/events`, the SSE connection | A row with state `failed`, or state `pending` with `last_error` set and `attempts >= 1`: alert, red. That red pose wins over the phone. Stream disconnected, and the events read is fresh: away, amber, "Reconnecting". After a frame that already saw alerts, the phone is the maximum id above the previous maximum whose state is `pending`, `delivered` or `resolved` and which is not the failed case above. The payload is newest-first, and the bubble's event and time are that row, for 12 seconds. Otherwise idle, green. Stale, unauthorized or loading events win. |

## Performance

- The live room is one `canvas` bitmap, scaled with `image-rendering: pixelated`. It does not load three.js.
- While motion is on, a due draw waits a full 1/30 s before the next frame. It does not draw while the document is hidden, the room is off screen, reduced motion is on, or a detail drawer is open.
- Day is fully bright 06:00–18:00 WIB. 05:30–06:00 and 18:00–18:30 blend. Night tints the windows darker. The phase is computed from `Asia/Jakarta` on the room clock.
- `prefers-reduced-motion` or `warp.room.motion === false` leaves poses static.

## Assets

Characters, desks, plants, the server rack and the neon strips are original pixel rectangles in `pixel.ts`. No texture, model or font is fetched at runtime. No third-party sprite is committed.

## Research `running` (T2)

`ResearchGate._status_locked()` now includes `running`, which is `busy()` (the research thread is alive). `once()` copies that view onto `engine.research_status` when a run starts. Settle already republishes the same view when the run is finished, so `running` is false there. Scheduling, budget, pacing and the research call are unchanged.

`operator._research_view()` copies `running` through as a boolean (`True` only when the stored value is `True`, otherwise `False`, including when `research_status` is missing). `publish()` writes that block to `/api/status`, and the SSE snapshot is the same published view. A missing or non-boolean value does not show the thinking pose.

## Checks

`cd web && npm run check:bundle` gzips `dist/assets` in the same decimal kB Vite prints (bytes / 1000). It fails if the entry JavaScript grows more than 1 kB over 113.01 kB, the entry CSS grows more than 1 kB over 14.28 kB, the lazy `Office3D` chunk exceeds 600 kB, or the entry statically imports that chunk. Those baselines are the merged office bundle. The dev harness at `web/dev/office3d-harness.html` is not an entry in `vite build`. The live chunk does not import three.js.
