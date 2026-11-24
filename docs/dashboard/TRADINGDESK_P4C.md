# TradingDesk 2.0 · P4C Activity, alerts, Settings and Risk

Settings & risk gains two tabs, **Activity** and **Risk & health**, and Overview gains an incident strip that appears only while a real incident exists. Nothing here adds an endpoint, a write call, a permission or a navigation item. The mobile bar stays at 5 items and the rail at 6.

Every activity row comes from a real source row. A value the source did not report reads **Not reported**. The UI never invents a time, an id, a severity, an outcome or a trace id.

The view models are in `web/src/tradingdesk/activity-model.ts`. They use the P1 `observe()` reducer and the P1 `sourceInstant` rule (strict ISO with a zone, future times beyond `MAX_CLOCK_SKEW_MS` = 5 s rejected). The UI is `web/src/tradingdesk/Activity.tsx`.

## Activity timeline lineage

Reads: `/api/events`, `/api/alerts`, `/api/journal`, `/api/signals`, `/api/breakers`. All are existing GET routes.

| Item field | events | alerts | journal (submissions) | signals (decisions) | breakers |
| --- | --- | --- | --- | --- | --- |
| Envelope | `events[]` | `alerts[]` | `submissions[]` | `decisions[]` | the object itself |
| `source` | `events` | `alerts` | `journal` | `signals` | `breakers` |
| `kind` | `event` | `event` | fixed `submission` | `stage` | fixed `breaker` |
| `at` (source time) | `ts` | `created_at` | `created_at` | `recorded_at` | none (always "time not reported") |
| `outcome` | not reported | `state` | `state` | `detail` | `reason`, or `halt` / `permitted` |
| `severity` | not reported | `pending` is Warning, `delivered` and `resolved` are Info, any other state is Not reported | not reported | not reported | `halt` is Problem, `risk_permitted` false is Warning, permitted is Info |
| `reason` | first of `reason`, `error`, `detail` | `last_error` | not reported | `reason` | `reason` |
| `refId`, `refKind` | `strategy_id` (`strategy_id`) | `id` (`alert_id`) | `client_id` (`client_id`) | not reported | not reported |
| related ref | none | none | `strategy_id`, shown only when the row has one | none | none |
| `receivedAt` | browser receipt of the read | same | same | same | same |

Rules that apply to every source:

- `at` is set only when `sourceInstant` accepts the source value. A missing, malformed, timezone-less (for example the SQLite `2026-11-24 09:59:00` format used by `journal.created_at`) or future (beyond 5 s) value leaves `at` null. That row goes to the separate **Time not reported** group and is never placed on the timeline.
- Timed rows are sorted newest first on the source time (microsecond aware, ties broken by source order and row order). `receivedAt` never decides order.
- De-duplication happens only when `refId` is not null, on the exact key `(source, refId, at)`. Rows are never merged across sources and a missing id is never generated.
- Rows that cannot be read at all (not an object, or no kind) are counted per source as "unreadable" and make that source **Partial**. They are not shown as rows.
- An unknown event kind is shown as reported, with its underscores replaced by spaces. No kind is dropped for being unknown.
- Severity is shown only where the source states it or where it maps 1:1 from a documented field (alert state, `halt`, `risk_permitted`).
- The **Agent** filter does not exist as a separate dimension. Rows carry no agent field, so the Kind filter is the only agent-like filter.

### Per-source state and freshness

| Source | Own clock | Freshness basis | Window |
| --- | --- | --- | --- |
| events | `as_of` | `as_of` against 60 s (`EVENTS_TTL_S`) | latest 20 |
| alerts, journal, signals, breakers | none | `/api/status` `published_at` (worker publication) against 900 s (`WORKER_PUBLICATION_TTL_S`) | latest 30 (breakers: one object) |

The badge row shows Confirmed, Confirmed empty, Partial, Stale, Refresh failed, Unauthorized or Loading for each source. A failed or stale `/api/status` read downgrades the four sources that depend on it to stale. A failed read never renders as an empty list. "Confirmed empty" is shown only for a valid read with zero rows.

Counts in the filter options and the "Showing X / Y" line are labelled **in loaded window**. They count the rows already loaded, never the history.

## Risk & health lineage

| Value | Route | Field | Note |
| --- | --- | --- | --- |
| Worker health | `/api/status` | `published_at`, `dependencies_healthy` | Current, Stale (older than 900 s) or Failed. Not a heartbeat of the process, only of the last publication. |
| Providers | `/api/status` | `providers{}` `state`, `model`, `detail`, `until` | A provider is an incident when `state` is `cooldown`, or any state other than `ok` and `unconfigured`. A cooldown shows the localized end time. A provider only shows green while the worker snapshot is current and the stream is connected. |
| Risk breaker HALT | `/api/breakers` | `halt` | Also compared with `/api/status` `halt`. Both are listed with their source route. |
| Risk permitted | `/api/breakers`, `/api/risk` | `risk_permitted` | Both listed. False without HALT is a risk incident. |
| Execution permitted, reason, equity | `/api/risk` | `execution_permitted`, `reason`, `equity` | |
| Equity reliable | `/api/risk` | `equity_reliable` | Null renders Not reported. |
| Stream | browser | `liveConnected()`, `liveLastFrameAt()`, `liveDownSince()` | Connected, Disconnected · HTTP polling (stream down, `/api/status` readable) or Disconnected · reads failing. |

The overall badge is **No incident reported** only when all of these hold: worker snapshot current, stream connected, HALT false in both sources, risk permitted true in both, `/api/breakers` and `/api/risk` confirmed, every provider `ok` or `unconfigured`, and no RESUME awaiting a snapshot. Otherwise it is **Incident**, **Awaiting fresh snapshot** or **Not verified**. It is a status read, not an execution permit.

### OBSERVE mode gate

In OBSERVE mode the worker always reports `risk_permitted=false` with reason `observe` or `observe_without_account`. That is the intended state, not an incident. `riskHealth` adds no risk incident when `status.mode` is `observe` and every `risk_permitted=false` read (`/api/status`, `/api/breakers`, `/api/risk`) carries one of those two reasons. It sets `observeGate` instead, and the UI shows a neutral chip: EN "Observe mode · entries blocked by design", ID "Mode observe · entri diblokir sesuai desain". The overall level is never "clear" in that state (it reads "Not verified", or "Awaiting fresh snapshot" after a RESUME). A HALT, any other reason, a missing reason, a non-observe mode (including paper) or an unreadable mode stays an incident. The chip appears in the Risk & health overview card, and inside the strip when the strip is already visible for another incident. On its own it never opens the strip.

### Incident strip

`IncidentStrip` mounts above Overview and uses only the status read already loaded plus the stream state, so it adds no request. It lists an entry for each of: HALT active, risk gate blocking entries, an AI provider in cooldown or error, stream disconnected for more than 5 s (`STREAM_GRACE_MS`), `/api/status` read failed, `/api/status` snapshot stale. Each entry links to Settings & risk, Risk & health. With no incident the strip renders nothing.

## HALT and RESUME

`RiskControls` is the same component, reused in the Risk & health tab. Its `send()` body, endpoints (`/api/pause`, `/api/resume`) and typed-word check (`HALT`, `RESUME`, case-insensitive trim) are unchanged. Only presentation was added.

After a RESUME acknowledgement the UI records the ack time and the `published_at` it was acknowledged against. `resumeGate` reports **awaiting** until `/api/status` `published_at` is strictly newer than the ack time and differs from the pre-ack value. While awaiting:

- the Entry gate and Execution rows read "Awaiting fresh snapshot" instead of On or Off,
- the Risk & health overall badge reads "Awaiting fresh snapshot",
- nothing on screen turns green from the ack alone.

A HALT acknowledgement discards any pending RESUME ack. The ack is held in module memory only (nothing is persisted), so a page reload drops it and the next `/api/status` is shown as reported. Because the gate compares browser time with the worker's `published_at`, a browser clock far ahead of the worker keeps it awaiting until the worker's clock catches up.

## Settings and rules

`RulesPage` and `BudgetForm` validation, POST bodies and endpoints are unchanged. P4C adds:

- the note "A saved setting is not an order" under both forms (EN and ID),
- readable save errors for `invalid_rules`, `invalid_budget` and `journal_unavailable`, shown with a bad tone, wrapped for 320 px. Any other failure keeps the generic message.

## Known limits

- **Bounded windows.** Events 20, alerts 30, submissions 30, decisions 30. The UI says "latest N reported" and never implies a full history. There is no server pagination and none was added.
- **Events are lost on restart.** `/api/events` reads worker memory. After a restart the list is empty or short, and that is confirmed-empty, not an error.
- **No trace ids.** No source carries a trace or correlation id. Alert id, client id and strategy id are shown as separate references. The UI never links a row from one source to another.
- **Breakers have no time.** The breaker row is the current gate and always sits in the "time not reported" group.
- **Journal time.** `journal.created_at` is a SQLite `YYYY-MM-DD HH:MM:SS` string without a zone, so `sourceInstant` rejects it. Submissions are therefore always in the untimed group until the worker publishes a zoned time. Guessing UTC was rejected on purpose.
- **Shared clock.** Alerts, journal, signals and breakers have no own clock. Their freshness is the worker publication time, which can be fresh while one source's data is old.
- **Overview system card and pipeline.** These still derive state from retained data and are out of scope for P4C. The incident strip is the new, conservative signal.
- **Unchanged from main:** `POST /api/warp-bots/action` has no server-side mode gate (pre-existing, not touched), and the F24-4, F24-5 and P4B stale-versus-partial follow-ups are untouched.

## Bundle

| | JS | gzip |
| --- | --- | --- |
| main (P4B), `index-*.js` | 336.07 kB | 105.51 kB |
| P4C first-load `index-*.js` | 352.93 kB | 111.21 kB |
| first-load growth | +16.86 kB | +5.70 kB |
| P4C `Activity-*.js` (lazy chunk) | 7.92 kB | 2.98 kB |
| total JS growth, all chunks | +24.78 kB | +8.68 kB |

The Activity timeline and the Risk & health panels are `React.lazy` chunks, fetched only when one of those two Settings tabs opens. The incident strip, the models and the resume gate stay in the first-load bundle, because Overview and `RiskControls` use them. First-load growth is inside the 20 kB and 7 kB gzip targets. Total shipped JS is not: it is +24.78 kB raw and +8.68 kB gzip. I found no way to get the total under 20 kB without removing EN/ID strings or data rows.

## Screenshots

`docs/dashboard/screenshots/p4c/`: `p4c-activity-320-en`, `p4c-activity-390-en`, `p4c-activity-320-id`, `p4c-activity-1280-en`, `p4c-incident-390-en` (Overview with the HALT incident), `p4c-incident-observe-390-en` (strip with the observe chip and a provider cooldown) and `p4c-risk-observe-390-en` (Risk & health in OBSERVE mode). They were captured from the real local worker with injected snapshots by `web/qa/activity.py` (`WARP_QA_SCREENSHOTS=<dir>`). Full-page captures include the fixed bottom navigation once, mid-page.

## Verification

`web/activity.test.ts` covers the model. `web/qa/activity.py` covers the browser behaviour. The layout audit (`python web/qa/account_reads.py --layout DIR`) now includes the Activity and Risk & health tabs: 2 languages, 7 widths and 12 route views each, 168 layouts.
