# WARP 3C system status

Verdict for this coding pass: **BLOCKED**. The observe-mode worker now has the modules that can be built without credentials. It is not a demo integration, not a Fly deployment, and not a green CI run. Default mode remains `observe`. Nothing in this repository has submitted a real or demo order.

## Implemented

- Static allowlist by default. `WARP_DISCOVERY=auto` ranks Binance spot USDT markets by quote volume after liquidity, spread, denylist, and precision checks, caches `exchangeInfo`, and falls back to `WARP_SYMBOLS` when the public read fails.
- Separate scan, research, monitor, and reconcile intervals. Research runs on a side thread and cannot call 3Commas. Monitoring still runs while research is busy.
- Decision rows for universe, market, research, risk, and broker outcomes.
- Trend profile is the only one that can size a ladder. Model fields such as `amount` are not read. Slippage is an extra buffer and defaults to 0 bps.
- Open-pair volatility, daily loss, drawdown, stale data, and unknown strategy status still fail closed.
- Alert rows stay pending until webhook or Telegram delivery succeeds, then retry with backoff.
- `/live`, `/ready`, `/health`, and an authenticated `/api/*` snapshot plus `/dashboard`. `POST /api/pause` creates the HALT file and does not close positions.
- SQLite backup and restore helpers. One writer lock and the non-root image are unchanged.

## Not implemented

- Edit, pause, or cancel of an existing 3Commas strategy. Those official mutation responses were not re-verified here, so the worker does not call them.
- Range and downtrend entries. Turning them on would be a trading-policy change.
- Auto discovery is implemented and left off in `fly.toml` (`WARP_DISCOVERY=static`) so a deploy does not widen the pair list by itself.

## External blockers

- GitHub Actions billing lock. Local tests are not CI.
- No 3Commas demo key, so no demo profile read and no demo strategy round-trip.
- No Fly token, so no observe deploy, egress IP, or volume restore.
- No live webhook or Telegram destination was called.
- Paper equity still assumes demo free balance does not fall while a position is open. That assumption is not a signed policy.
