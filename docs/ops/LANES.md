# Lanes

XAUUSD signals are the primary lane. The 3Commas spot lane stays in the tree and is postponed unless a human turns it back on. Postponed means the process does not call it. It does not mean the code was removed, and it does not mean a write is allowed.

## Spot lane

`WARP_SPOT_LANE_ENABLED` is read once at boot.

- Exactly `1` runs today's spot loop: scan, research, the read-only 3Commas client, and the dashboard reads.
- Unset, `0`, or any other value postpones the lane. An unrecognized value is logged once as `spot_lane_config_warning` and does not raise.

While postponed, the host loop still publishes `/api/status`, flushes the Telegram outbox, and may refresh the public Binance price tape. It does not call `once()`, start dashboard reads, start Vibe, or build the screener. Broker-backed routes answer `{"state":"postponed"}` with HTTP 200. That is a lane state, not healthy broker data and not an error.

`/api/status` includes:

```json
{"lanes":{"primary":"xau","spot":{"state":"postponed","since":"<iso>","flag":"WARP_SPOT_LANE_ENABLED"}}}
```

`reason` is `spot_postponed`. `execution_permitted` and `risk_permitted` are false. `dependencies_healthy` stays true because the host loop itself is up.

## Restore the spot lane

Set the flag and restart the process:

```bash
fly secrets set WARP_SPOT_LANE_ENABLED=1 -a warp3c-bayue
```

Unset it to postpone again:

```bash
fly secrets unset WARP_SPOT_LANE_ENABLED -a warp3c-bayue
```

Do not set `TRADING_MODE` or `WARP_LIVE_EXECUTION_ACK` as part of this change. The mode stays observe. 3Commas stays read-only.

## Ready

With the spot lane active, `/ready` is 200 only when `dependencies_healthy` and `last_cycle_ok` are both true.

With the spot lane postponed, `/ready` is 200 only when the host loop is healthy and `xau.state` is `running`, `warming`, or `degraded`. `stalled`, `config_error`, `feed_unavailable`, `stopped`, and a failed host pass return 503.
