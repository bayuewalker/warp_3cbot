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

## Still open before any live use

1. No demo-account round-trip has been run. Grid and cabinet DCA responses were checked against the published docs, not a captured demo body.
2. The LLM daily cap resets when the process restarts.
3. Editing, pausing, or cancelling an existing 3Commas bot is not implemented. HALT only stops new submissions.
4. Terminal strategies are invisible to the Open API. Use a dedicated profile.
5. GitHub Actions has not been observed from this environment. Local `ruff` and `unittest` were run here.
6. Correlation groups, volatility, RSI, daily loss, and drawdown defaults are still engineering defaults.
7. Paper mark-to-market still assumes demo free balance does not fall while a position is open.
8. A Fly volume backup has not been restored. The SQLite backup helper is covered by a local test only.
9. Vibe signal quality and the new screener/LLM path are not forward-tested.
10. Indonesian covers the dashboard chrome. The model thesis is requested in English and also rendered with an Indonesian sentence around it.

Do not enable real money until those gaps are closed and an operator explicitly turns live mode on.
