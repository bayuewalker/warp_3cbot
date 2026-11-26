# XAU architecture

XAU is a signal desk inside the WARP process. It never places an order. The only execution type is `NullAdapter`, and every mutating method raises `ExecutionDisabled`.

## Thread

`start_xau` starts one daemon thread named `xau`. The loop is:

`while not stop: try cycle() except record and back off finally heartbeat`

Backoff starts at 30 seconds and doubles up to 600 seconds. `stop(timeout)` sets the thread event and joins only for that timeout, so a blocked cycle cannot hold process shutdown.

The status snapshot is a reference swap. Readers do not take a lock. If the heartbeat is older than `max(5 minutes, 4 × cycle)`, the snapshot reports `stalled` without waiting on the thread.

## What XAU may touch

- Its own sqlite connection to the same DB file, with `busy_timeout=2000`, autocommit, and WAL. Writes are single statements. The migration is one short transaction and creates only `xau_*` tables.
- An injected `alert_sink(event, detail)`. `__main__` builds that sink as `enqueue_incident` on the existing outbox. XAU does not call Telegram and does not read the bot token.
- `warp3c.telemetry.log_event` only.

XAU does not write `HEALTH` and does not change `dependencies_healthy`, `risk_permitted`, `execution_permitted`, `reason`, or `/ready`.

`/api/status` gains one key, `xau`, read from the snapshot. If that read fails, the value is `{"enabled": false, "state": "unavailable"}` and the rest of the payload is unchanged.

## Data flow

1. Candles come from Binance USDⓈ-M `XAUUSDT`, then Bybit, then OKX. The open bar is dropped. Bars inside the New York close (Friday 17:00 to Sunday 18:00, and the daily 17:00–18:00 break) are dropped.
2. Spot for the basis is TradingView `OANDA:XAUUSD`. The basis is the median of the last 15 one-minute `(perp − spot)` samples. Published levels are perp minus that basis.
3. DXY is informational only.
4. News is the Forex Factory weekly JSON, at most once an hour, with `data/news_static.toml` as the backstop.
5. At each killzone open the selector locks SMC, SCALP, or NO_TRADE for that window. EMA runs only in the two K3 windows.
6. The gate scores C1–C14. Only A+ and A with 14/14 are sent. Scalp is journaled with `sent=false`.
7. Outcome tracking uses closed M1 bars and resumes from the last bar stored for that signal.

## Tables

`xau_signals`, `xau_signal_events`, `xau_decisions` (last 5000), `xau_windows`, `xau_news`, `xau_feed_health`, `xau_meta`.

## States

`disabled`, `starting`, `running`, `degraded`, `stalled`, `config_error`, `feed_unavailable`, `stopped`.

XAU starts only when `WARP_XAU_ENABLED` is exactly `1`. Any other value leaves it disabled. An unrecognized value logs `xau_config_warning` once and does not raise. Invalid `WARP_XAU_*` settings become `config_error` inside the XAU thread.
