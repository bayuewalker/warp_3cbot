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

The XAU thread does not write `HEALTH`. When the spot lane is postponed, the host loop copies `xau.state` onto `HEALTH["xau_state"]` and `/ready` follows that state. When the spot lane is active, `/ready` stays the worker predicate.

`/api/status` carries `xau` and `lanes`. `xau.feed` and `xau.basis` are the live book, including before the first signal. If the snapshot read fails, `xau` is `{"enabled": false, "state": "unavailable"}`.

## Data flow

1. Candles come from Binance USDⓈ-M `XAUUSDT`, then Bybit, then OKX. The open bar is dropped. Bars inside the New York close (Friday 17:00 to Sunday 18:00, and the daily 17:00–18:00 break) are dropped.
2. Spot for the basis is TradingView `OANDA:XAUUSD`. In the same cycle the desk reads the Binance USDⓈ-M last price and stores one `(perp_now − spot_now)` sample per minute. The basis is the median of the last 15 samples. It stays unhealthy under 10 samples, when the absolute median exceeds the configured maximum, or when the standard deviation exceeds 1.50. `basis_unhealthy` is sent only after that state has lasted 10 minutes, at most once an hour. `basis_recovered` is sent only after an unhealthy alert. Published levels are perp minus that basis.
3. DXY is informational only.
4. News is the Forex Factory weekly JSON, at most once an hour, with `data/news_static.toml` as the backstop.
5. At each killzone open the selector locks SMC, SCALP, or NO_TRADE for that window. EMA runs only in the two K3 windows.
6. The gate scores C1–C14. Only A+ and A with 14/14 are sent. Scalp is journaled with `sent=false`. Every cycle in an open window that does not send records one reason in `xau_funnel`: `market_closed`, `halt`, `dirty_basis`, `dirty_gaps`, `stale_m5`, `news_block`, `no_direction`, `engine_none`, `entry_closed`, `no_setup`, `gate_fail:<checks>`, or `risk_block:<rule>`. `/api/status` exposes today's counts, the current window's counts, and the last five reasons with their WIB time.
7. Outcome tracking uses closed M1 bars and resumes from the last bar stored for that signal.

## Tables

`xau_signals`, `xau_signal_events`, `xau_decisions` (last 5000), `xau_windows`, `xau_news`, `xau_feed_health`, `xau_funnel`, `xau_meta`.

## States

`disabled`, `starting`, `running`, `degraded`, `stalled`, `config_error`, `feed_unavailable`, `stopped`.

XAU starts only when `WARP_XAU_ENABLED` is exactly `1`. Any other value leaves it disabled. An unrecognized value logs `xau_config_warning` once and does not raise. Invalid `WARP_XAU_*` settings become `config_error` inside the XAU thread.
