# Operator alerts

WARP keeps operator alerts in the `alerts` table and sends them only when a channel is configured. The text is plain. It is cut to 400 characters. Nothing in an alert is a credential.

## Channels

Set both `WARP_TELEGRAM_BOT_TOKEN` and `WARP_TELEGRAM_CHAT_ID`, or set neither. The chat id is numeric (`-` allowed). `WARP_ALERT_WEBHOOK_URL`, when set, must be `https`.

Telegram is `sendMessage` with no `parse_mode` and a 5 second timeout. The body is `{"chat_id", "text"}`. Operator text is `{event}: {detail}`. A verdict card, whose detail starts with `🎯 Verdict`, is sent as that card alone. Either way the text is cut at 400 characters.

If neither Telegram nor the webhook is set, the row moves to `skipped_no_channel`. It is not retried and it is not `delivered`. A pending or delivered row still suppresses the same event until `clear_alert()` on an ok cycle. A skipped row does not: once a channel exists, the same event can be queued again. The log is one `alert_skipped` line with `reason=no_channel` per event.

## Logs

httpx logs every request at INFO, and the Telegram URL has the bot token in the path. A filter on the `httpx` and `httpcore` loggers (installed at import, and again when the worker configures logging) rewrites `/bot<token>/` to `/bot<redacted>/` in `record.msg` and `record.args`. Request lines for other hosts are unchanged.

A failed send logs `alert_failed` with `channel` and, for an HTTP response, `http_status`. The exception text is not logged: `str(exc)` contains the request URL.

```
HTTP Request: POST https://api.telegram.org/bot<redacted>/sendMessage "HTTP/1.1 200 OK"
```

## Retry

A failed send stays `pending`. The next attempt is `5 * 2^n` seconds, capped at 3600. A Telegram 429 uses `parameters.retry_after` for that next attempt, also capped at 3600.

Each worker loop starts at most 3 sends and does not start another once 10 seconds have already been spent in that flush.

## Events

| Event | When |
| --- | --- |
| `research_failed:<code>` | First failure of a research streak. `code` is `llm_cap`, `providers_failed`, `bad_json`, `unconfigured`, or `research_error`. |
| `llm_cap` | The daily model cap is reached. |
| `deal_closed:<symbol>:<client_id>` | A WARP strategy retires. The text is realized quote P&L, quote fee, and status. |
| `halt_on` / `halt_off` | The HALT file appears or disappears. The chat text is the halt verdict card. Each alert flush compares the file with the previous observation, and the worker flushes every loop. The same comparison also runs at the start of monitor, cycle, and cached execute. |
| `worker_started` | The worker log line. Text is `mode` and `shared_account` only. |
| `breaker:drawdown` / `breaker:daily_loss` | The circuit breaker trips. The text is the reason word. It does not include equity. |
| `order:<symbol>:<client_id>` | An order was submitted. |
| `cycle_blocked` | The worker loop is blocked. The chat text is the fixed blocked card. The exception text stays in the log. |
| `alert_test` | `POST /api/alerts/test`. |

Dedup is unchanged: a pending or delivered row with the same event suppresses a new one.

The text does not include the bot token, a full account code, or balances of assets that are not WARP's.

## Operator test

`POST /api/alerts/test` uses the same operator authentication as the other POSTs. It enqueues `alert_test` with the fixed text `WARP alerts connected (<mode> mode) - test`. Fields in the body are ignored.
