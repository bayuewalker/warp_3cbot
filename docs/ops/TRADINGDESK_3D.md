# Archived TradingDesk 3D prototype

The production Trading Room is 2D only. The 3D prototype is accessible through the development harness at `web/office3d-harness.html`. The notes below describe the earlier dashboard integration.

## Former dashboard toggle

The room header has a `2D | 3D` switch, next to the existing display settings. The choice is stored in `localStorage` under `warp.room.view` (`2d` by default). 3D is offered only when the browser can create a WebGL2 or WebGL context. Otherwise the 3D button is disabled and the header says why.

Opening 3D lazy-loads `office3d` (three.js, `@react-three/fiber` 8.18.0, `@react-three/drei` 9.122.0). Nothing is fetched from a CDN. While that chunk loads, and if the canvas throws, the 2D room is shown. A context loss or a render error returns to 2D for the rest of the page session and logs once to the console. The saved preference is not cleared, so a reload can try 3D again.

Tap or hover a character for the live bubble (sentence, source route, source time in WIB). Tap again, or click, to open the existing room detail for SCAN, ANALYST, AEGIS, EXECUTOR, PORTFOLIO and WATCHER. XAU and Scorer open a read-only drawer in the same style listing the fields the pose used. A visually hidden list of desk buttons repeats the role, the bubble text and the time, so the view works from the keyboard without the canvas.

## Truthfulness

Every pose, lamp, prop and number comes from a field on an already loaded read, or from a change between two consecutive frames of that read. The first frame after load, including the first frame after a read goes from loading to present, shows the steady pose. There is no `Math.random` and no `setInterval` in `web/src/tradingdesk/office3d/`. Limb positions are static postures. The only motion is the wall ticker, and only while room motion is on and the user has not asked for reduced motion.

A stale or unauthorized read wins over an active pose: the character goes to the unknown posture and the lamp goes off. `mode: "observe"` never shows an order flag. An observe candidate inside the 12 second event window is a paper ticket stamped OBSERVE.

Screening "new publication" is a change in `pipeline.screening` (`state`, `count`, `source`). `published_at` moves on every worker publication, so it is not treated as a new scan.

## Desk to signal

| Desk | Sources | What the scene does |
|---|---|---|
| Market Scanner (SCAN) | `status.pipeline.screening`, `status.market_data_fresh`, `market_data_blocked` | Ready and fresh: typing only when the screening block changed since the previous frame, otherwise idle, lamp green. Blocked or `market_data_fresh === false`: alert, red. Publication older than 15 minutes, or a stale/unauthorized/loading read: unknown, lamp off. |
| Research Analyst (ANALYST) | `status.research`, `providers.budget` | `research.running === true`: thinking, blue. `ok` and not running: coffee when `next_attempt_at` is in the future, otherwise idle, lamp green. `budget_wait` or `capped`: coffee, amber, bubble `calls_used/calls_cap` when both numbers are present. `backoff` or `failed`: alert, red, bubble has `error_code` and `next_attempt_at` in WIB. Missing `research`: unknown. |
| Risk Officer (AEGIS) | `status.halt`, `status.risk_permitted`, `status.reason`, `pipeline.risk`, `/api/breakers` | Halt on status or a successful breaker read: alert, red, alarm. `risk_permitted === false`, breaker `risk_permitted === false`, or risk state `blocked`: alert, red. `insufficient_free_usdt` says "Free USDT below sleeve need" and does not invent a number. Risk `pass` with `risk_permitted === true`: idle, green. |
| DCA Trader (EXECUTOR) | `status.mode`, `execution_permitted`, `gating`, events | `gating` `halt`: away, red. `awaiting_worker`: unknown, lamp off. `observe`: blue lamp and an OBSERVE plate; `observe_candidate` inside 12 seconds: stamping with a paper ticket. `paper` or `live` with `strategy_confirmed` inside 12 seconds: stamping with an order flag. Never an order flag when mode is observe. |
| Ledger (PORTFOLIO) | the existing portfolio observation | Confirmed and fresh: idle, green. Partial: idle, amber. Stale, unavailable, unauthorized or loading: unknown, lamp off. The bubble uses the same read labels as the 2D station. |
| XAU Desk | `status.xau` | `enabled` false or missing on a fresh status: empty desk, lamp off. Window `closed` or `off`: sleeping, lamp off. `warming` or state `warming`: warming, amber, bubble `basis_samples/10` only when `basis_samples` is a number (10 is `MIN_SAMPLES` in the basis book). `running` in London/NY KZ or EMA: typing when `last_bar_age_s` is a number and at most 180, otherwise idle, amber. `degraded`, `feed_unavailable`, `config_error`: alert, red, bubble has the state and a trimmed `last_error`. `halt.xau` or `halt.global`: alarm. `today.signals_sent` increased, or `open_signal` appeared, after a previous frame that already had an XAU block: signal flag for 12 seconds. A stale status publication wins over all of these. |
| Scorer (FORWARD) | `/api/forward` | `scored` increased after a previous frame that already had a number: stamping, scorecard. `pending > 0` with no increase: idle, green, a paper stack of `min(4, floor(log10(pending)) + 1)`. The bubble shows the exact `pending`. `pending === 0`: coffee. A read error or a missing number: unknown. The forward payload has no source time, so freshness stays unknown while the numbers are shown. |
| Comms (WATCHER) | `/api/alerts`, `/api/events`, the SSE connection | A failed alert row: alert, red. Stream disconnected, and the events read is fresh: away, amber, "Reconnecting". A new alert id above the previous maximum, state `delivered`, after a frame that already saw an alerts payload: phone, blue, for 12 seconds. Otherwise idle, green. Stale, unauthorized or loading events win. |

## Performance

- Canvas `dpr` is `[1, 1.5]` at a viewport of 800px or narrower and `[1, 2]` otherwise. Antialias is off when `hardwareConcurrency <= 4` or `deviceMemory <= 4`. Shadows are desktop-only behind that same check.
- `frameloop` is `demand`. The canvas draws again when the mapped state or the WIB light changes, and while the camera is moving. It does not draw while the document is hidden, the room is off screen, or a detail drawer is open.
- One hemisphere light and one directional light. Day is fully bright 06:00–18:00 WIB. 05:30–06:00 and 18:00–18:30 blend. Night is dim and the status lamps brighten. The phase is computed from `Asia/Jakarta` on the room clock (the desk already ticks), not from a timer inside the 3D module.
- Orbit is touch rotate and pinch. Pan is off. Polar angle, azimuth and distance are clamped so the camera stays over the floor.
- `prefers-reduced-motion` or `warp.room.motion === false` leaves poses and lamps instant and turns off camera damping and the ticker scroll.

## Assets

Characters, desks, lamps and props are original procedural geometry (boxes, a low-segment sphere, a low-segment cylinder) with Lambert materials in the existing station colors. No textures, models, fonts or HDRIs are loaded at runtime. No third-party mesh is committed, so there is no asset license file.

## Research `running` (T2)

`ResearchGate._status_locked()` now includes `running`, which is `busy()` (the research thread is alive). `once()` copies that view onto `engine.research_status` when a run starts. Settle already republishes the same view when the run is finished, so `running` is false there. Scheduling, budget, pacing and the research call are unchanged.

`operator._research_view()` copies `running` through as a boolean (`True` only when the stored value is `True`, otherwise `False`, including when `research_status` is missing). `publish()` writes that block to `/api/status`, and the SSE snapshot is the same published view. A missing or non-boolean value does not show the thinking pose.

## Checks

`cd web && npm run check:bundle` gzips `dist/assets` with `gzipSync` and prints kB as bytes/1024. It fails if the entry JavaScript grows more than 2 KiB over 111.55 kB, the entry CSS grows more than 2 KiB over 14.04 kB, the lazy `Office3D` chunk exceeds 600 KiB, or the entry statically imports the 3D chunk or the room overlay chunk. Those two baselines are the entry gzip sizes on main at `f875695`. The dev harness at `web/office3d-harness.html` is not an entry in `vite build`. A `manualChunks` split was not used: assigning three to its own chunk also pulled shared modules into it and made the entry import that chunk on every page.
