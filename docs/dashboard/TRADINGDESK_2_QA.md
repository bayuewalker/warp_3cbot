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
