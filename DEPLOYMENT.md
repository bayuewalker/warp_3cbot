# Fly.io Deployment Runbook

## Recommendation

Singapore (`sin`), a 2 GB single Fly Machine to start. The actual RAM footprint of Vibe-Trading should be measured; do not assume this is enough. Scale one Machine **vertically** when necessary, not horizontally. Set autostop off and keep exactly one running instance.

## Steps (after local + demo tests)

1. Install `flyctl`, log in to your own Fly.io organization, and choose a unique Fly app name.
2. `fly apps create <app-name> --org <org>`
3. `fly volumes create warp_data --region sin --size 3 --app <app-name>`
4. Set secrets with `fly secrets set --app <app-name>`; copy the values from `.env.example`. Required: `API_AUTH_KEY`, your LLM provider token, `THREECOMMAS_API_KEY`, `THREECOMMAS_API_SECRET`, `THREECOMMAS_PROFILE_CODE`. Do not put secrets in `fly.toml`.
5. `fly deploy --config fly.toml --app <app-name>`
6. Verify `fly status --app <app-name>`, `fly logs --app <app-name>`, and `/health`. Confirm `TRADING_MODE=observe` in logs.
7. Allocate **app-scoped static egress IPs** via the current Fly CLI, then whitelist those IPs in 3Commas where supported. The inbound Fly address is not the outbound egress IP.
8. Verify demo broker orders and recovery behavior before changing the mode; a public URL is never an indication that live trading is active.

## Operations

- Emergency pause: create `/data/HALT` inside the Machine (or temporarily scale the worker to zero). This blocks **new** submissions; it does not liquidate open 3Commas positions.
- Do not delete the `/data` volume: it contains the order journal, dedupe history and Vibe research state.
- Back up the volume and verify restore before production.
- For static egress IPs refer to [Fly app-scoped egress IP migration](https://fly.io/changelog/app-scoped-egress-ip-migration/).
- For Fly billing refer to [2026 pricing](https://fly.io/pricing-update/). Compute, storage, static egress and bandwidth may be separate charges.
