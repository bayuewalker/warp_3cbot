# TradingDesk 2.0 post-merge baseline

**Decision on 22 November 2026:** P0 found operator-visible data-integrity defects. Review P0A before starting the dependent P1 telemetry adapter.

| Baseline item | Observation |
| --- | --- |
| Repository | Clean checkout of main at `0307f61807402bfab9dd9986d6671e496d720d48`, merged PR #18. The only open PR at audit start was #11, the independent shadow paper book. |
| Fly runtime | Public `GET /live` reported `mode=observe`, `execution_permitted=false`, `risk_permitted=false`. The deployed dashboard JS matched the clean build byte for byte, SHA-256 `7aea46d04fd17e8c9b8d6912cefbc4739de60f500c4e61c2a04b8d27d6f5bd07`. The Fly release number and backend image digest were unavailable. |
| Regression | 143 Python tests and 10 frontend policy tests passed. Clean npm install, TypeScript and Vite passed. Ruff flagged pre-existing I001 import ordering in `src/warp3c/__main__.py:248` and `tests/test_account_positions.py:322`. |
| Responsive | 126 Chromium checks across nine views, seven widths and two languages. All 18 checks at 320 px had a document width of 348 px. The other 108 fit. No uncaught page errors. |

## Read contracts inspected

| Authenticated route or stream event | Contract and failure behavior |
| --- | --- |
| `GET /api/accounts`, SSE `accounts` | Published fields are `accounts`, `selected`, `places_orders`, `as_of`, `error`, `sleeve`, `cadence_seconds` and `readonly`. Account rows carry profile identity, classification, selectability, balances and holdings. The pre-publication REST fallback has only accounts, selected and places_orders. A failed refresh may retain old rows and advance the **attempt** time. An empty list alone cannot confirm a successful read. |
| `GET /api/account-positions`, SSE `positions` | `positions`, `by_account`, `as_of`, `error`, `errors`, `stale`, `cadence_seconds`, `readonly` and optional `bots_checked`. REST can return an empty object before publication. The normalized unloaded book has no timestamp and carries an error. Partial reads can contain valid positions. A missing profile is grouped as `unknown` in the server count. |
| Position row | Identity, account, cabinet kind, owner, pair, side, paper flag, prices, residual size/cost, PnL, TP/SL and open time. Numeric financial fields are decimal strings or null. Mark and PnL carry their producer. Binance fallback requires a fresh mark. Derived USD PnL also requires residual size, side and a supported quote. |
| `GET /api/positions` | Legacy SmartTrade-only list with `positions`, `as_of` and `error`. Its scope differs from the combined cabinet and strategy book. A failed read can advance its attempt time. |
| `GET /api/stream` | One authenticated EventSource. Route-wide snapshots include a publication time. Specific account and position events carry the REST view. A heartbeat proves transport connectivity. It supplies no broker-read timestamp. Disconnected monitoring routes poll every five seconds. |

## Data-integrity findings

1. **False freshness and false zero.** A real credentialless OBSERVE worker produced a recent failed balance attempt. Chromium showed a green “Updated” badge. Account cards also defaulted an absent count to zero. Partial books and failed or missing snapshots could produce an exact zero or an empty-book message. The legacy Overview count had the same failed-read problem.
2. **Profile and ownership attribution.** The position projection accepted a WARP name prefix as cabinet ownership and selected the first profile from ambiguous lists. A cached or expired account inventory could still determine the displayed grouping. Cabinet and SmartTrade IDs could also differ when both identifiers were present.
3. **Incomplete evidence.** Negative exit totals inflated residual values. Invalid cabinet rows could disappear without a partial flag, repeated rows could count twice, and an empty retained strategy list could acquire a new timestamp after a failed refresh.

PR #18's existing fixtures passed for absent/non-numeric exit size and cost, valid partial-sale arithmetic, independently reported PnL including zero, the 40-read cap, profile/strategy pagination rejection, retention of populated stale strategies, and account/position event publication. These results use local fixtures. Authenticated production broker payloads and the Fly control plane were outside the audit's available evidence.

[Corrective PR test evidence and screenshots](docs/dashboard/TRADINGDESK_2_QA.md)
