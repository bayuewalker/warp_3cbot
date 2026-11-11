# WARP 3C Bot

Vibe-Trading AI research + deterministic global risk + official 3Commas REST execution. One always-on Fly.io worker, Binance Spot USDT pairs, and audit logging.

> Integration MVP, **not yet live-verified**. Default mode is observe-only. Never grant trade permissions until demo tests and account reconciliation pass.

## Flow

1. Read current 3Commas API profile balances and live strategies.
2. Vibe researches configured symbols and returns strictly structured, dated signals.
3. WARP validates symbols, evidence freshness and confidence; risk rules determine sizing.
4. Reserve the full DCA ladder capital and write a unique order ID to SQLite before calling 3Commas.
5. Reconcile any uncertain submissions before permitting further entries.

Modes: `observe` (no writes), `paper` (3Commas demo profile only), `live` (real profile, separate acknowledgement required). The 3Commas account should be dedicated to WARP: manual trades and external bots can make risk accounting incomplete.

## Run locally

1. Copy `.env.example` to `.env`. Do not commit credentials.
2. Configure the Vibe LLM provider and the 3Commas profile. Only read permissions are needed in `observe`.
3. `docker compose up --build` (first build installs Vibe-Trading dependencies).
4. Check `http://localhost:8080/health` and logs. With no credentials WARP remains idle.
5. `python -m unittest discover -s tests -v` runs offline tests.

Read [ARCHITECTURE.md](ARCHITECTURE.md) and [DEPLOYMENT.md](DEPLOYMENT.md) before connecting a demo or live profile.

Official contracts: [Vibe sessions](https://github.com/HKUDS/Vibe-Trading#-api-server), [3Commas HMAC](https://trade.3commas.io/docs/rest-api/auth), [3Commas API profiles](https://trade.3commas.io/docs/rest-api/schemas/apiprofile), [live strategies](https://trade.3commas.io/docs/rest-api/get-strategies-live), [new strategy](https://trade.3commas.io/docs/rest-api/post-strategies-trade).
