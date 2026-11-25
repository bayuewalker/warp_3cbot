# TradingDesk 2.0 P0A review packet

**Tested code** `abc5ac530593102cfb36aa41194fe3136209a8a8`. **Base** `0307f61807402bfab9dd9986d6671e496d720d48`. The evidence commit adds this packet, the baseline report and screenshots.

## Result

The Accounts panel displays an exact position count only for a current, complete read. Cached account-list failures, missing account identities and expired account maps leave the corresponding known rows reachable under **Account not reported** (Unassigned). Retained rows stay inspectable when only part of the position book succeeded. Balance read errors suppress the attempt time as evidence of successful freshness. The legacy SmartTrade count and empty view also require successful evidence.

Cabinet ownership uses the journal product, remote bot code, account and WARP identity. Ambiguous profiles stay unassigned. SmartTrade row identity prefers the producer's strategy ID. Missing or negative exit inputs preserve null independently for size and cost. Malformed, duplicate and count-mismatched cabinet reads mark the book partial. A failed strategy refresh retains the previous successful timestamp even for a previously empty list.

## QA results

| Check | Outcome on the branch |
| --- | --- |
| `python -m unittest discover -s tests -v` | 146 passed. Includes account and strategy parsing, stale retention, pagination, read caps, GET-only doubles, journal ownership, HALT, mode restrictions and authentication. |
| `python -m ruff check src tests web/qa`, `python -m compileall -q src tests` | Passed. Two pre-existing import-order findings were corrected. |
| `cd web && npm ci && npx tsc --noEmit && npm run build` | Passed with the locked dependencies. Vite JS 269.06 kB, 84.09 kB gzip. |
| `node --test web/*.test.ts` | 10 passed. Covers stream cache policy, fallback reads and logout state. |
| `python web/qa/account_reads.py` | Passed in headless Chromium. Real local session, fallback REST, dedicated SSE events and a clock advance past the broker TTL. Browser-only fixtures exercise pre-publication, confirmed zero, malformed date, partial/stale books, two profiles, missing parents, retained account errors, HTTP 429/503, timeout, retry and 401 session expiry. |
| `python web/qa/account_reads.py --layout /tmp/warp-p0a-captures` | 108/126 fit. Both languages showed 28 px document overflow at 320 px in all nine views, matching main. The runner exits 1 on overflow. Zero uncaught page errors. |
| `git diff --check` | Passed. |

The responsive run used a real local OBSERVE startup snapshot, an empty temporary journal, reduced motion and an Asia/Jakarta browser timezone. It signed in, toggled EN/ID and visited Overview, Opportunities, bot inventory, positions, bot preset, Screener, accounts, rules and configuration at 320, 360, 390, 768, 1024, 1440 and 1920 px. The fault fixtures live only in the test process. To reproduce, install Python Playwright and its Chromium browser, or set `CHROMIUM_PATH` to a local Chromium executable.

## Visual evidence

Both sides use real credentialless worker output. In the baseline capture, the Balances line is green beside a credentials error. On the branch it reads “Unavailable” with the cause.

| Viewport | Main | P0A |
| --- | --- | --- |
| 390 × 844 | [Mobile before](screenshots/p0a/before-390.png) | [Mobile after](screenshots/p0a/after-390.png) |
| 1440 × 960 | [Desktop before](screenshots/p0a/before-1440.png) | [Desktop after](screenshots/p0a/after-1440.png) |

## Safety and review boundary

No endpoint, broker operation or frontend execution control was added. The worker change supplies existing journal records to its read-only display projection. The entry point also has an import-order cleanup. Trading configuration, broker request code, risk gates, auth handlers and deployment files have no diff. The audit made public Fly GETs only. Browser traffic and fault injection ran on loopback. Live 3Commas reads, physical Android testing and a Fly release-ID check remain unverified.

**Review requested for P0A.** The existing 320 px layout defect belongs to P2's shell acceptance gate. P1's cross-route source contract, frame ordering and six-agent projections await P0A review.

---

## P5 release-gate matrix

Updated in P5 against base `91d3c38` (P4C). The sections above are the P0A packet and keep their original figures (146 tests, 108/126 layouts), which the later phases superseded. Current figures are in the commands table of [the release candidate report](RELEASE_CANDIDATE_REPORT.md). Verdict values: **PASS** (asserted by a passing test), **PASS (mock)** (asserted against a local mock only, so the live broker contract is still unverified), **KNOWN GAP** (asserted as an expected failure or recorded finding), **UNVERIFIED** (not tested here).

### Data contract rows

| Row | Tests that cover it | Verdict |
| --- | --- | --- |
| Cold start, no first snapshot | `tests/test_p5_release_contracts.py::ColdStartTests` (all 18 routes carry no source time, the book is `not_loaded`, unauthenticated reads refused); `test_account_positions::test_not_loaded_is_explicit`; `web/accounts.test.ts` "loading and unauthorized reads are their own states"; `web/tradingdesk.test.ts` "portfolio is loading while either broker read awaits its first response"; `web/qa/telemetry.py`, `p5_sse.py` (empty frame reads "Awaiting worker") | PASS |
| Confirmed zero | `ConfirmedZeroTests` (empty book has a time and no error, zero counts and PnL survive, a zero price is not a price); `accounts.test.ts` "a successful empty book is confirmed_empty", "a confirmed zero stays visible"; `tradingdesk.test.ts` "small forward clock skew permits confirmed empty reads"; `p5_sse.py` "Confirmed empty" | PASS |
| Missing balance | `accounts.test.ts` "negative or non-decimal balances are rejected as Not reported", "an accounts read error marks every account stale and drops the balance clock"; `tradingdesk.test.ts` "a valid position book cannot confirm failed balances"; `web/qa/accounts.py` | PASS (mock). Production shows one REAL account as "balance unavailable", which is the intended display. |
| Partial positions | `PartialPositionTests` (count mismatch in 3 shapes, a failed bot keeps the others, a row without an id is kept and flagged); `test_account_positions::test_incomplete_cabinet_rows_preserve_unique_known_positions`, `test_failed_reads_are_reported_and_others_kept`; `accounts.test.ts` incomplete grid rows | PASS (mock). Why production grid bots report "incomplete position rows" is UNVERIFIED. |
| Missing sold quantity or cost | `MissingSoldQuantityAndCostTests` (12 unusable inputs, each leaving only the matching value unreported; missing entry; USD PnL needs the broker summary); `test_account_positions::test_unknown_entry_or_exit_leaves_only_matching_value_unavailable`; `accounts.test.ts` "null size and cost ... read Not reported", "a missing sold amount leaves size unreported" | PASS |
| Stale strategy book | `StaleStrategyBookTests` (public view carries stale, error and the last good time; first failure has no time); `test_account_positions::test_retained_empty_strategy_book_keeps_last_good_timestamp`, `test_failed_strategy_read_keeps_the_old_timestamp_and_reports_the_error`; `accounts.test.ts` "a stale strategy feed raises the broker-stale condition and keeps every row" | PASS. KNOWN GAP P4B P2: a book error with retained rows reads partial, not stale, for up to about 90 s. |
| Accounts fail, Unassigned still visible | `UnassignedRowTests` (no or ambiguous account becomes unknown, `by_account` adds up to the rows); `accounts.test.ts` "Unassigned keeps every row when the accounts read fails"; `web/qa/accounts.py`, `p5_flows.py` (account switching) | PASS |
| Bad pagination | `BadPaginationTests` (20 malformed page sequences all fail closed: duplicate id, missing, empty and non-string ids, truncated page, changing total and page count, limit and page mismatch, over-long last page, zero-page list with items, page count not matching total, negative total, string and boolean metadata, non-object item, missing envelope; 8 HTTP error codes; one well-formed 2-page list still loads); `tests/test_integration.py` inconsistent pages, repeating cursor, empty page | PASS (mock) |
| Duplicate ids | `DuplicateIdTests` (same position twice in one bot counts once and flags the book, id-less rows never merge, the same id under two bots stays two rows, duplicate strategy ids refused by the client); `accounts.test.ts` "grid sub-rows with a repeated broker id are counted once" | PASS |
| Missing market mark | `MissingMarketMarkTests` (no symbol, a retained tape is not a mark, a present mark labels its source, a side is required, no USD PnL on a non-dollar quote); `test_account_positions::test_stale_prices_are_not_used`, `test_binance_price_fills_only_missing_values_and_is_labelled`; `accounts.test.ts` "a missing symbol mark is Not reported and PnL is never labelled computed" | PASS |
| Unknown submission | `UnknownSubmissionTests` (an unreconciled submission is published as `unknown`, blocks a repeat, stays unknown after a broker listing that omits it); `tests/test_core.py::test_unknown_order_blocks_until_reconciled`; `activity.test.ts` "a submission without strategy_id shows its client_id and invents nothing" | PASS. KNOWN GAP F26-3: journal `created_at` has no zone, so submissions show as untimed. |
| HALT | `HaltContractTests` (the HALT file wins over a healthy snapshot on REST and on the stream frame, removing the file does not reopen a snapshot taken under HALT, a missing path cannot clear a halted snapshot); `test_phase1`, `test_live_desk` HALT tests; `p5_sse.py` (HALT reads off, old frames cannot undo it); `p5_flows.py` (typed HALT and RESUME) | PASS |

### SSE and transport rows

| Row | Suite and case | Verdict |
| --- | --- | --- |
| Initial handshake | `p5_sse.py` `case_handshake` | PASS |
| Auth loss, 401 mid-session | `case_auth_loss` | PASS |
| Disconnect | `case_disconnect_reconnect` | PASS |
| Reconnect with backoff | `case_disconnect_reconnect` | PASS |
| Fallback polls | `case_disconnect_reconnect` | PASS |
| Old frames, frames beyond the TTL | `case_old_and_repeat_frames` | PASS |
| Repeat events | `case_old_and_repeat_frames` | PASS |
| Route error | `case_route_errors`, `case_http_route_errors` | PASS |
| Stale producer time | `case_stale_producer_with_heartbeats` | PASS |
| No green after a failure | every case above, with positive controls | PASS |

### Responsive, accessibility and flow rows

| Row | Suite | Verdict |
| --- | --- | --- |
| 320, 360, 390, 768, 1024, 1440, 1920 px, EN and ID | `account_reads.py --layout` (168 layouts) | PASS, 0 overflow, 0 page errors |
| Pixel-class Android (DPR 2.625, touch), Galaxy-class, desktop 1280 and 2560 | `p5_layout.py` (96 layouts) | PASS |
| 200% browser zoom, 400% reflow | `p5_layout.py` (48 layouts) | PASS |
| 200% text size | `p5_layout.py` text-only emulation (48 layouts, outside the gate) | KNOWN GAP F-P5-1, F-P5-2 (14 views); real Android font scaling UNVERIFIED |
| Room webp blocked, all room art blocked | `p5_layout.py` (32 layouts) | PASS |
| Reduced motion | `p5_layout.py`, `room.py` | PASS |
| Slow 3G | `p5_perf.py` | Reported: FCP 8.2 s with 4x CPU. KNOWN GAP F-P5-3 (no compression). |
| WCAG AA contrast, text tokens | `web/p5-contrast.test.ts` (every text and accent token on every surface, minimum 5.44:1) | PASS |
| WCAG AA contrast, rendered text | `p5_a11y.py` (19,931 text nodes) | PASS. 107 nodes on image fills are unknown. |
| Visible focus, keyboard-only, accessible names, 44 px targets | `p5_a11y.py`, `p5_flows.py` keyboard journey | PASS |
| Navigation, login and logout | `p5_flows.py` | PASS |
| Signals, account switching, view-only external bots | `p5_flows.py` | PASS |
| Preset save, risk preview | `p5_flows.py` | PASS (mock broker). Live 3Commas UNVERIFIED. |
| HALT and RESUME typed confirmation | `p5_flows.py` | PASS |
| RESUME awaiting fresh snapshot | `p5_flows.py`, `activity.test.ts` | PASS. KNOWN GAP F26-4 (5 s clock-skew window, guarded by the `published_at` change). |
| OBSERVE chip | `p5_flows.py`, `activity.test.ts` | PASS |

### Boundary and performance rows

| Row | Evidence | Verdict |
| --- | --- | --- |
| Twelve POST routes, no PUT, PATCH or DELETE | `ReadOnlyBoundaryTests` | PASS |
| Broker write inventory pinned (11 call sites) | `ReadOnlyBoundaryTests`, `scripts/qa/diff_review.py` | PASS |
| Browser write sites pinned (11), no `fetch()` outside `api.ts` | `ReadOnlyBoundaryTests` | PASS |
| `POST /api/warp-bots/action` refused outside paper mode | `tests/test_bot_action_gate.py` | Fixed in PR #29 (6181bc8), SENTINEL PASS, Fly v24 |
| Logout revokes the session token | `KnownGapTests::test_logout_revokes_...`, `tests/test_session_revocation.py` | Fixed in this PR (F-P5-5), pending SENTINEL |
| No secret in a published payload, the bundle or screenshots | `NoSecretInPayloadsTests`, `scripts/qa/secret_scan.py` | PASS |
| Room animation CPU, long tasks, SSE burst frame churn | `p5_perf.py` | PASS (60 fps, 0 long tasks in the burst) |
| Listener counts after 10 and 50 route switches | `p5_perf.py` | PASS (identical to baseline) |
| Time to first position list at 4x CPU | `p5_perf.py` | Reported: 292 to 350 ms median over two runs |
| Bundle weight | `scripts/qa/bundle_report.py` | PASS: 352.93 kB equals the base |
| Live 3Commas v2 shapes, staging, physical Android | none | UNVERIFIED |

