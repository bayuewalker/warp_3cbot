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
| `WARP_MAX_DAILY_LOSS_FRACTION`, `WARP_MAX_DRAWDOWN_FRACTION` | These are the halts the engine enforces, now measured on sleeve equity. Set them to the loss limits you want (defaults 0.05 and 0.15). |
| `WARP_TRADE_DAILY_LOSS`, `WARP_TRADE_DRAWDOWN` | Shown in the rules view and fed to the AEGIS policy, but the engine does not pass a day-start, peak or equity to AEGIS, so they do not halt entries today. Do not rely on them as the loss limits. |
| `WARP_MAX_PER_PAIR_FRACTION`, `WARP_MAX_GROUP_FRACTION`, `WARP_MAX_COMMITTED_FRACTION` | These fractions are now applied to the sleeve (for example 50 USDT), not to the account. With the defaults a 50 USDT sleeve cannot reach `WARP_MIN_BASE_ORDER_USDT`, so no order is planned. That fails closed. Check a paper or observe run before expecting entries. |
| `WARP_DENY_SYMBOLS` | List the pairs of coins you hold outside WARP (see residual risks). |
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
| Equity used for sizing, daily loss, drawdown | Whole account value | `WARP_SLEEVE_USDT` plus open profit or loss on WARP's own strategies |
| Equity history compared | All marks | Marks since shared mode was first enabled |
| Position count cap | WARP strategies | WARP strategies only |
| `WARP_LIVE_EXECUTION_ACK`, HALT file, pause, mode gates, bot action gate | Enforced | Enforced, untouched |

"Open profit or loss" is quantity times the public mark minus net cost, for each journaled WARP strategy. A mark that is missing, or a broker `lastPrice` more than 2% away from the public price, makes the equity unreliable and blocks entries. External coin balances and prices never enter the number, so a BTC or ETH move can neither trip a halt nor hide one.

The engine records `shared_account_since` in the journal settings on the first shared cycle. Whole-account marks written earlier are ignored, because comparing them with sleeve equity would look like a 99% drawdown. Switching back to strict clears the setting.

## What WARP will and will not touch

- Orders: WARP only creates new spot long strategies for pairs in its own universe. There is no sell, close, pause or delete path that starts from a wallet balance or from a strategy WARP did not create.
- Bot actions (`apply_action`) work on journaled `WARP-<id>` rows, and only when the remote bot's name and code match the journal. A foreign bot, an unknown id, or a row pointing at a foreign bot is refused before any write. `tests/test_shared_account.py` pins this.
- Other bots (for example VORTEQ, AEGIS, BOT-NAME-*) are only ever read through the existing read-only calls (bot list, positions book, reserve lookup). They are never modified and never counted against WARP's caps.

## Risk trade-offs

1. WARP no longer proves the whole wallet is accounted for. A coin that appears in the wallet for any reason is invisible to it. That is the point of shared mode, and also the loss of a control that strict mode gives.
2. WARP can only check that the wallet holds at least what its own strategies need. If WARP and another bot hold the same coin, the wallet check cannot tell whose quantity is whose. Exits come from 3Commas' own strategy accounting, which this repository does not verify. Keep WARP off coins you hold elsewhere with `WARP_DENY_SYMBOLS`.
3. Free USDT is shared. Another bot or a manual buy can spend it between WARP's read and its order. WARP checks free USDT against the sleeve and subtracts other DCA bots' maximum required amount before sizing, but it cannot reserve cash.
4. Daily loss and drawdown only see open profit and loss on strategies still on the live list. A deal that closes at a loss and leaves the list is not in the history, so the sleeve equity returns to `WARP_SLEEVE_USDT`. The per-deal and aggregate stop-risk caps, the sleeve size and the HALT file are the hard limits. A realized-P&L ledger in the journal would close this gap and was out of scope here.
5. The sleeve is a budget enforced by WARP's sizing, not an exchange-level cap. A bug in sizing or a change made in the 3Commas UI is not bounded by it.
6. A WARP-looking strategy that is not in the journal (restored backup, second WARP instance, spoofed client id) stops entries until it is reconciled by hand.

## Rollback

Unset `WARP_SHARED_ACCOUNT` (or set `0`) and restart. Strict mode resumes and will halt on any unexplained balance, as before. Pause new entries at any time with the HALT file or the dashboard pause.

Tests: `tests/test_shared_account.py`. Strict-mode behavior is covered by the existing suites, which run with the flag off, and by `StrictModeUnchangedTests`.
