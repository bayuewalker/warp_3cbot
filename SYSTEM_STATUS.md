# WARP 3C system status

Verdict for this pass: **observe and paper paths are implemented and unit-tested**. This is not a demo round-trip, not a Fly deploy, and not a live trading approval. Default mode remains `observe`. Nothing in this repository has submitted a real or demo order.

## Done in this phase

- TradingView crypto scan parser, filter ranking, and Binance 24h ticker fallback. `WARP_DISCOVERY=screener` uses that list and fails closed when both sources fail. `fly.toml` turns the screener on. The code default remains `static`.
- Slim analysis client for NVIDIA and OpenRouter with JSON extraction, price-field stripping, timeouts, failover, and a process-local daily cap. Entry, stop, and target are derived from the candle snapshot.
- Confidence blend (cannot raise the model score) and a long-only backtest gate. Both are on in `fly.toml` and off unless the env flags are set, so older tests keep their behavior.
- DCA strategy payload (unchanged for the Moderate preset), cabinet DCA payload, and interval grid payload. Presets clamp to the configured caps. Client ids are deterministic per symbol, kind, preset, and UTC day. A rejected id may take the next attempt. An unknown or confirmed id blocks a second submit.
- Grid and cabinet DCA writes exist on the 3Commas client. A response without an id stays unresolved. Confirmed bots that do not appear on the strategy list still consume pair capacity.
- FastAPI and the stdlib server share one dispatcher. Password session cookie, bearer token, HALT, and resume. React/Vite/Tailwind dashboard with English default and an Indonesian toggle.
- Image no longer installs the Vibe-Trading fork. The HTTP client and the pin remain for an external Vibe process.

## Paper desk

- Accounts from `GET /open_api/api_profiles` are shown with exchange, quote balance, and a DEMO or REAL badge. Selection is stored as `target_account`. Paper cannot select a real account. Live still requires `WARP_LIVE_EXECUTION_ACK=I_UNDERSTAND_LIVE_ORDERS` and refuses a demo account.
- Futures USDT-M uses the cabinet DCA bot (and a long derivative grid when that preset is saved): pair code is the perpetual such as `BTCUSDT`, exchange code comes from the account (`BINANCE_FUTURES`), leverage defaults to 1 and cannot exceed 3, direction is long. `marginMode` is sent as `isolated`. WARP does not edit the account's own margin mode, because that would affect bots it did not create. A futures account that reports `cross` cannot be selected. Paper may use a demo futures account whose margin mode is unreported; live may not.
- Every create is stored in `warp_bots` under a `WARP-` name. Pause, resume, close, and delete load that row, re-read the remote bot, and refuse the call unless the remote name matches. Open API strategy orders stay on the old client id and do not have a cabinet pause route.
- Before a create, WARP checks `min(WARP_BUDGET_USDT, WARP_BUDGET_FRACTION * balance)` minus open WARP commitments, and `WARP_MAX_WARP_BOTS`. The dashboard can store a tighter or looser per-account override. It does not place an order.
- Verdicts are stored with the market price at decision time. A later cycle fills 4h, 24h, and 72h moves from Binance 1h closes. BUY hits when price rises, SKIP when it falls, WAIT when the move stays inside 0.5%.
- `python -m warp3c.papercheck` is the demo round trip. It is not a live approval.

## Still open before any live use

1. `python -m warp3c.papercheck` is implemented and unit-tested with a mock broker. It has not been run against the owner's demo futures account from this environment.
2. The LLM daily cap resets when the process restarts.
3. Cabinet DCA and grid bots that WARP created can be paused, resumed, closed, or deleted. The Open API strategy product still has no pause route. HALT still blocks new submissions and also blocks resume.
4. Terminal strategies are invisible to the Open API. Use a dedicated profile.
5. GitHub Actions has not been observed from this environment. Local `ruff` and `unittest` were run here.
6. Correlation groups, volatility, RSI, daily loss, and drawdown defaults are still engineering defaults.
7. Paper mark-to-market still assumes demo free balance does not fall while a position is open.
8. A Fly volume backup has not been restored. The SQLite backup helper is covered by a local test only.
9. Verdicts are now journaled and scored after 4h, 24h, and 72h. That score is empty until the worker has been running.
10. Indonesian covers the dashboard chrome. The model thesis is requested in English and also rendered with an Indonesian sentence around it.

Do not enable real money until those gaps are closed and an operator explicitly turns live mode on.
