# WARP 3C system status

Verdict for this pass: **observe and paper paths are implemented and unit-tested**. This is not a demo round-trip, not a Fly deploy, and not a live trading approval. Default mode remains `observe`. Nothing in this repository has submitted a real or demo order.

## Done in this phase

- Binance public spot screening and exchange-rule-validated automatic discovery. Unattended TradingView scanner calls are disabled; manual TradingView chart review remains possible. `fly.toml` uses `WARP_DISCOVERY=auto` in observe mode. The code default remains `static` for backwards compatibility and the denylist applies in both modes.
- Slim analysis client for NVIDIA and OpenRouter with JSON extraction, price-field stripping, timeouts, failover, and a process-local daily cap. Entry, stop, and target are derived from the candle snapshot.
- Confidence blend (cannot raise the model score) and a long-only backtest gate. Both are on in `fly.toml` and off unless the env flags are set, so older tests keep their behavior.
- DCA strategy payload (unchanged for the Moderate preset), cabinet DCA payload, and interval grid payload. Presets clamp to the configured caps. Client ids are deterministic per symbol, kind, preset, and UTC day. A rejected id may take the next attempt. An unknown or confirmed id blocks a second submit.
- Grid and cabinet DCA writes exist on the 3Commas client. A response without an id stays unresolved. Confirmed bots that do not appear on the strategy list still consume pair capacity.
- FastAPI and the stdlib server share one dispatcher. Password session cookie, bearer token, HALT, and resume. React/Vite/Tailwind dashboard with English default and an Indonesian toggle, plus stage statuses, signal provenance and explicit stale-evidence labels.
- Image no longer installs the Vibe-Trading fork. The HTTP client and the pin remain for an external Vibe process.

## Paper desk

- Accounts from `GET /open_api/api_profiles` are shown with exchange, quote balance, and a DEMO or REAL badge. Selection is stored as `target_account`. Paper cannot select a real account. Live still requires `WARP_LIVE_EXECUTION_ACK=I_UNDERSTAND_LIVE_ORDERS` and refuses a demo account.
- Spot buy/long only. Futures accounts are shown and cannot be selected. Eligible NAV is the sleeve in [docs/tradebook-addendum.md](docs/tradebook-addendum.md), after other bots' safety orders are reserved.
- Stop-loss and take-profit are dashboard toggles, both on by default. Turning the stop off sizes the deal against a virtual stop and shows **No SL**. Turning take-profit off without a trailing exit shows **Manual exit**.
- The minimum score defaults to 80 and can be set from 50 to 100 on the bot preset. Below 80 shows a warning. Rubric weights are editable and must sum to 100.
- Every create is stored in `warp_bots` under a `WARP-` name. Pause, resume, close, and delete load that row, re-read the remote bot, and refuse the call unless the remote name matches. Open API strategy orders stay on the old client id and do not have a cabinet pause route.
- Verdicts are stored with the market price at decision time. A later cycle fills 4h, 24h, and 72h moves from Binance 1h closes, net of round-trip fees. The Signals page shows hit rate. The Bots page shows DEMO/REAL accounts. The Rules page lists every active threshold and the last cycle's AEGIS status.
- `python -m warp3c.papercheck` is the demo round trip. It is not a live approval.

## Still open before any live use

1. `python -m warp3c.papercheck` is implemented and unit-tested with a mock broker. It refuses real accounts and any account that is not spot. It has not been run against a live demo spot profile from this environment.
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
