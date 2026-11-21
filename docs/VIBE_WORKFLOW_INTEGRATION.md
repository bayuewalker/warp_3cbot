# WARP Vibe pipeline — Crypto Spot first

## Goal

Continue the four-stage workflow in `bayuewalker/warp_3cbot` **without** duplicating Vibe-Trading's API server:

1. **Market screening** — automated Binance Spot/USDT discovery on exchange-verified symbol, spread, liquidity and minimum-notional metadata.
2. **AI analysis** — read-only NVIDIA/OpenRouter analysis, or authenticated external Vibe-Trading research.
3. **AEGIS strategy/risk validation** — exchange candle evidence, signal timestamps, backtest, full DCA commitment, concentration limits, fee buffers, and account/position reconciliation.
4. **Trade opportunities** — mobile dashboard with ranked setups, deterministic entry/stop/target, provider/source time, and explicit stale-evidence indicators.

The repository already has these components. This change connects safer source selection, validation, and stage visibility. **It does not grant permission to send broker orders.**

## TradingView vs. automated data

TradingView's Terms of Use explicitly restrict non-display automated use of TradingView data (including alerts/webhooks) and the official MCP server explicitly prohibits automated trading and bot data-feeds. Do not use `scanner.tradingview.com/crypto/scan`, bulk scrape a scanner page, or send MCP market data into 3Commas execution. Refer to:

- https://www.tradingview.com/policies/
- https://www.tradingview.com/mcp/docs

TradingView charts are **manual visual references only**. The autonomous market data is from Binance's documented public Spot market-data host (https://data-api.binance.vision), with only GET requests.

The legacy `WARP_DISCOVERY=screener` setting remains backward compatible but uses the Binance public screener. Recommended deployment uses `WARP_DISCOVERY=auto` because it additionally validates trading status, spot eligibility, spread, precision, notional and the global denylist. Static discovery also respects the denylist. Both modes fail closed when required exchange/mark information is unavailable.

## Link the existing deployed Vibe-Trading service

Your Vibe-Trading deployment is hosted at:

`https://vibe-trading-bayue.fly.dev`

The Vibe HTTP research adapter already exists at `src/warp3c/vibe.py`. It creates a Vibe session, submits a research prompt, polls for the linked response, validates symbols/actions/age, and requires successful market-data tool evidence for BUY decisions.

**No remote Vibe credential is stored in Git or this document.** To select Vibe as the research provider, configure your WARP 3C deployment's Fly secrets (replace `<warp-fly-app>` with the WARP app's actual name):

```sh
fly secrets set \
  VIBE_API_URL=https://vibe-trading-bayue.fly.dev \
  WARP_ANALYSIS=vibe \
  --app <warp-fly-app>

# Separately: use the Vibe-Trading API bearer secret in WARP's
# API_AUTH_KEY Fly secret. Do not paste it into GitHub issues or logs.
```

Confirm the Vibe endpoint supports the expected sessions/messages HTTP contract. A working `/live` endpoint is **not** proof that authenticated Vibe research or model calls succeeded. The same bearer secret should not be reused as the WARP operator token or a 3Commas API secret.

If remote Vibe analysis isn't enabled, `WARP_ANALYSIS=auto` uses the existing configured local NVIDIA/OpenRouter analyst. PR #5 in WARP 3C separately addresses a retired NVIDIA model; avoid merging contradictory model-default changes without reconciling that PR.

## Deployment boundaries

- Remain `TRADING_MODE=observe`. No `WARP_LIVE_EXECUTION_ACK`.
- Fly requires **one** worker Machine, a retained `/data` volume, and an exclusive lock.
- Keep `WARP_MANAGE_VIBE=0` when using a remote Vibe service; it must not receive 3Commas credentials.
- Default schedule: scan every 300s, model research every 900s, account monitoring every 60s, reconciliation every 120s.
- Always stop new entries on stale signal, stale mark, lost exchange/3Commas data, unknown submission, or HALT. Monitoring/reconciliation must continue during HALT.
- Until a real 3Commas demo account round-trip, network smoke test, and full CI pass, use observe only. No DCA execution or production funds.
- Existing dashboard English default is intentional; the user may explicitly select Bahasa Indonesia.

## Observable state

The authenticated dashboard exposes four stage badges in **Overview**, and offers source/evidence age in **Signals**. The `GET /api/status` object includes `pipeline.screening`, `pipeline.analysis`, `pipeline.risk`, and `pipeline.opportunities`. `/api/screener` identifies the real source as `binance`. Retained opportunities are displayed as stale when the evidence age exceeds the configured signal-age limit.

An empty research panel may mean no eligible markets, missing model configuration, or blocked risk: it is not a successful automated strategy. A numerical rank/confidence score is not a calibrated win probability.

## Tests / remaining external gates

Run from repo root:

```sh
python -m unittest discover -s tests -v
python -m ruff check src tests
cd web && npm ci && npx tsc --noEmit && npm run build
```

Additional static and mocked tests cover the TradingView transport prohibition, Binance discovery, age gating, denylist, and held-asset price refresh. To call the implementation **deployed**, also verify the actual Fly WARP app, Binance egress from its Machine, authenticated Vibe sessions, 3Commas observe-only reads, and persistent journal replay after restart.
