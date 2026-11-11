# Fly.io deployment and rollback

Region: Singapore (`sin`). One Machine, 2 GB, volume `warp_data` mounted at `/data`. Start in observe mode. Do not create the app until `flyctl auth` is already available, and do not set `TRADING_MODE` to `live`.

`/live` is the Fly check so a risk halt does not restart the Machine. Restart policy is `always` with 10 retries. `kill_signal` is `SIGTERM` and `kill_timeout` is 30 seconds, matching Compose `stop_grace_period`.

## Deploy

1. Install `flyctl` and `fly auth login` to the organization that will own the app.
2. `fly apps create <app-name> --org <org>`
3. `fly volumes create warp_data --region sin --size 3 --app <app-name>`
4. `fly scale count 1 --app <app-name>` after the first deploy. Do not scale above 1. Autostart is off; it does not create Machines, but a cloned Machine would not share the SQLite lock.
5. Set secrets with `fly secrets set --app <app-name>`. Copy names from `.env.example`. Required for a read-only observe loop that can see the account: `THREECOMMAS_API_KEY`, `THREECOMMAS_API_SECRET`, `THREECOMMAS_PROFILE_CODE`, `API_AUTH_KEY`, and the LLM provider token. Do not set `WARP_LIVE_EXECUTION_ACK`. Do not put secrets in `fly.toml` or the image.
6. Optional: `WARP_ALERT_WEBHOOK_URL` must be `https`.
7. `fly deploy --config fly.toml --ha=false --app <app-name>`
8. Confirm `fly status`, `fly logs`, and `curl https://<app>.fly.dev/live`. The JSON field `execution_permitted` must be false while `TRADING_MODE=observe`.
9. Allocate app-scoped static egress IPs and whitelist them on the 3Commas key. The public inbound address is not the outbound address. See [Fly app-scoped egress IP migration](https://fly.io/changelog/app-scoped-egress-ip-migration/).

Vibe-Trading is installed from the pinned fork in the Dockerfile. Measure RAM before assuming 2 GB is enough. Scale the one Machine vertically, not horizontally.

## Rollback

1. `fly releases --app <app-name>` and `fly deploy --image <previous-image> --app <app-name>` or `fly releases rollback`.
2. Leave `TRADING_MODE=observe`. Rolling back the image does not close 3Commas strategies that were already accepted.
3. If a release sent an uncertain POST, do not deploy a second writer. Keep the volume. The journal must reconcile before any later paper or live process submits again.
4. Emergency stop for new entries: `fly ssh console --app <app-name> -C "touch /data/HALT"`. This does not liquidate. Removing the file resumes the cycle. Scaling to zero also stops the worker and also stops reconciliation until it is started again.

## Operations

- Do not delete the volume. It holds the order journal, equity marks, and Vibe state.
- The process can copy its SQLite file with the backup helper. Restoring that copy onto a spare Fly volume has not been executed. Do not delete the live volume to test it.
- Logs are JSON lines. They must not contain API keys or signatures. If a provider token appears, rotate it.
- Billing for compute, volume, egress, and bandwidth is separate. See [Fly pricing](https://fly.io/pricing-update/).

No Fly app is created by the tests or by this change.
