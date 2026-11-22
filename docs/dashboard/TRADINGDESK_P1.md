# P1 telemetry contract and QA

Base: `4a34e15105ef0ca077d241e32242da5c5a2cdd67`, the merge of PR #19. Open **Overview → Telemetry evidence** to inspect the four KPI cards and six role observations. The disclosure uses the existing authenticated reads and contains no operator actions.

## What each number means

| Display | Source and calculation | Source clock, validity and failure |
| --- | --- | --- |
| Equity | Worker status, `equity` from the last decision. The API sends a decimal string. | Decision mark time is absent. A numeric value has unknown freshness even when the worker just published. Missing or malformed is unavailable. |
| Today's PnL | Derived in the worker, `pnl_today` is the difference between the latest and earliest reliable equity marks for the current UTC day. | Individual mark times are absent. A reported decimal zero survives. No reliable marks yields null. |
| Active WARP bots | Count of journal bots with status `confirmed` or `active` in `/api/warp-bots`. | No inventory source time. A validated empty list confirms zero for that read. Malformed statuses invalidate the count. |
| Open positions | Length of `/api/positions.positions`, the existing SmartTrade-only KPI. The separate account book includes DCA/grid positions. | Successful strategy read `as_of`, 300 s. Errors can advance the attempt time. Failed, expired or unstamped reads hide the exact count. |
| Discovery and analyst output counts | `status.pipeline.screening.count` is the worker universe size. `analysis.count` counts current cards. | Worker publication time describes the projection only. Individual scan/model completion times are absent. |
| Market tape and screener | Binance quotes in `/api/prices`. Screener results have a separate `source` and `fetched_at`; the display feed is independent of automated discovery. | Price TTL from `limit_seconds` (30 s default). Existing screener age limit is 1,800 s. Show available rows and upstream errors. |
| Account balances, holdings and position details | `/api/accounts` supplies profile balances. `/api/account-positions` supplies combined rows, broker values, optional Binance marks and labelled derived PnL. | 30 s balance cadence × 3, 60 s book cadence × 2.5. A failed account attempt cannot establish current balances. P0A preserves unassigned rows, last-good figures, partial errors and unknown residuals. |
| Opportunity levels and score | `/api/opportunities.cards` contains closed-candle evidence, analyst output and deterministic levels. The rubric score is separate from price and PnL. | Per-card model and evidence clocks are independent. The worker supplies its freshness verdict. |
| Budget, presets, forward results, configuration | Existing worker/journal projections. | Several routes have no producer timestamp. Their receipt labels explicitly identify the browser. No producer freshness is inferred. |

## Six role observations

| Role | Evidence used | Interpretation |
| --- | --- | --- |
| SCAN | `status.pipeline.screening.state` | Worker reports ready or blocked. It does not publish a scanning lifecycle event. |
| ANALYST | `status.pipeline.analysis.state` | Ready or pending in the worker projection. No running inference from a retained card. |
| AEGIS | Authoritative HALT overlay, then `status.pipeline.risk` | HALT takes precedence over the projected pass/blocked verdict. The reason stays attached. |
| EXECUTOR | Server mode and `execution_permitted` | OBSERVE is explicit. In other modes the permission describes a gate, never a submission or fill. |
| PORTFOLIO | Independent account and combined-position reads | Both reads must succeed before the portfolio is confirmed. Expire each using its own TTL. The combined source time is the older of the two known clocks. Partial/retained evidence cannot confirm zero. |
| WATCHER | `/api/events` snapshot and latest emitted event | Event feed observation, with a 60 s snapshot TTL. Display a trace ID only if the producer supplies one. An empty feed says nothing about broker health. |

The four status-based roles use the shell's existing 900 s worker-publication threshold. Stage-specific activity timestamps are unavailable. Source times and receipt times render in the browser's local zone. Unsupported clocks remain unknown.

## Transport and failure handling

- A monitoring hook starts one shared session-authenticated EventSource. An authenticated hello or heartbeat establishes connectivity. Disconnection activates REST fallback at five seconds after completion of the previous read. Reconnect uses the existing exponential backoff. Editable forms continue to use HTTP.
- Compare worker snapshot envelopes on their publication clock. Compare clocked route data on its route-specific clock. Preserve the last accepted evidence watermark when a fresh failure reports older retained positions or prices. Duplicates preserve the original accepted receipt. Microsecond ordering is retained for Python-produced timestamps.
- Dedicated events lack a global event ID and publication time. Untimed or equal-time changed frames use delivery order when sequence cannot be proven. HALT overlays with the same worker stamp remain observable. Browser time never orders upstream observations.
- Receipt is the instant the browser first accepted that distinct payload. Repeated frames, an open socket and heartbeats do not advance source age. Session expiration closes the stream, clears route cache and returns the operator to sign-in.
- The presentation model distinguishes loading, awaiting first snapshot, confirmed, confirmed empty, partial, stale, unavailable, request/shape error and unauthorized. Availability and freshness are independent. A retained value can remain visible alongside its error. Exact broker position counts require a successful current read.

## Verification

| Check | Result |
| --- | --- |
| Baseline / P1 Python | 148 tests passed at both revisions. |
| Static checks and build | Ruff repository-wide, clean npm install, TypeScript and Vite build passed. |
| Frontend unit tests | 17 passed, including null, decimal zero, absent/future/invalid timestamps, independent portfolio TTLs, authorization loss, duplicate and out-of-order frames, and equal-time gate changes. |
| Authenticated Chromium | The account-read suite and the P1 transport suite passed. The latter exercises all ten disclosure rows, malformed and partial books, a timed REST fallback, reconnection, restored SSE updates and HTTP 401. |
| Responsive Chromium | Nine views × two languages × seven widths. 108/126 fit. All 18 cases at 320 px reproduce the baseline's 28 px document overflow. Zero uncaught page errors. The shared-shell fix belongs to P2. |

| Real local OBSERVE captures | Before | After, disclosure open |
| --- | --- | --- |
| 390 × 844 | [Mobile](screenshots/p1/before-390.png) | [Mobile](screenshots/p1/after-390.png) |
| 1440 × 960 | [Desktop](screenshots/p1/before-1440.png) | [Desktop](screenshots/p1/after-1440.png) |

Reproduce from the root with the project installed, Python Playwright and Chromium available. Set `CHROMIUM_PATH` when using a system browser.

```sh
PYTHONPATH=src python -m unittest discover -s tests
ruff check .
(cd web && npm ci && npx tsc --noEmit && npx vite build && node --test *.test.ts)
python web/qa/account_reads.py
python web/qa/telemetry.py
python web/qa/account_reads.py --layout /tmp/warp-p1-layout
```

The layout command exits 1 on the documented overflow. Screenshots show an actual local worker with no broker credentials. Synthetic failure frames exist only in test processes. Production broker reads and physical-device performance were not measured. This PR changes the web read path, tests and review evidence only. No backend, trading mode, broker write path, authentication endpoint or deployment change.
