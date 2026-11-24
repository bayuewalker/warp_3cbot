# TradingDesk 2.0 · P4B Accounts, capital, bots and accurate positions

Settings & risk → Accounts shows the read-only account and position book from #18/#19. Every figure is a reported value, or a value derived by a documented method and labelled **Derived**. Anything the broker or worker did not report reads **Not reported**. It is never a fabricated zero or a full-size assumption. A confirmed zero stays visible. Nothing here adds an endpoint, a write call or a permission.

The view models live in `web/src/tradingdesk/accounts-model.ts`. They use the P1 `observe()` reducer, the P1 clock-skew rule (5 s) and the P1 `payloadIssue` gate that `useLoad` already applies. Each value carries a state (`confirmed`, `confirmed_empty`, `partial`, `stale`, `loading`, `unauthorized`, `not_reported`), a source (`3commas`, `binance`, `computed`, `worker`), the source's own clock and the browser receipt.

## Data lineage

| Field | Route | Source | Null, stale or partial handling |
| --- | --- | --- | --- |
| Account name, DEMO/REAL, status, market, quote | `/api/accounts` `accounts[]` | 3Commas profile | DEMO/REAL is shown only when `demo` (boolean) and `badge` agree. A conflict or a non-boolean `demo` renders Not reported with the danger tone, never a guess. A missing `badge` falls back to `demo`, as on main. |
| Total, free, in use | `/api/accounts` `total`, `free`, `used` | 3Commas, `as_of` | Must match `^-?\d+(\.\d+)?$` and be non-negative. Negative, exponent, `NaN` or non-string values are rejected as Not reported and listed under the account. A top-level `error` marks every account stale and drops the balance clock, because failed attempts are time-stamped too. A per-account `error` makes only that account stale. |
| Holdings and USD value | `/api/accounts` `assets[]` (`total`, `usd`, `usd_reason`, `quote_age_seconds`) | USDT, USDC and USD are the reported balance. Other coins are a reported balance times a Binance mark, labelled Derived. | `usd` null shows `usd_reason`. `quote_age_seconds` is the mark age. Above 30 s (`QUOTE_FRESH_SECONDS`, `/api/prices.limit_seconds`) it raises Mark stale. A derived value with no age reads "mark age not reported". |
| Budget WARP can use | `/api/accounts` `sleeve.eligible` | Worker computation over 3Commas balances | Applies to the selected account only. Other accounts read Not reported with the reason "only for the selected account". Hidden when the account list is stale. |
| Configured USDT sleeve, share of account | `/api/warp-bots` `budget.sleeve_usdt`, `sleeve_pct` | Worker configuration | No source clock; the card says so. `sleeve_pct` must be within 0..1. |
| Used by WARP, remaining, max WARP bots | `/api/warp-bots` `budget.used`, `remaining`, `max_bots` | none on `46c86eb` | **Absent from the real payload** (see gaps). Rendered Not reported with "not published by the worker". |
| Open WARP bots | `/api/warp-bots` `bots[]` | Derived: count of this account's bots with status `pending`, `unknown`, `confirmed`, `active` or `paused` (the server's `OPEN_BOT` set) | A validated empty list is a confirmed zero. A malformed status makes the count Not reported. |
| Position rows | `/api/account-positions` `positions[]` | 3Commas cabinet and SmartTrade reads | See the next three rows. |
| Average entry, open size, cost, stop, take profits, safety orders, opened | row fields | 3Commas, book `as_of` | Prices must be positive decimals, size and cost non-negative. A rejected field reads Not reported and the row becomes partial. Null size or cost (partial fill, partial sell, missing exit total) reads Not reported. No other field fills the gap. |
| Sold size | none | none | The contract has no sold or remaining-size field. The card always says Not reported, "not in the broker contract". |
| Mark price | row `current_price`, `current_price_source` | 3Commas `lastPrice` or the Binance tape | Shown only with a valid source. A price without a source is rejected. A missing mark raises Mark stale ("no mark source"). A Binance mark has no source time, so the label says so instead of borrowing the book clock. |
| PnL USD and % | row `pnl_*`, `pnl_*_source` | 3Commas, or Computed from the mark | Computed is accepted only on top of a reported mark. A computed claim without a mark, or any PnL without a recognised source, is rejected. Labels differ: "3Commas" and "Derived (computed)". |
| Owner, kind, side | row fields | 3Commas plus the journal ownership match | A missing owner is never assumed to be WARP. External rows show "Read-only · external". |
| Counts in the picker | `by_account` and the rows | Producer tally | Exact only for a complete, in-date book and a current account list. If `by_account` disagrees with its own rows the count is Not reported. |

## Stale, partial and mark conditions

| Badge | Raised when | Retained data |
| --- | --- | --- |
| **Broker book stale** | `payload.stale`, a missing or unusable `as_of`, `as_of` older than 2.5 × `cadence_seconds`, or a failed refresh with retained rows. A book that is current but incomplete is not stale. | All rows stay listed. Retained SmartTrade rows keep "Last good read". |
| **Broker book partial** | Any entry in `errors` (including `grid <code>: incomplete position rows`), a missing `errors` array, or a payload `error`, on a book that may be perfectly current. Independent of stale: a book can be either, both or neither. | All rows stay listed. Counts are withheld while the book is partial. |
| **Account stale** | The accounts read `error`, an `as_of` older than 3 × `cadence_seconds`, a missing or unusable `as_of`, a failed refresh, or a profile with its own `error`. | Balances stay as retained values with the stale state. Positions are never hidden. |
| **Mark stale** | `quote_age_seconds` above 30 s on a holding, or a position with no mark source. | Value reads Not reported. PnL source is never Computed. |

Positions keep the main-branch attribution rule. If the accounts read is not current, every row sits under **Account not reported** (Unassigned), which stays visible. A failed accounts read cannot hide loaded positions.

## Incomplete grid rows (production observation)

Production on `46c86eb` reported 24 positions, `stale=false` with a current `as_of`, `bots_checked=38` and five errors of the form `grid <code>: incomplete position rows`. `src/warp3c/book.py` has not changed since P0A, so this is how it already behaves. A throwaway fixture run against `collect()` (uncommitted, fake broker) showed:

| Case | Rows produced | Error | Fields |
| --- | --- | --- | --- |
| Grid bot whose `unrealizedPnl.cnt` is 1 but whose list has 3 rows | 3 | `grid G1: incomplete position rows` | size, cost, PnL `None` |
| Two rows with the same id | 1 (repeat skipped) | `grid G2: incomplete position rows` | |
| Two rows with no id | 2 (cannot be de-duplicated) | `grid G3: incomplete position rows` | |
| Entry total without an exit total | 1 | none | size, cost, PnL `None` |

So grid bots do produce rows, and the error means "the bot's `cnt` disagrees with its list, or an identity is missing or repeated". It does not mean rows were dropped. Without a sanitized payload I cannot tell whether those 24 rows are legitimate separate grid positions with a `cnt` that counts something else, or sub-rows. Nothing in `book.py` is a verified regression, so it is unchanged. It is documented as a contract gap.

P4B handles the case in the UI so that nothing is fabricated or double counted:

- Each `incomplete position rows` error is parsed, and the rows of that bot (same kind and `bot_code`) render as **Incomplete rows** and partial. Their fields are shown as reported, with Not reported where null.
- The per-error list is always visible under "Some reads failed", each entry classified (incomplete rows, read cap, SmartTrade, list failed, read failed) with the producer's text unchanged.
- The same broker id appearing twice is collapsed once and noted. Rows without an id are kept, flagged partial and counted as "rows reported", never as positions.
- A book like that shows **Broker book partial** and never **Broker book stale** (audit finding F25-1). Counts in the picker are withheld (Unavailable) whenever the book is incomplete. The bots list says "N rows reported".

## Bots

`WarpBotsPanel` remains the only place with actions. The `allowed()` gating (paper mode, demo spot account, owned WARP bot, `external_code`, product, status, HALT for resume) and the typed confirmations (`PAUSE`, `RESUME`, `CLOSE`, `DELETE`) are unchanged. The action grid and confirmation box are now rendered only in paper mode. In OBSERVE or any other mode the panel says actions need paper mode. External and non-WARP bots appear only as read-only position rows and in the "Bots reporting positions" list, labelled "Read-only · external", with no control.

## Contract gaps

1. `/api/warp-bots` `budget` publishes configuration only (`operator.py:498-509`: `sleeve_usdt`, `sleeve_pct`, ceilings, `max_deals`, risk limits). `used`, `remaining`, `open_bots` and `max_bots` are absent. `accounts.budget_for()` has no caller. The budget form's prefill from `absolute`, `fraction` and `max_bots` therefore never populates on main either. Open bots is Derived client-side. Max bots, used and remaining stay Not reported until a backend change is approved.
2. No per-position mark time. 3Commas `lastPrice` has no timestamp in the book and Binance marks carry none. Mark age exists only for holdings (`quote_age_seconds`, and only when a USD value could not be priced). Computed PnL has no time of its own.
3. No sold or remaining size. `size` and `cost` are entry minus exit only when both totals parse, otherwise null.
4. No per-row incomplete flag. Attribution to rows relies on the error text `"<kind> <bot code>: incomplete position rows"`.
5. Rows without a broker id cannot be de-duplicated server-side or client-side.
6. The meaning of grid `unrealizedPnl.cnt` against the `/positions` list is unverified. A sanitized sample of one affected grid bot's list row `unrealizedPnl` and `/positions` rows would settle whether the check in `collect()` over-reports.
7. Rows with no account profile are tallied under `by_account["unknown"]`.
8. P1 `payloadIssue` fails the whole `/api/account-positions` read when any row is not an object or lacks a `take_profits` array. The model also tolerates malformed rows as partial, but in the running app the stricter gate wins.
9. `/api/positions` (the legacy SmartTrade list) is not repeated in the accounts panel. The account book supersedes it, Overview and Assets → Positions already show it, and reading it here changes the first-load ordering that the existing P0A browser test relies on.
10. Known and untouched: `POST /api/warp-bots/action` has no server-side mode gate. The client hides the controls outside paper mode, but that is not a server guarantee.

`AGENTS.md` and `COMMANDER.md` are not present in the repository at `46c86eb`; the constraints in the lane brief were used instead.

## Verification

| Check | Result |
| --- | --- |
| `uvx ruff check .` | `uvx` is not installed on this VM. `ruff 0.17.6` (`ruff check .`) reported all checks passed. |
| `PYTHONPATH=src python -m unittest discover -s tests` | 148 tests passed, unchanged. |
| `cd web && npm ci && npx tsc --noEmit && npx vite build && npm test` | Types and build clean. 65 JS tests passed (36 before, 29 in `accounts.test.ts`). |
| `web/qa` `account_reads.py`, `telemetry.py`, `shell.py`, `room.py`, `opportunities.py`, `control_room.py`, `accounts.py` | All seven passed. No existing QA file was edited. |
| `python web/qa/account_reads.py --layout DIR` | 140 layouts, 0 horizontal overflow, 0 page errors. |
| `web/qa/accounts.py` | Route-fulfilled fixtures only. Checked DEMO/REAL badges, account switching, Unassigned with `/api/accounts` 500, incomplete grid rows (stale=false, current `as_of`, five errors: partial badge shown, stale badge absent, EN and ID), the stale, partial, account and mark conditions, OBSERVE action hiding, external bot without controls, no non-GET requests, EN and ID at 320, 390 and 1280 px with no overflow. |

Bundle versus `46c86eb` (Vite production build):

| Asset | Base | P4B | Delta |
| --- | --- | --- | --- |
| JS | 317.66 kB, gzip 99.93 kB | 335.75 kB, gzip 105.45 kB | +18.09 kB, gzip +5.52 kB |
| CSS | 57.02 kB, gzip 13.02 kB | 59.35 kB, gzip 13.35 kB | +2.33 kB, gzip +0.33 kB |

Set `PYTHONPATH=src` and `CHROMIUM_PATH` for local browser checks. Run `web/qa/accounts.py` with `WARP_QA_SCREENSHOTS` set to capture `P4B-320-EN`, `P4B-390-EN`, `P4B-1280-EN` and `P4B-320-ID`. The script writes PNG; the committed files under `docs/dashboard/screenshots/p4b/` are the same captures converted to WebP with `ffmpeg -c:v libwebp -quality 78`. Each is a full-page capture of a labelled QA fixture, not production telemetry.
