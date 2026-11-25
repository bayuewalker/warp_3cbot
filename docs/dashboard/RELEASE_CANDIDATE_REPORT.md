# TradingDesk 2.0 release candidate report (P5)

**Owner approval required: release candidate approval is separate from merge/deploy.** This report is evidence, not a decision. Nothing was merged, deployed or run against production or staging.

Every figure below is what this lane ran and observed on one VM. It is unverified until it is re-run on a fresh checkout of the exact head SHA.

## Verdict

**READY_FOR_REVIEW for the OBSERVE-mode release candidate, with one release blocker for any PAPER or LIVE enablement** (P0, below). The AC-01..AC-14 table cannot be completed from this repository: the AC definitions were not supplied (see [Acceptance criteria](#acceptance-criteria-ac-01ac-14)).

## What was tested

| Item | Value |
| --- | --- |
| Product base (P4C, PR #26) | `91d3c38f7581d6bf3954d01c0f9f895b5d0e8c75` |
| Tested SHA | `a60d319a10e908877152a63c836770268362c680`. Every command in the table below was run on this commit (the final full run, with all 8 existing and 5 new browser suites). An earlier full run on `6ce9012`, which differs only in the documents, the document guard tests and one reordered assertion, gave the same results. |
| Head SHA | The head is one commit above the tested SHA and differs from it only in this document (the tested-SHA line). The head SHA is in the PR description. |
| Product source | Unchanged. `git diff origin/main --stat -- src web/src fly.toml Dockerfile .github` is empty. A build from the base and a build from this branch contain the same files, and screenshots of the two are pixel-identical. |
| Environment | Linux VM, Python 3.12, Node 22.23, Chrome 157 via Playwright (`CHROMIUM_PATH=/usr/bin/google-chrome`), ruff 0.17.6. GitHub Actions is billing-locked, so a red CI is not a code signal. |
| Date of runs | Nov 25, 2026 (UTC). |
| Merge check | `git merge-tree --write-tree origin/main HEAD` produced a tree with no conflicts. |

## Commands and results

Baseline on `91d3c38` (before any P5 change) is the owner-verified table in the brief. Reproduced here: unittest 148 OK, web tests 86/86, bundle 352.93 kB, 8 browser suites pass, layout audit 168/0/0.

| Command | Result on the tested commit |
| --- | --- |
| `PYTHONPATH=src python3 -m unittest discover -s tests` | `Ran 195 tests`, `OK (expected failures=2)`. 148 existing + 47 new. The 2 expected failures are the known gaps below. |
| `python3 -m ruff check .` and `python3 -m ruff check src tests` | All checks passed. No pre-existing findings on the base. Thirteen findings (import order, and `zip()` without `strict=`) appeared in the new files while they were written and were fixed in those files only. No existing file was touched by `--fix`. |
| `cd web && npm ci && npx tsc --noEmit` | Pass. |
| `cd web && npm run build` | Pass. Index JS 352.93 kB (gzip 111.21 kB), Activity chunk 7.92 kB (2.98 kB), CSS 63.30 kB (13.97 kB), `control-room.webp` 259.75 kB. All equal to the base. |
| `cd web && npm test` (`node --test *.test.ts`) | 92 tests, 92 pass, 0 fail. 86 existing + 6 new (`p5-contrast.test.ts`). |
| `python3 web/qa/account_reads.py` | Browser regressions passed. |
| `python3 web/qa/telemetry.py` | Browser regressions passed. |
| `python3 web/qa/shell.py` | Browser regressions passed. |
| `python3 web/qa/room.py` | Browser regressions passed. |
| `python3 web/qa/opportunities.py` | Browser regressions passed. |
| `python3 web/qa/control_room.py` | Browser regressions passed. |
| `python3 web/qa/accounts.py` | Browser regressions passed. |
| `python3 web/qa/activity.py` | Browser regressions passed. |
| `python3 web/qa/account_reads.py --layout DIR` | **168 layouts, 0 horizontal overflow, 0 page errors.** Unchanged. |
| `python3 web/qa/p5_layout.py` | **176 new layouts, 0 overflow, 0 page errors.** Gate total 168 + 176 = 344. |
| `python3 web/qa/p5_sse.py` | 7 cases pass. |
| `python3 web/qa/p5_a11y.py` | 0 findings over 192 view loads. |
| `python3 web/qa/p5_flows.py` | Pass. State-changing requests seen: `/api/login`, `/api/logout`, `/api/pause`, `/api/resume`, `/api/bots/preview`, `/api/bots/template`. |
| `python3 web/qa/p5_perf.py` | Pass. Numbers are in [TRADINGDESK_P5.md](TRADINGDESK_P5.md). |
| `python3 scripts/qa/bundle_report.py --expect-index-kb 352.93` | First-load index JS 352.93 kB (Vite count): match. |
| `python3 scripts/qa/secret_scan.py` | 0 unexplained findings, see Security. |
| `python3 scripts/qa/diff_review.py` | Exit 0: write surface identical at both ends, see Security. |
| `scripts/qa/run_suites.sh` | Runs everything above in one pass. |

The whole set was run twice on separate occasions. One existing unit test failed in the first run for a clock reason, described next.

### Pre-existing test fault: a time-of-day failure (F-P5-7)

`tests/test_failures.py:149` (`test_daily_loss_and_halt_block_entries`) failed in the first full run, which started at 00:02 UTC, and passed when re-run at 00:15 UTC. The file is unchanged by this branch, so this predates it. The test builds an equity mark at "now minus 5 minutes". `circuit_reason` (`src/warp3c/risk.py:234`) only counts marks from the current UTC date, so in the first five minutes of each UTC day the mark belongs to yesterday and the function returns `None` instead of `daily_loss`.

Reproduction with a frozen clock (the check script is not committed):

```text
00:02 UTC -> FAIL (assertEqual(reason, "daily_loss"), None != 'daily_loss')
00:04 UTC -> FAIL
00:06 UTC -> pass
```

Severity: low, and test-only. This is a flaky test, not a product defect: the product behaviour at midnight is the documented one. It was not fixed, because existing tests may not be edited in this lane.

## Acceptance criteria AC-01..AC-14

The AC-01..AC-14 definitions are not in the repository, in `docs/dashboard/*.md` (a search for `AC-0`, `AC-1` and `NON-GO` found nothing), or in the task text. This lane did not guess them. Marking an AC as met against a definition it has not seen would be a false claim, so every row is UNVERIFIED and the table is ready for the owner to fill in.

| AC | Definition | Status | Evidence |
| --- | --- | --- | --- |
| AC-01 | not supplied | UNVERIFIED (NON-GO set) | Candidate evidence by area below |
| AC-02 | not supplied | UNVERIFIED | |
| AC-03 | not supplied | UNVERIFIED (NON-GO set) | |
| AC-04 | not supplied | UNVERIFIED (NON-GO set) | |
| AC-05 | not supplied | UNVERIFIED | |
| AC-06 | not supplied | UNVERIFIED | |
| AC-07 | not supplied | UNVERIFIED (NON-GO set) | |
| AC-08 | not supplied | UNVERIFIED (NON-GO set) | |
| AC-09 | not supplied | UNVERIFIED (NON-GO set) | |
| AC-10 | not supplied | UNVERIFIED | |
| AC-11 | not supplied | UNVERIFIED | |
| AC-12 | not supplied | UNVERIFIED | |
| AC-13 | not supplied | UNVERIFIED (NON-GO set) | |
| AC-14 | not supplied | UNVERIFIED | |

**NON-GO set (AC-01, 03, 04, 07, 08, 09, 13): UNVERIFIED.** None of the seven can be called met or not met. If any is a gate for release, treat the release candidate as not yet approvable until the owner maps it to the evidence below.

Evidence available, by area, for the owner to map to the ACs. Links are to this branch.

| Area | Result | Evidence |
| --- | --- | --- |
| Data contracts (12 rows) | PASS with mocked brokers. Live 3Commas shapes UNVERIFIED | [matrix in TRADINGDESK_2_QA.md](TRADINGDESK_2_QA.md#p5-release-gate-matrix) |
| SSE and transport (10 cases) | PASS | [TRADINGDESK_P5.md](TRADINGDESK_P5.md#sse-and-transport-matrix), [disconnected](screenshots/p5/p5-sse-disconnected-390-en.webp), [auth loss](screenshots/p5/p5-sse-auth-loss-390-en.webp), [stale producer](screenshots/p5/p5-sse-stale-producer-390-en.webp) |
| Responsive, 344 layouts | PASS with 2 known low findings | [Pixel-class](screenshots/p5/p5-pixel-412x915-dpr2.625-touch-overview-en.webp), [400% reflow](screenshots/p5/p5-zoom-400pct-320-overview-en.webp), [art blocked](screenshots/p5/p5-no-room-art-390-desk-en.webp) |
| Accessibility | PASS, 0 findings | [populated desk, ID](screenshots/p5/p5-a11y-populated-1440-id.webp) |
| Flows | PASS | [RESUME awaiting](screenshots/p5/p5-flow-resume-awaiting-390-en.webp), [OBSERVE chip](screenshots/p5/p5-flow-observe-chip-390-en.webp), [accounts](screenshots/p5/p5-flow-accounts-390-en.webp), [signals](screenshots/p5/p5-flow-signals-390-en.webp) |
| Performance | Reported, no gate on timings | [TRADINGDESK_P5.md](TRADINGDESK_P5.md#performance-emulated-low-end-android) |
| Read-only boundary | PASS | [Security](#security-and-diff-review) |
| Rollback | Documented, not executed | [ROLLBACK.md](ROLLBACK.md) |

## Release blocker for PAPER or LIVE: P0

**Status: fixed in the `fix/warp-bot-action-server-gate` PR, pending SENTINEL.** The server now applies the paper-mode and demo-spot gate before any broker call; see [BOT_ACTION_GATE.md](../security/BOT_ACTION_GATE.md). The text below describes the finding as reported against `cbd9e71`.

**P0. `POST /api/warp-bots/action` has no server-side mode or account gate.** `src/warp3c/httpapi.py:118-119` routes it to `_bot_action` (`httpapi.py:382`). Only the UI limits it to paper mode on a demo spot account. A signed-in caller can reach the broker write path for a journaled WARP bot while the worker is in OBSERVE.

- **Reproduce:** `PYTHONPATH=src python3 -m unittest tests.test_p5_release_contracts.KnownGapTests.test_P0_warp_bot_action_is_refused_by_the_server_outside_paper_mode` is an expected failure. To see the failure itself, remove the `@unittest.expectedFailure` marker on a scratch copy: `AssertionError: Lists differ: [('pause', 'owned')] != []`. A typed `PAUSE` confirmation for a journaled WARP bot reached `pause_dca_bot` on a recording broker stub, and the worker answered 200, with `TRADING_MODE=observe`. The same request is refused at the UI in OBSERVE.
- **Scope:** pre-existing, backend, not introduced by TradingDesk (the route and handler are unchanged across #19 to #26). In production today it needs an authenticated session and a WARP-owned bot in the journal, and the actions it exposes are pause, resume (refused only while the HALT file exists), close and delete, each behind a typed confirmation word, on a journaled WARP bot whose remote identity matches.
- **Severity:** blocker for any PAPER or LIVE enablement. Not a blocker for the OBSERVE candidate, because the UI does not offer the action in OBSERVE.
- **Not fixed here:** `src/warp3c/**` is out of scope. The expected failure flips to an unexpected success when the gate is added.

## New findings from P5

None of these were fixed. Each is a product or deployment fact.

| ID | Severity | What | Where | Reproduce |
| --- | --- | --- | --- | --- |
| F-P5-1 | Low | At 200% text size in Indonesian at 390 px, the status strip overflows by 6 px and its labels overlap. The bottom-nav labels break mid-word. | `web/src/tradingdesk/Shell.tsx` `StatusStrip`, `index.css` | `python3 web/qa/p5_layout.py`, then [screenshot](screenshots/p5/p5-text-200pct-390-overview-en.webp). Real Android font scaling is unverified. |
| F-P5-2 | Low | At 1024 px with 200% text, Settings and risk > Activity overflows (EN 1 px, ID 80 px). | Activity panel badge and notes | same |
| F-P5-3 | Medium (deployment) | The worker sends no compression. First load is 527,768 bytes. On 4x CPU with Slow 3G, FCP is 8.2 s and the first position list is 8.9 s. | `src/warp3c/httpapi.py` `_asset` (about line 140 to 152) sends bytes as stored | `python3 web/qa/p5_perf.py` (`first_load_transfer.compressed: false`). Gzip would take the index chunk from 353 kB to about 111 kB. |
| F-P5-4 | Low | `.webp` has no entry in `_TYPES`, so `control-room.webp` is served as `application/octet-stream`. Chrome still renders it. | `src/warp3c/httpapi.py:50-57` | `curl -sI` against a local worker for the webp asset |
| F-P5-5 | Medium | Logout clears the cookie in the browser only. The session is a signed, stateless token valid for 12 h, so a copied cookie stays valid after logout until it expires or `WARP_SESSION_SECRET` changes. | `src/warp3c/httpapi.py:91-92`, `src/warp3c/auth.py:43-50` | Expected failure `KnownGapTests.test_logout_revokes_the_session_token_server_side` |
| F-P5-6 | Low | `/health`, `/live` and `/ready` are unauthenticated and return the worker's health map, which includes the mode, permission flags and the HALT file path (`/data/HALT`). No credential or token is in it (the contract test asserts that for every authenticated route and the secret scan covers the bundle). | `src/warp3c/httpapi.py:75`, `src/warp3c/__main__.py:222-225` | `curl` a local worker |
| F-P5-7 | Low (test only) | Time-of-day failure of an existing test in the first five minutes of the UTC day. See above. | `tests/test_failures.py:149` | frozen-clock run above Status: still open. Pinning `now` alone is not enough: `GlobalRisk.assess` also reads the wall clock and rejects the account snapshot as stale, so the fix is more than a test-clock pin and was left out of the P0 gate PR. |

## Open follow-ups carried from earlier phases (not fixed here)

| ID | Status |
| --- | --- |
| F24-4 | The mobile station list sits below the room scene at 390 px. Open. |
| F24-5 | `DeskResearch` (`monitor.tsx` about 347-376) does not use `opportunities()`. EN shows the thesis, ID shows `why_id`. Open. |
| P4B P2 | A book `error` with retained rows reads partial, not stale, for up to about 90 s. Open. |
| F26-3 | Journal `created_at` has no zone, so submissions always show as untimed. Open, documented in the Activity timeline. |
| F26-4 | The resume gate could accept a pre-ack snapshot within 5 s of browser-ahead clock skew. It is guarded by the `published_at` change check. Open. |
| P4C bundle | Total JS growth across all chunks is +24.78 kB (first-load +16.86 kB), accepted by the owner. Unchanged in P5. |
| Grid "incomplete position rows" | The cause in `book.py` `collect()` is unverified. New tests prove the mechanism (a count mismatch raises the flag) but not why production grid bots report it. A read-only capture of a live grid response is needed. |
| Nov 23 volume loss | No journal data between snapshot `vs_40kv0xollvAtz5lVmV5` (Nov 23, 14:10 WIB) and the host loss. The old volume `vol_42k13q9n9g9jo294` is kept. See the worked example in [ROLLBACK.md](ROLLBACK.md). |

## Unverified external conditions

| Condition | Why it is unverified |
| --- | --- |
| Live 3Commas v2 response shapes | No call to 3Commas was made. Every broker test uses a local mock. A passing mock is not proof of the real contract, and no row is marked "verified" because of one. |
| Staging smoke | Out of scope unless the owner authorizes it. No staging host was contacted. |
| Production behaviour of this branch | Nothing was run against `warp3c-bayue.fly.dev`. Production facts quoted in the brief (v21, observe, three accounts) were not re-checked. |
| Physical Android devices and real font scaling | Emulation only: Pixel-class and Galaxy-class viewports with touch and mobile UA, 4x CPU, software raster. |
| Fly release ID of the candidate | No `flyctl` command was run. |
| Rollback and restore procedures | Written, not executed. The `flyctl` flags in `ROLLBACK.md` were not tried. |
| AC-01..AC-14 | Definitions not supplied. |

## Security and diff review

### Secret scan

Method: `python3 scripts/qa/secret_scan.py`. It reads every file under `web/dist`, `docs/dashboard/screenshots`, `docs`, `web/qa`, `web/src`, `tests` and `scripts` as bytes (so the metadata of images is scanned as well). It looks for the credential variable names `WARP_OPERATOR_TOKEN`, `WARP_SESSION_SECRET`, the live-execution acknowledgement variable and the 3Commas key names, the words `API_KEY`, `API_SECRET` and `SECRET`, `Bearer <token>` strings, JWTs, vendor key prefixes (`sk-`, `ghp_`, `github_pat_`, `AKIA`, `xox`, `nvapi-`), private key headers, `token|secret|password|api_key` assignments to long literals, and `warp_session=` cookie values. Artifact folders get every detector. Source folders get only the value-shaped detectors, because source may name a variable. `--self-test` proves the detectors fire on synthetic values.

Result: 231 files scanned, 10 findings, **0 unexplained**.

| Finding | Where | Why it is not a secret |
| --- | --- | --- |
| 4 name mentions | `web/dist/assets/index-*.js` | Login and mode help text from `web/src/i18n.ts` (lines 178, 271, 722, 815) naming `WARP_DASHBOARD_PASSWORD`, `WARP_OPERATOR_TOKEN` and the live-execution acknowledgement variable. Names only. No value anywhere. |
| 3 bearer-header fixtures (`operator-token-16`) | `tests/test_phase1.py:347`, `tests/test_phase2.py:137, 252` | Unit-test constant that exists only inside the test process. Predates P5. |
| 3 password literals | `tests/test_phase1.py:319, 392, 468` | Unit-test login password for an in-process server. Predates P5. |

The 21 committed P5 screenshots (17 suite captures and the 4 `before`/`after` images) were scanned with every detector: 0 findings. Screenshots are taken only after sign-in, never of the login form. There are no committed logs. The in-process test credentials are random per run and are not written anywhere.

Separately, `tests/test_p5_release_contracts.py::NoSecretInPayloadsTests` builds a worker with sentinel key, secret and operator-token values and asserts none appears in any published route or in the in-memory snapshot.

### Diff of the TradingDesk PR range

Range: `0307f61` (#18) to `91d3c38` (#26). Eight PRs (#19 to #26). Command: `python3 scripts/qa/diff_review.py` (use `--show-diff` for the full text).

| Reviewed file | Changed? | What and why |
| --- | --- | --- |
| `src/warp3c/engine.py` | Yes, in #19 only: +3 −1 | Imports `sqlite3`. `refresh_position_book` passes `owned_bots=self.journal.warp_bots()` to the read-only position book so cabinet bots are attributed to WARP by journal identity instead of by name prefix. It also catches `sqlite3.Error` next to `IntegrationError`, so a locked journal keeps the last book and records the error instead of raising. No call to the broker is added. |
| `src/warp3c/risk.py` | No | |
| `src/warp3c/threecommas.py` | No | |
| `src/warp3c/config.py` | No | |
| `src/warp3c/httpapi.py` | No | |
| `fly.toml` | No | |
| `Dockerfile`, `.github/**` | No | |

Other `src/` files changed in the range, outside the files named in the brief: `src/warp3c/book.py` (#19, the read-only position projection: ownership by journal identity, ambiguous profile unassigned, independent null size and cost, partial and stale flags), `src/warp3c/__main__.py` (#19, import tidy), `src/warp3c/operator.py` (#24, `_card_fresh` becomes `_card_expires_at` and published cards gain an `expires_at` field). All are read-only presentation of data the worker already holds.

### No new order path

Compared at the base and at the head by parsing the code, not by searching text:

| Surface | At `0307f61` | At `91d3c38` |
| --- | --- | --- |
| Broker write call sites in `threecommas.py` (`request` or `_bot_write` with POST, PUT, PATCH or DELETE) | 11 | 11, identical (strategy trade, DCA and grid create, DCA pause, close-all, delete, grid stop, restart, delete) |
| Worker POST routes in `httpapi.py` | 12 | 12, identical (the documented twelve) |
| Browser write call sites in `web/src` | 11 | 11, identical. `fetch()` appears only in `api.ts`. |
| Browser `/api/*` targets | 27 | 32. Added: `/api/alerts`, `/api/breakers`, `/api/journal`, `/api/risk`, `/api/signals`. All five are existing GET routes in the worker's `ROUTES` tuple. No write target was added. |

These are pinned by `tests/test_p5_release_contracts.py::ReadOnlyBoundaryTests`, which fails if a POST route, a PUT, PATCH or DELETE handler, a broker write call, a browser write site or an `/api/*` literal outside the documented set appears. It also asserts that every POST except login and logout answers 401 without a session, that the worker answers PUT, PATCH and DELETE with an error even when signed in, that non-WARP bot names cannot become owned, and that the only external host in the bundle source is `www.tradingview.com`.

The broker client does contain write methods (create and manage WARP bots, SmartTrade create). They predate TradingDesk, they are unchanged, and in OBSERVE the engine does not call them. The P0 above is the one path where a signed-in caller can reach them without a mode gate.

## Hard rules, checked

| Rule | Status |
| --- | --- |
| `TRADING_MODE` stays observe, no value of the live-execution acknowledgement read or set, no `fly.toml`, Dockerfile, secrets or env change | Held. Only `docs/`, `tests/`, `web/qa/`, `scripts/qa/` and `web/p5-contrast.test.ts` changed. |
| No new non-GET call | Held. See the table above. |
| No fake data in production paths | Held. Fixtures are in `tests/**` and `web/qa/**`. The secret scan and the bundle report run on the built output. |
| TradingView manual only | Held. No TradingView or MCP data in any test. |
| Non-WARP bots untouched | Held. External rows are asserted view-only in the flows suite and unowned in the unit tests. |
| 5-item mobile nav and 6-item rail | Held. Asserted in the flows suite. |
| No deploy, no merge, no push to main, nothing against production or staging | Held. The request recorder fails any browser request that leaves loopback. |
| No Fly volume or machine manipulation | Held. Rollback is documented only, and `ROLLBACK.md` contains no command that removes a volume. |
| No secret, token, cookie or password printed, logged, committed or screenshotted | Held. Secret scan above. |

## Decision requested

1. Approve or reject the OBSERVE-mode release candidate. Approval is separate from merge and from deploy.
2. Supply the AC-01..AC-14 definitions, or map the evidence table above to them, so the NON-GO set can be marked.
3. Schedule the P0 gate on `/api/warp-bots/action` before any PAPER or LIVE enablement.
4. Decide whether F-P5-3 (no compression) and F-P5-5 (logout does not revoke) should be fixed before the candidate goes out.
