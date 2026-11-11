# WARP 3C Bot — System Architecture

## Responsibility boundary

- **Vibe-Trading**: read-only market research, evidence and candidate signals. No trading account key and no shell tools. AI output is untrusted until parsed.
- **WARP risk/execution worker**: checks the live portfolio, applies deterministic limits, chooses the base order size, and sends an authenticated 3Commas REST request.
- **3Commas**: broker strategy order lifecycle, DCA averaging, exits, and exchange connectivity.
- **Fly.io**: exactly one worker on an always-on Machine with a persistent volume.

## Deployment / state

One container runs the Vibe API on localhost and the WARP orchestration loop. A local health server exposes `/health`. SQLite uses WAL, FULL synchronous writes, and a local file lock. The volume MUST be persisted and backed up. Never run two independent writers to the same exchange account.

The account must be dedicated to this worker: other bots and terminal positions cannot reliably be included by the current risk adapter. Missing active-position metadata fails closed. No strategy without a `warp3c-` client ID is admitted.

## Mode gates

1. `observe`: read-only API key, never create a 3Commas strategy.
2. `paper`: checks the selected 3Commas profile reports `demo: true` before any submission.
3. `live`: checks `demo: false` and requires `WARP_LIVE_EXECUTION_ACK=I_UNDERSTAND_LIVE_ORDERS`. This is a code gate, **not** evidence of deployment readiness.

## Sizing

- Base order amount = min(configured max, per-pair cap / ladder factor, remaining global cap / ladder factor, free reserve budget / ladder factor).
- Full DCA ladder is reserved immediately, including unfilled averaging orders.
- Free reserve and global cap use a conservative quote-balance + strategy cost-basis approximation. This is not a mark-to-market NAV or PnL-based drawdown guard.
- Only USDT quote-priced Binance Spot strategies are supported in this iteration.
- At most **one** new entry per worker cycle; existing pairs and recently submitted symbols are deduplicated.
- After a POST timeout/error, the journal records an unresolved submission and blocks future entries until reconciliation.

## Known gaps before live production

1. **Broker contract test** with a real 3Commas **demo** profile and the current API schema (this repository only includes offline mocked tests).
2. Exchange market / minimum quantity / fee validation; exchange-specific price precision and minimum notional.
3. Consolidated mark-to-market NAV and drawdown protection (currently only capital commitment / cash-reserve limits).
4. Active strategy pagination, account isolation, disconnected/external positions, and reconciliation of completed/closed uncertain submissions.
5. Observable incident alerts (Telegram or another alert provider), backups/restore testing, and credential rotation.
6. A signal evaluation dataset with forward tests; AI signal quality is unproven.

Do not enable real money until these gaps are addressed and an operator explicitly approves live activation.
