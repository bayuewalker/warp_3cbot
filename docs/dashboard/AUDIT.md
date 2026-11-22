# Operator dashboard audit

Baseline is PR #6 at `194f836`. The review follows the React entry point, shared desk controls, translations and the payloads published by the Python worker.

| Observation | Operator impact | Design change |
| --- | --- | --- |
| The shell stops at 768px and keeps a two-row bottom bar on desktop. | Monitoring panels stack while most of a wide screen is unused. | Persistent desktop rail, compact mobile navigation and a fluid content grid. |
| Positions default to zero before a successful response. Blank snapshots return HTTP 200 with an empty object. | A disconnected desk can appear flat or crash. | Distinguish unavailable, confirmed zero, loading and retryable errors. |
| Pipeline descriptions repeat the runbook. There is no cross-page HALT indicator or status refresh. | Operators cannot tell what the worker observed while editing a preset. | Put mode and HALT in the shell. Keep source, counts and gating reasons next to each stage. |
| Forward-test history precedes current opportunities. Screener cards mix numeric columns into one sentence. | Comparing setups requires repeated scanning. | Lead with ranked candidates, label units, keep chart links manual, and use contained tables. |
| The confirmation field is detached from the selected bot. Action completion clears the list without reloading. | A mobile operator loses context and cannot verify the result. | Place typed confirmation alongside the target, disable until matched, and refresh after submission. |
| Several mutation failures are unhandled. Disabled RSI inputs and unlabeled fields occupy mobile space. | The desk can imply a save succeeded or hide why a field has no effect. | Surface request outcomes, label the momentum bounds and explain supported filters. |

PR #5 at `46146dc` independently adds provider cooldown, model and health fields to status and changes the old Overview component. Consume its optional fields when supplied. The missing provider telemetry on #6 must stay explicit. Reconcile the two Overview edits when stacking changes upstream.

PR #6 supplies stage state and selected counts, plus per-card signal and evidence times. It does not publish stage completion times, a numeric risk score, or a separate screener timestamp. Keep these distinct from the time the browser fetched a snapshot. Retain the existing Binance discovery and manual TradingView chart boundary.

## WalkerMind theme revision

PR #14 applied the WalkerMind theme on the stale `feat/vibe-pipeline-safe-auto-screening` base. This revision carries the theme onto `main` and keeps main's TradingView screener, SSE price stream and per-profile account handling. It also fixes labels that were 10–11px, mixed UTC/local times, raw source ids (`tradingview`, `binance_spot_public`) and raw eight-decimal numbers.
