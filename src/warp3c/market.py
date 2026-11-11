"""Public spot market data. No exchange trading credentials are used."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal

import httpx

from .config import Settings
from .models import MarketEvidence, positive


class MarketDataError(RuntimeError):
    """Candle, ticker, or exchange rule data is missing. Do not trade."""


def ema(values: list[Decimal], period: int) -> Decimal:
    if len(values) < period or period < 1:
        raise ValueError("Not enough closes for EMA")
    k = Decimal(2) / Decimal(period + 1)
    acc = sum(values[:period], Decimal("0")) / Decimal(period)
    for value in values[period:]:
        acc = value * k + acc * (1 - k)
    return acc


def rsi(closes: list[Decimal], period: int = 14) -> Decimal:
    if len(closes) <= period:
        raise ValueError("Not enough closes for RSI")
    gains = Decimal("0")
    losses = Decimal("0")
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains += max(delta, Decimal("0"))
        losses += max(-delta, Decimal("0"))
    avg_gain = gains / Decimal(period)
    avg_loss = losses / Decimal(period)
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, Decimal("0"))) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + max(-delta, Decimal("0"))) / Decimal(period)
    if avg_loss == 0:
        return Decimal("100")
    rs = avg_gain / avg_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + rs))


def atr_fraction(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], period: int = 14) -> Decimal:
    if not (len(highs) == len(lows) == len(closes)) or len(closes) <= period:
        raise ValueError("Not enough candles for ATR")
    trs: list[Decimal] = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    acc = sum(trs[:period], Decimal("0")) / Decimal(period)
    for tr in trs[period:]:
        acc = (acc * (period - 1) + tr) / Decimal(period)
    if closes[-1] <= 0:
        raise ValueError("Non-positive close")
    return acc / closes[-1]


def closed_candles(raw: list, *, now: datetime) -> list[dict]:
    """Drop the still-forming candle. Binance kline arrays are oldest first."""
    if not isinstance(raw, list) or len(raw) < 2:
        raise MarketDataError("Kline payload is not a candle list")
    candles = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 8:
            raise MarketDataError("Kline row is incomplete")
        close_ms = int(row[6])
        close_at = datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc)
        if close_at > now:
            continue
        candles.append(
            {
                "close_at": close_at,
                "high": positive(row[2], "high", allow_zero=False),
                "low": positive(row[3], "low", allow_zero=False),
                "close": positive(row[4], "close", allow_zero=False),
                "quote_volume": positive(row[7], "quote volume"),
            }
        )
    if len(candles) < 60:
        raise MarketDataError("Not enough closed candles")
    return candles


def analyze(
    symbol: str,
    candles: list[dict],
    *,
    quote_volume_24h: Decimal,
    min_notional: Decimal,
    quote_precision: int,
    tradable: bool,
    cfg: Settings,
    now: datetime,
) -> MarketEvidence:
    closes = [c["close"] for c in candles]
    last = closes[-1]
    as_of = candles[-1]["close_at"]
    age = (now - as_of).total_seconds()
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    momentum = (last / closes[-11]) - Decimal("1")
    rsi_value = rsi(closes)
    volatility = atr_fraction(
        [c["high"] for c in candles],
        [c["low"] for c in candles],
        closes,
    )
    if volatility > cfg.max_volatility:
        regime = "high_volatility"
    elif fast > slow and last > fast and momentum > 0:
        regime = "trend_up"
    elif fast < slow and last < fast and momentum < 0:
        regime = "trend_down"
    else:
        regime = "range"
    reason = "ok"
    passed = True
    if not tradable:
        passed, reason = False, "market_not_trading"
    elif age < 0 or age > cfg.max_signal_age * 60:
        passed, reason = False, "stale_market_data"
    elif quote_volume_24h < cfg.min_quote_volume:
        passed, reason = False, "illiquid"
    elif regime != "trend_up":
        passed, reason = False, regime
    elif not cfg.rsi_min <= rsi_value <= cfg.rsi_max:
        passed, reason = False, "rsi_outside_band"
    elif min_notional <= 0:
        passed, reason = False, "missing_min_notional"
    return MarketEvidence(
        symbol=symbol,
        as_of=as_of,
        last_price=last,
        quote_volume=quote_volume_24h,
        trend="up" if fast > slow else "down" if fast < slow else "flat",
        momentum=momentum,
        rsi=rsi_value,
        volatility=volatility,
        regime=regime,
        min_notional=min_notional,
        quote_precision=quote_precision,
        tradable=tradable,
        passed=passed,
        reason=reason,
    )


def quantize_quote(amount: Decimal, precision: int) -> Decimal:
    if precision < 0 or precision > 18:
        raise ValueError("Invalid quote precision")
    step = Decimal("1").scaleb(-precision)
    return amount.quantize(step, rounding=ROUND_DOWN)


class MarketData:
    def __init__(self, url: str, cfg: Settings, *, transport=None):
        self.cfg = cfg
        self.http = httpx.Client(
            base_url=url.rstrip("/"),
            timeout=15,
            transport=transport,
            headers={"User-Agent": "warp3c-observe", "Accept": "application/json"},
        )

    def close(self) -> None:
        self.http.close()

    def scan(self, symbols: tuple[str, ...], *, now: datetime | None = None) -> dict[str, MarketEvidence]:
        moment = now or datetime.now(timezone.utc)
        evidence = {}
        for symbol in symbols:
            evidence[symbol] = self._one(symbol, moment)
        return evidence

    def _get(self, path: str, params: dict) -> object:
        try:
            response = self.http.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MarketDataError(f"Market data {path} failed") from exc

    def _one(self, symbol: str, now: datetime) -> MarketEvidence:
        rules = self._rules(symbol)
        ticker = self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        if not isinstance(ticker, dict):
            raise MarketDataError("Ticker payload is not an object")
        klines = self._get("/api/v3/klines", {"symbol": symbol, "interval": "15m", "limit": 120})
        candles = closed_candles(klines, now=now)
        return analyze(
            symbol,
            candles,
            quote_volume_24h=positive(ticker.get("quoteVolume"), "24h quote volume"),
            min_notional=rules["min_notional"],
            quote_precision=rules["quote_precision"],
            tradable=rules["tradable"],
            cfg=self.cfg,
            now=now,
        )

    def _rules(self, symbol: str) -> dict:
        payload = self._get("/api/v3/exchangeInfo", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise MarketDataError("exchangeInfo payload is not an object")
        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            raise MarketDataError("exchangeInfo has no symbols")
        match = [row for row in symbols if isinstance(row, dict) and row.get("symbol") == symbol]
        if len(match) != 1:
            raise MarketDataError("exchangeInfo did not return the requested symbol")
        row = match[0]
        if row.get("quoteAsset") != "USDT" or row.get("isSpotTradingAllowed") is not True:
            raise MarketDataError("Symbol is not a spot USDT market")
        precision = row.get("quoteAssetPrecision", row.get("quotePrecision"))
        if not isinstance(precision, int):
            raise MarketDataError("Quote precision missing")
        min_notional = None
        for item in row.get("filters") or []:
            if not isinstance(item, dict):
                continue
            if item.get("filterType") in {"NOTIONAL", "MIN_NOTIONAL"} and item.get("minNotional") is not None:
                min_notional = positive(item["minNotional"], "min notional", allow_zero=False)
        if min_notional is None:
            raise MarketDataError("Minimum notional filter missing")
        return {
            "tradable": row.get("status") == "TRADING",
            "quote_precision": precision,
            "min_notional": min_notional,
        }
