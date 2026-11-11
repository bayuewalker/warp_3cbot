# WARP 3C operator runbook

Default `TRADING_MODE=observe`. This runbook does not authorize paper or live trading. Deployment commands that need `fly auth` are listed so an operator can run them later. They have not been run from this repository.

## What the process does

1. Monitor reads the 3Commas profile and live strategies when credentials exist, reconciles unknown client ids, and refreshes equity marks.
2. Scan reads public Binance data. Research asks Vibe for `BUY`, `WAIT`, or `SKIP` and ignores any size the model adds.
3. Risk may plan one new ladder. Observe logs it and does not POST. Paper and live POST only after the journal reserves the client id.
4. A busy research thread does not stop step 1.

Cadence defaults: monitor 60s, reconcile 120s, scan 300s, research 900s. `WARP_CYCLE_SECONDS` remains in the environment for older notes; the loop waits `WARP_MONITOR_SECONDS`.

## Health

| Path | Meaning |
| --- | --- |
| `/live` | Process is up. Fly uses this so a risk halt does not reboot the Machine. |
| `/ready` | The last monitor or cycle finished without a dependency error. |
| `/health` | Same JSON as `/live`, including `risk_permitted` and `execution_permitted`. |
| `/dashboard` | Mobile page. It holds no account data until an operator token is entered. |
| `/api/*` | JSON snapshot. Requires `Authorization: Bearer $WARP_OPERATOR_TOKEN`. |

`execution_permitted` is false in observe. A 200 from `/live` is not permission to trade.

## Pause and resume

Pause stops new strategies. It does not close, reduce, or hide 3Commas positions.

- Dashboard button, or `POST /api/pause` with the bearer token.
- Or create `/data/HALT` (`fly ssh console -C "touch /data/HALT"` once a Machine exists).

Resume is deleting that file on the Machine. There is no HTTP resume route. Scaling to zero also stops reconciliation until the Machine is started again.

## Alerts

Set `WARP_ALERT_WEBHOOK_URL` to an `https` URL, or both `WARP_TELEGRAM_BOT_TOKEN` and `WARP_TELEGRAM_CHAT_ID`. A failed delivery stays pending and is retried. Recovery does not delete a pending row. Tokens are not written to the JSON log. Delivery to a real chat or webhook has not been executed here.

## Backup

The journal can copy itself to another SQLite file and restore that copy into an empty file. That path is covered by a unit test. The Fly volume restore below is not done:

1. Keep the Machine at one replica.
2. Copy `/data/warp.db` from the volume while the writer is stopped, or use the process backup helper onto a file on the volume.
3. Restore onto a spare volume and start an observe Machine against that spare. Do not point two Machines at one volume.
4. Confirm unknown client ids still block new entries before any later paper mode.

## Rollout

1. Observe with no secrets: scan and research only. `execution_permitted` stays false.
2. Observe with a 3Commas read key: account and strategy reads, still no POST. This step needs a key that is not in the repo.
3. Paper only after a demo profile returns `demo: true` and a strategy round-trip is checked by an operator. This build has not done that round-trip.
4. Live only with a non-demo profile and `WARP_LIVE_EXECUTION_ACK` set by an operator. Do not set it from this change.

Rollback of the image is in [DEPLOYMENT.md](DEPLOYMENT.md). Rolling back does not close a strategy 3Commas already accepted. If a POST outcome is unknown, keep the same volume and do not start a second writer.

## Invariants

- One Machine, one volume, one flock.
- Vibe's environment does not receive the 3Commas key, secret, profile code, live acknowledgement, or alert webhook.
- AI output cannot skip risk, and it cannot set the quote size.
- Unknown POSTs stay blocking across restarts. Definite 4xx marks the row rejected. POST is not retried.
- Terminal strategies opened by hand are invisible to the Open API. Use a dedicated profile.
