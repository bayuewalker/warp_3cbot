# WARP 3C

One worker for spot crypto: a scheduled screener, a bounded analysis step, a deterministic risk engine, and 3Commas. Default mode is **observe**. Nothing in this repository turns live trading on.

`execution_permitted` stays false in observe. A green `/live` response is process liveness, not permission to trade.

## Flow

1. **Screener.** Each unattended scan uses Binance's documented public Spot endpoints, not TradingView's unofficial scanner. Dashboard filters (volume, change, cap) are saved locally; exchange-validated `WARP_DISCOVERY=auto` applies spot/tradability, spread, notional and denylist gates. The legacy `screener` discovery mode also uses Binance public data. `static` keeps an operator-selected list subject to the denylist. Binance closed candles remain the OHLCV source. No exchange trading key is used. See [Vibe workflow integration](docs/VIBE_WORKFLOW_INTEGRATION.md).
2. **Analysis.** NVIDIA (`NVIDIA_API_KEY`, OpenAI-compatible, default model `nvidia/nemotron-3-nano-30b-a3b`) screens names. OpenRouter (`OPENROUTER_API_KEY`, default `deepseek/deepseek-v4-pro`) writes the final `BUY` / `WAIT` / `SKIP` JSON. Each provider can fail over to the other. Calls time out, and a process-local daily call and USD cap stops further calls. The model cannot set entry, stop, or target. Those come from the last price, ATR-style volatility, and the configured fractions. A `BUY` still needs a passing market snapshot.
3. **Risk.** Existing portfolio, pair, reserve, daily-loss, and drawdown caps still apply. With `WARP_BLEND_CONFIDENCE=1` the score can only fall below the model number. With `WARP_REQUIRE_BACKTEST=1` a long-only candle replay must clear a minimum trade count and a positive expectancy before a `BUY` can be sized.
4. **Execution.** Observe logs the plan and does not POST. Paper and live reserve a deterministic `warp3c-` client id, then POST. The default product is the Open API spot strategy (DCA ladder) because that response returns a strategy id. A saved Grid template posts to `POST /open_api/grid_bots/create?gridMarket=spot`. A cabinet DCA bot template posts to `POST /open_api/dca_bots/create`. If that response has no id, the journal stays `unknown` and new entries stop. Presets are Conservative, Moderate, and Aggressive, and they are clamped to the configured caps. One new order per cycle. HALT blocks new submissions and does not close positions. A confirmed order queues a Telegram or webhook alert.

Modes: `observe` (no writes), `paper` (demo profile only), `live` (real profile plus `WARP_LIVE_EXECUTION_ACK=I_UNDERSTAND_LIVE_ORDERS`). Live is not enabled by this repository.

The dashboard lists 3Commas accounts with a DEMO or REAL badge. Paper mode can select only a demo spot account. A real account can be selected only while the process is already in live mode, which still requires the acknowledgement above. WARP is spot buy/long only. Eligible capital is a sleeve (`WARP_SLEEVE_PCT`, default 10%), not the whole wallet, and other bots' safety orders are reserved first. See [docs/tradebook-addendum.md](docs/tradebook-addendum.md). WARP names every cabinet bot `WARP-` plus an id stored in its database, and pause, resume, close, and delete refuse any bot that is not in that table.

`python -m warp3c.papercheck` creates a 15 USDT bot on a demo account, reads it back, pauses it, and deletes it. It refuses observe mode, live mode, a real account, and a HALT file. The same check is `POST /api/paper/roundtrip` with `{"confirm":"PAPERCHECK"}`.

## Dashboard

`/dashboard` is a dark, mobile-first page (React, Vite, Tailwind). English is the default. The EN/ID control is in the header. Sign in with `WARP_DASHBOARD_PASSWORD` (at least 12 characters). The session cookie is HttpOnly. `WARP_OPERATOR_TOKEN` (at least 16 characters) still authorizes `Authorization: Bearer` for automation, and it signs the cookie when `WARP_SESSION_SECRET` is unset.

Pages: Overview, Signals, Bots, Positions, Screener, Settings. Overview shows equity, today's PnL, open positions, the mode badge, and HALT. HALT and resume ask you to type the word. Saving a bot preset or a screener filter does not place an order.

`WARP_HTTP=fastapi` serves that API with FastAPI. The default `stdlib` server uses the same routes and is what the unit tests drive. Fly sets FastAPI.

## Run locally

1. Copy `.env.example` to `.env`. Do not commit it.
2. `python3 -m pip install -e ".[dev]"` then `python3 -m warp3c`. With no 3Commas credentials the worker still scans. It does not submit.
3. Build the UI once if you want the React page instead of the small fallback: `cd web && npm ci && npm run build`.
4. `http://127.0.0.1:8080/live` is process liveness. `/ready` is dependency health. `/dashboard` is the operator page.
5. `python3 -m ruff check src tests && python3 -m unittest discover -s tests -v`

`docker compose up --build` builds the same image Fly uses: dashboard assets included, Vibe-Trading not installed.

## Tests

The suite covers the old risk, journal, and broker contracts plus screener parsing, the backtest gate, DCA and grid payload bounds, client-id idempotency, and password/session auth. Tests do not call TradingView, NVIDIA, OpenRouter, or 3Commas.

## Deploy

Read [ARCHITECTURE.md](ARCHITECTURE.md), [DEPLOYMENT.md](DEPLOYMENT.md), and [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md). Secrets stay in Fly, not in git.

The image does not install the pinned Vibe-Trading fork (`bayuewalker/Vibe-Trading` @ `61d20c9a3a5954dcb52ce2bb758ca0ac4d6c71a4`). That process needed a large scientific stack. The worker keeps the Vibe HTTP client for an operator who runs `vibe-trading serve` elsewhere and sets `WARP_MANAGE_VIBE=1`. The default analysis path is the slim NVIDIA / OpenRouter client.
