# Zero-error alerts

What each operator-facing signal means after the warm-up and debounce changes. This does not change risk, sizing, or XAU rules. A blocked cycle still refuses new entries in that same cycle.

## cycle_blocked

The worker could not finish a cycle. Health flags go fail-closed immediately, including a single transient failure.

Telegram fires only when the block is worth a page:

- HTTP 5xx, HTTP 429, or a timeout: after at least two loop iterations and 60 seconds.
- Any other error: on the first occurrence.

`cycle_recovered` follows once, after an alerted block clears.

The log has one `cycle_blocked` line per occurrence, with `mode`, `error_type`, and `detail`.

## budget_wait

Research did not start because the remaining daily calls cannot cover one run (screen plus final when both providers are configured). The bot waits for the UTC day roll. This is a log line, not a Telegram alert, and it is not an `llm_cap` error.

The call cap and the dollar cap are unchanged. The configured research interval is the fastest the bot will run. When fewer runs fit in the calls left today, the interval gets longer. `next_attempt_at` is that paced time. A budget wait still shows the next UTC midnight, and a research error still shows its backoff.

A restart inside the research interval does not start research again. The last start time is stored in the journal.

## XAU basis_unhealthy

The perp-minus-spot basis is not usable, so XAU signals stay blocked.

For the first 15 minutes after the XAU runner starts (10 one-minute samples plus 5 minutes), reason `few_samples` is `warming`. That state does not alert and does not increment the episode.

If the basis is still unhealthy when warm-up ends, one `XAU basis_unhealthy #<n>` alert is sent. A later healthy-to-unhealthy change alerts the same way. `XAU basis_recovered #<n>` is sent once after an alerted episode clears.

## XAU status fields

`feed` and `basis` are the last signal row (`last_signal_feed` and `last_signal_basis` in this note). They stay null when no signal has been stored.

The live book is separate:

- `live_feed`: provider from the last successful book read
- `last_bar_age_s`: age of that bar
- `basis_value`, `basis_samples`, `basis_reason`
- `warming`: true only during the post-start `few_samples` window

## forward_eval_failed

Forward scoring runs on the scan clock, at most once per scan interval, with at most 20 price lookups per run. A lookup that fails waits 60 seconds, then 120, doubling up to one hour, and the retry counts against that same cap. The loop keeps running. The log is once per failure streak.

## Backtest window

`WARP_BACKTEST_CANDLES` is optional and off by default. Unset means the current 120 closed 15-minute candles, requested as a single limit of 120. An allowed opt-in is 120 through 1000. The scan then requests that many closed candles (one extra raw bar so the forming candle does not shorten the series) and pages backward at the exchange cap of 1000. Regime and RSI still use the most recent 120 raw candles. If the longer series is not in hand, the backtest result is `not_enough_candles`. The minimum-trade rule, stop, and target are unchanged. Do not set the variable unless that longer window is what you want.
