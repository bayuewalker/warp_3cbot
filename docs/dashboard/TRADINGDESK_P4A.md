# TradingDesk 2.0 · P4A Discovery and Opportunities

The Screener page shows the worker universe and recorded Binance market checks. Its manual research panel keeps the existing TradingView feed, chart links and filter form. Opportunities preserves the worker array order. A card shows the published verdict, rubric, deterministic levels, provenance, backtest and associated symbol history. The AEGIS drawer supports keyboard opening, Escape and focus restoration.

## Data lineage

| Route | Fields | Source time vs browser receipt | Null, zero, stale or partial | Fallback and errors |
| --- | --- | --- | --- | --- |
| `GET /api/status` | `mode`, `halt`, `reason`, `universe`, `pipeline.screening` | `published_at` timestamps the worker projection. Individual market observations have separate clocks. | Missing membership stays unknown. Current confirmed empty membership is distinct from an absent read. P1 skew tolerance is 5 s and worker publication expiry is 15 min. | In auto mode, exchange discovery fails closed. The panel shows the worker reason and recorded exclusions when available. |
| `GET /api/config` | `discovery` | Configuration has no source timestamp. | Missing or failed mode reads display “Not reported.” | Automatic and static universes have distinct labels. Existing legacy screener mode is identified by its mode name. |
| `GET /api/screener` | `source`, `fetched_at`, `error`, `hits`, `filters` | `fetched_at` timestamps the feed fetch. Receipt is shown separately. | The display feed uses a 30-minute TTL. A failed attempt labels retained rows stale. Conflicting source labels show a mismatch and leave the row's chart inactive. A reported zero remains zero. | Manual research uses the existing worker cache. An operator follows chart links explicitly. The existing filter save retains its original route and payload. |
| `GET /api/opportunities` | Worker-ordered `cards`, `symbol`, `action`, `model_action`, `source`, `thesis`, `score`, `entry`, `stop`, `take`, `backtest_*` | Card `as_of` is the signal's published timestamp. `evidence_as_of` dates exchange evidence. The worker evaluates `fresh` at publication. Each card gives the publication time and the elapsed whole minutes since `evidence_as_of`, refreshed every five seconds. An admissible future clock yields zero elapsed minutes. An invalid clock leaves age unreported. LLM can stamp the signal from the candle snapshot. | The worker's false freshness flag or expired publication marks the card stale. Missing proof of freshness yields a partial read. Missing fields show “Not reported.” A rubric zero is a score. | A failed refresh labels any retained cards. Unqualified or stale rows stay visible for audit and lose the proposal badge. |
| `GET /api/decisions` | Slot order, `symbol`, `status`, `reasons`, `warnings` | The collection and its per-symbol assessments lack source timestamps. Browser receipt appears explicitly. | A current read can confirm the response shape. The age of the actual verdict remains unknown. Reported strings outside the public allowlist display “Unlisted code (hidden)” and count as present. Missing reason data stays unreported. Stale worker or failed refresh is shown. | Symbol history is shown under a separate heading with public reason codes. The UI excludes capital, raw diagnostics and private account details. |
| `GET /api/signals` | Limited journal window with `recorded_at`, `stage`, `symbol`, `reason`, `detail` | Each check carries its recorded time. The journal timestamp belongs to that check. | Missing clocks and absent reasons display “Not reported.” Unlisted reason strings display the localized hidden-code label. The journal window can contain historical market exclusions, passes and blocks for the same symbol. | Independent checks appear in the drawer and in Discovery. No timestamp is assigned to a verdict by matching symbols. |

All new reads use the shared authenticated EventSource and five-second REST fallback. The proposal label describes a BUY qualified at worker publication in OBSERVE with reported levels, a passing backtest, current universe membership and a usable assessment read. It is display-only. A known symbol veto suppresses the label.

## Known gaps

- The opportunity contract omits the configured `max_signal_age`. Between publications the UI can show the evidence age but cannot determine the exact instant a card expires. Exposing that setting requires separate backend approval.

- The exchange universe does not publish per-pair volume, spread, a complete rejection list or a per-pair freshness clock. Discovery shows available journal outcomes and leaves volume and spread unreported. Manual research figures keep their own source.
- Opportunity cards lack reliable provider/model identity, model completion time and a stable candidate ID. The displayed provenance is the card's source string. The worker signal timestamp can be inherited from the candle snapshot.
- Per-symbol AEGIS verdicts lack an evaluation timestamp or candidate ID. AEGIS time is “Not reported.” Journal records show reason and `recorded_at` together in their own rows.
- Browser QA uses a local OBSERVE snapshot and read-only fixtures. Physical-device and production broker validation are outside this run.

## Verification

| Check | Result |
| --- | --- |
| Base `b54883858e2225422c93f00cdd0c66841370338f` | 26 frontend and 148 Python tests, TypeScript, Vite, Ruff and four browser suites passed. 126 layouts, 0 horizontal overflow, 0 page errors. |
| `cd web && npm ci && npx tsc --noEmit && npx vite build && npm test` | 34 frontend tests passed. |
| `PYTHONPATH=src python -m unittest discover -s tests` | 148 tests passed with shell proxy variables unset. |
| `uvx ruff check .` | All checks passed. |
| `python web/qa/account_reads.py`, `telemetry.py`, `shell.py`, `room.py`, `opportunities.py` | All five suites passed against the built frontend in Chromium. The P4A suite checked worker ordering, publication-scoped freshness, advancing evidence age, hidden reason codes, score label, independent dated journal evidence, empty fields, freshness, navigation, read-only requests and bilingual layout. |
| `python web/qa/account_reads.py --layout /tmp/p4-layout` | 126 layouts, 0 with horizontal overflow, 0 page errors. |

Set `PYTHONPATH=src` and `CHROMIUM_PATH` for local browser checks. Run `web/qa/opportunities.py` with `WARP_QA_SCREENSHOTS` set to capture the labelled fixtures at 320, 390 and 1280 px in English and 320 px in Indonesian.
