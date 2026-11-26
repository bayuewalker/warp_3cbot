# Shared account mode (`WARP_SHARED_ACCOUNT`)

By default WARP assumes a dedicated 3Commas API profile: every coin in the wallet must be explained by a WARP strategy, any strategy on the profile that WARP did not create stops entries, and equity is the whole account. That is unchanged.

`WARP_SHARED_ACCOUNT=1` lets WARP trade a fixed sleeve inside a real spot account that other bots and manual buys also use. It is off unless set. It is not a safety relaxation for WARP's own money: it narrows what WARP looks at to WARP's own budget, and keeps WARP's own checks fail-closed.

## How to enable

```
WARP_SHARED_ACCOUNT=1        # accepts 0, 1, true, false. Anything else refuses to start.
WARP_SLEEVE_USDT=50          # required and > 0 in shared mode (startup error otherwise)
```

Also review before going live:

| Variable | Why it matters in shared mode |
| --- | --- |
| `WARP_MAX_DAILY_LOSS_FRACTION`, `WARP_MAX_DRAWDOWN_FRACTION` | Measured on sleeve equity in shared mode (defaults 0.05 and 0.15). The circuit uses the tighter of these and the tradebook limits below. |
| `WARP_TRADE_DAILY_LOSS`, `WARP_TRADE_DRAWDOWN` | Tradebook defaults 0.03 and 0.08. In both modes `circuit_reason` uses `min(WARP_MAX_DAILY_LOSS_FRACTION, WARP_TRADE_DAILY_LOSS)` and `min(WARP_MAX_DRAWDOWN_FRACTION, WARP_TRADE_DRAWDOWN)`. The looser number never wins. In shared mode the same limits are also passed into `capital.approve` from the persisted day-start and peak. |
| `WARP_MAX_PER_PAIR_FRACTION`, `WARP_MAX_GROUP_FRACTION`, `WARP_MAX_COMMITTED_FRACTION` | Applied to the sleeve, not the whole account. On a 50 USDT sleeve with `WARP_DCA_TOTAL_ORDERS=2`, `WARP_MAX_BASE_ORDER_USDT=15` and the default multiplier, two orders of 15 USDT plus the 0.1% fee are about 30.03 USDT. `0.61 × 50 = 30.5`, so those three fractions need to be `0.61` before a legacy base of 15 fits. The reserve stays `WARP_MIN_FREE_RESERVE_FRACTION=0.25` (12.5 USDT), so free USDT must still cover about 43. The defaults fail closed and this file does not change them. |
| `WARP_DENY_SYMBOLS` | Must list pairs held outside WARP. For the shared Binance spot wallet that is `BTCUSDT,ETHUSDT,ZECUSDT,XAUTUSDT,NIGHTUSDT`. |
| `WARP_LIVE_EXECUTION_ACK` | Unchanged. Live mode still needs it. |

Surfaces: `GET /api/status` and `GET /api/config` (authenticated) carry `shared_account: true|false`. The public `/health` does not carry the flag or any balance.

## What changes

| Check | Strict (default) | Shared |
| --- | --- | --- |
| Coin held that no WARP strategy explains | Halt: `Unexplained spot balance` | Ignored. Not read, not priced. |
| Wallet quantity below a WARP strategy's quantity | Halt | Halt (unchanged) |
| WARP strategy base asset absent from the wallet | Halt | Halt (unchanged) |
| Mark price for held coins | Required for every coin | Required only for WARP strategies' base assets |
| Strategy on the profile not created by WARP | Halt: `External strategy on dedicated account` | Skipped. Not validated, sized, counted or touched. |
| Strategy that looks like WARP (`createdBy=open_api`, `warp3c-` client id) but is not in the WARP journal as `confirmed` or `unknown` | n/a | Halt |
| USDT balance row missing, or free USDT differs from the wallet row | Existing checks | Halt |
| Account snapshot stale or without a timezone | Halt | Halt (unchanged) |
| 3Commas bot list (other bots' reserved safety orders) unreadable | Treated as zero reserve | Halt |
| Free USDT below the part of the sleeve not yet invested | n/a | No entries, reason `insufficient_free_usdt` |
| Equity used for sizing, daily loss, drawdown | Whole account value | `WARP_SLEEVE_USDT` + realized P&L since `shared_account_since` + open P&L on WARP strategies |
| Closed WARP strategy with no readable realized P&L | Not required | Halt. Never booked as zero |
| Day-start equity and drawdown peak | Derived from equity marks | Also persisted (`shared_day_start`, `shared_equity_peak`). Not cleared when the flag is turned off |
| `WARP_TRADE_DAILY_LOSS`, `WARP_TRADE_DRAWDOWN`, stop cooldown, stop streak | Not passed into the AEGIS check | Passed from the persisted anchors and the realized-close ledger |
| Position count cap | WARP strategies | WARP strategies only |
| `WARP_LIVE_EXECUTION_ACK`, HALT file, pause, mode gates, bot action gate | Enforced | Enforced, untouched |

"Open profit or loss" is quantity times the public mark minus net cost, for each journaled WARP strategy. A mark that is missing, or a broker `lastPrice` more than 2% away from the public price, makes the equity unreliable and blocks entries. External coin balances and prices never enter the number, so a BTC or ETH move can neither trip a halt nor hide one.

The engine records `shared_account_since` in the journal settings on the first shared cycle. Whole-account marks written earlier, and marks written while the flag is off, are not compared with sleeve equity. Sleeve marks are stored separately. Turning the flag off does not delete `shared_account_since`, the peak, the day-start, or realized P&L. Turning it back on continues the same high-water mark.

## What WARP will and will not touch

- Orders: WARP only creates new spot long strategies for pairs in its own universe. There is no sell, close, pause or delete path that starts from a wallet balance or from a strategy WARP did not create.
- Bot actions (`apply_action`) work on journaled `WARP-<id>` rows, and only when the remote bot's name and code match the journal. A foreign bot, an unknown id, or a row pointing at a foreign bot is refused before any write. `tests/test_shared_account.py` pins this.
- Other bots (for example VORTEQ, AEGIS, BOT-NAME-*) are only ever read through the existing read-only calls (bot list, positions book, reserve lookup). They are never modified and never counted against WARP's caps.

## Risk trade-offs

1. WARP no longer proves the whole wallet is accounted for. A coin that appears in the wallet for any reason is invisible to it. That is the point of shared mode, and also the loss of a control that strict mode gives.
2. WARP can only check that the wallet holds at least what its own strategies need. If WARP and another bot hold the same coin, the wallet check cannot tell whose quantity is whose. Exits come from 3Commas' own strategy accounting, which this repository does not verify. Keep WARP off coins you hold elsewhere with `WARP_DENY_SYMBOLS`.
3. Free USDT is shared. Another bot or a manual buy can spend it between WARP's read and its order. WARP checks free USDT against the sleeve and subtracts other DCA bots' maximum required amount before sizing, but it cannot reserve cash.
4. Realized P&L is booked when a WARP strategy retires, from its quote entry cost, exit proceeds, and `totalFee` when that field is present. Sleeve equity includes the sum of those rows since `shared_account_since`. A close whose costs cannot be read, or a retired journal row with no booked P&L, halts entries. It is not treated as a zero loss. Free USDT in the shared wallet is not the loss limit: three stopped deals totaling -15 USDT on a 50 USDT sleeve leave equity at 35 and trip the drawdown halt even though the wallet still holds other bots' USDT. Whether 3Commas' cost fields are already net of fees is not documented on the row; an explicit `totalFee` is subtracted, and a missing cost fails closed.
5. The sleeve is a budget enforced by WARP's sizing, not an exchange-level cap. A bug in sizing or a change made in the 3Commas UI is not bounded by it.
6. A WARP-looking strategy that is not in the journal (restored backup, second WARP instance, spoofed client id) stops entries until it is reconciled by hand.

## Rollback

Unset `WARP_SHARED_ACCOUNT` (or set `0`) and restart. Strict mode resumes and will halt on any unexplained balance, as before. Pause new entries at any time with the HALT file or the dashboard pause.

Tests: `tests/test_shared_account.py`. Strict-mode behavior is covered by the existing suites, which run with the flag off, and by `StrictModeUnchangedTests`.
