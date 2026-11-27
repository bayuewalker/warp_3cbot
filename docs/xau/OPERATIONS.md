# XAU operations

XAU is off until a human sets the flag after this change has been audited. Do not set it from a deploy script.

## Enable

```bash
fly secrets set WARP_XAU_ENABLED=1 -a warp3c-bayue
```

The process reads the flag at boot. Exactly `1` starts the desk. Unset, `0`, or any other value leaves it disabled.

The spot lane is postponed unless `WARP_SPOT_LANE_ENABLED` is exactly `1`. See `docs/ops/LANES.md`. While it is postponed the host loop still publishes status, flushes Telegram, and keeps the public price tape. It does not call 3Commas. `/ready` is then 200 only when that host loop is healthy and `xau.state` is `running`, `warming`, or `degraded`.

## Disable

Unset the secret and restart:

```bash
fly secrets unset WARP_XAU_ENABLED -a warp3c-bayue
```

## Pause XAU only

Create the file next to the database. In production the database is `/data/warp.db`, so the file is:

```bash
touch /data/XAU_HALT
```

New signals stop. Open signals keep being tracked. Remove the file to resume.

`/data/HALT` is the existing global pause. It also blocks new XAU signals and does not change how DCA treats HALT.

## Read status

`GET /api/status` includes `xau` and `lanes` when the operator is authenticated. The XAU block has `enabled`, `state`, `ready`, `ready_reason`, `heartbeat_at`, `window`, `next_window`, `engine`, `feed`, `basis`, `basis_std`, `basis_samples`, `spot_last`, `spot_at`, `spot_state`, `funnel`, `last_signal_at`, `today`, `open_signal`, `recent`, and `stats30`. `feed` and `basis` are the live book, including before the first signal. `spot_state` is `market_closed` through the weekend and the daily 17:00–18:00 New York break, and that quote is not fresh. `last_signal_at` is read from `xau_signals` and survives a restart.

`state` is `feed_unavailable` when no candle source answers. `/live` and `/health` stay process checks. `/ready` follows the spot lane when that lane is active, and follows XAU when the spot lane is postponed.

Alerts go out through the existing Telegram outbox. Every XAU event starts with `XAU `. The desk does not add a bot token or a chat id.
