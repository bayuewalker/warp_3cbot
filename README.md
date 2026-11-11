# WARP 3C Bot

Vibe-Trading proposes candidates. A deterministic risk engine sizes them. 3Commas is the only execution path. One Fly.io worker, Binance Spot USDT pairs.

Default mode is **observe**. This build is not live-verified. Do not grant trade permission until a demo round-trip and account reconciliation have passed, and do not treat a green `/health` or `/live` response as permission to trade.

## Flow

1. Read public Binance spot candles, 24h volume, and exchange filters. No trading key is used. `WARP_DISCOVERY=auto` ranks the allowlist by volume and spread; `static` keeps `WARP_SYMBOLS`.
2. Ask Vibe for `BUY` / `WAIT` / `SKIP`. Model prices and sizes are ignored. A `BUY` also needs a successful Vibe market-data tool call.
3. Read the 3Commas API profile and live strategies when credentials exist. Paginate until the list is proven complete.
4. Reserve the full DCA ladder plus a fee buffer, enforce portfolio, pair, correlation, reserve, daily-loss, and drawdown limits, and refuse stale or unexplained balances.
5. In `paper` or `live` only, write a `warp3c-` client id to SQLite and then POST. Timeouts stay unresolved and block new entries. Definite 4xx responses are marked rejected and are not retried blindly.

Modes: `observe` (no writes), `paper` (demo profile only), `live` (real profile plus `WARP_LIVE_EXECUTION_ACK`). Live is not enabled by this repository. The 3Commas profile should be dedicated to WARP: terminal strategies are invisible to the Open API.

## Run locally

1. Copy `.env.example` to `.env`. Do not commit it.
2. `docker compose up --build` installs the pinned Vibe-Trading fork. With no 3Commas credentials the worker still scans and researches, and it does not submit.
3. `http://127.0.0.1:8080/live` is process liveness. `/ready` is dependency health. `/dashboard` is the read-only operator page. `execution_permitted` stays false in observe.
4. `python -m pip install -e ".[dev]" && ruff check src tests && python -m unittest discover -s tests -v`

Read [ARCHITECTURE.md](ARCHITECTURE.md) and [DEPLOYMENT.md](DEPLOYMENT.md) before connecting a demo profile.

Contracts: [Vibe sessions](https://github.com/HKUDS/Vibe-Trading/blob/master/guides/cli-and-api.md), [3Commas HMAC](https://trade.3commas.io/docs/rest-api/auth), [API profiles](https://trade.3commas.io/docs/rest-api/schemas/apiprofile), [strategy schema](https://trade.3commas.io/docs/rest-api/schemas/strategy), [new trade](https://trade.3commas.io/docs/rest-api/post-strategies-trade).
