"""Public spot market data. No exchange trading credentials are used."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal

import httpx

from .config import Settings
from .models import MarketEvidence, positive
from .universe import BSTOCK_PERMISSION, Candidate, select_universe


class MarketDataError(RuntimeError):
    """Candle, ticker, or exchange rule data is missing. Do not trade."""


# Binance returns at most this many klines per request.
_KLINE_PAGE = 1000


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
    min_qty: Decimal | None = None,
    step_size: Decimal | None = None,
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
        min_qty=min_qty,
        step_size=step_size,
    )


def _ticker_index(payload: object) -> dict[str, dict]:
    if isinstance(payload, dict) and payload.get("symbol"):
        return {str(payload["symbol"]): payload}
    if not isinstance(payload, list):
        raise MarketDataError("Ticker payload is not a list")
    indexed = {}
    for row in payload:
        if isinstance(row, dict) and row.get("symbol"):
            indexed[str(row["symbol"])] = row
    return indexed


def _spread(ticker: dict) -> Decimal | None:
    if ticker.get("bidPrice") is None or ticker.get("askPrice") is None:
        return None
    try:
        bid = positive(ticker["bidPrice"], "bid")
        ask = positive(ticker["askPrice"], "ask", allow_zero=False)
    except ValueError:
        return None
    if ask <= 0 or bid > ask:
        return None
    return (ask - bid) / ask


def _min_notional(row: dict) -> Decimal:
    for item in row.get("filters") or []:
        if not isinstance(item, dict):
            continue
        if item.get("filterType") in {"NOTIONAL", "MIN_NOTIONAL"} and item.get("minNotional") is not None:
            return positive(item["minNotional"], "min notional", allow_zero=False)
    return Decimal("0")


def quantize_quote(amount: Decimal, precision: int) -> Decimal:
    if precision < 0 or precision > 18:
        raise ValueError("Invalid quote precision")
    step = Decimal("1").scaleb(-precision)
    return amount.quantize(step, rounding=ROUND_DOWN)


def _lot(row: dict) -> tuple[Decimal, Decimal]:
    for item in row.get("filters") or []:
        if isinstance(item, dict) and item.get("filterType") == "LOT_SIZE":
            return positive(item.get("minQty"), "min qty", allow_zero=False), positive(item.get("stepSize"), "step", allow_zero=False)
    raise MarketDataError("LOT_SIZE filter missing")


def _parse_klines(raw: object, now: datetime, *, minimum: int) -> list[dict]:
    if not isinstance(raw, list):
        raise MarketDataError("Kline payload is not a candle list")
    candles = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 7:
            raise MarketDataError("Kline row is incomplete")
        close_at = datetime.fromtimestamp(int(row[6]) / 1000, tz=timezone.utc)
        if close_at > now:
            continue
        candles.append(
            {
                "close_at": close_at,
                "high": positive(row[2], "high", allow_zero=False),
                "low": positive(row[3], "low", allow_zero=False),
                "close": positive(row[4], "close", allow_zero=False),
            }
        )
    if len(candles) < minimum:
        raise MarketDataError("Not enough closed candles")
    return candles


def _returns(closes: list[Decimal]) -> list[Decimal]:
    values = []
    for prev, nxt in zip(closes, closes[1:], strict=False):
        if prev <= 0:
            raise MarketDataError("Non-positive close")
        values.append((nxt - prev) / prev)
    return values


def _pearson(left: list[Decimal], right: list[Decimal]) -> Decimal | None:
    count = min(len(left), len(right))
    if count < 20:
        return None
    xs = left[-count:]
    ys = right[-count:]
    mean_x = sum(xs, Decimal("0")) / Decimal(count)
    mean_y = sum(ys, Decimal("0")) / Decimal(count)
    cov = sum(((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False)), Decimal("0"))
    var_x = sum(((x - mean_x) ** 2 for x in xs), Decimal("0"))
    var_y = sum(((y - mean_y) ** 2 for y in ys), Decimal("0"))
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / (var_x.sqrt() * var_y.sqrt())


def _correlation_table(closes: dict[str, list[Decimal]]) -> dict[tuple[str, str], Decimal]:
    returns = {symbol: _returns(series) for symbol, series in closes.items()}
    table: dict[tuple[str, str], Decimal] = {}
    names = list(returns)
    for i, left in enumerate(names):
        for right in names[i:]:
            value = Decimal("1") if left == right else _pearson(returns[left], returns[right])
            if value is None:
                raise MarketDataError("Correlation history is short")
            table[(left, right)] = value
            table[(right, left)] = value
    return table


class MarketData:
    def __init__(self, url: str, cfg: Settings, *, transport=None):
        self.cfg = cfg
        self._info_at: datetime | None = None
        self._info: dict | None = None
        self.last_candles: dict[str, list[dict]] = {}
        self.http = httpx.Client(
            base_url=url.rstrip("/"),
            timeout=15,
            transport=transport,
            headers={"User-Agent": "warp3c-observe", "Accept": "application/json"},
        )

    def close(self) -> None:
        self.http.close()

    def discover(self, *, now: datetime | None = None) -> tuple[tuple[str, ...], list[tuple[str, str]]]:
        """Rank spot USDT markets. The optional discovery allowlist is not the static fallback."""
        moment = now or datetime.now(timezone.utc)
        info = self._exchange_info_all(moment)
        tickers = self._get("/api/v3/ticker/24hr", {})
        by_symbol = _ticker_index(tickers)
        candidates: list[Candidate] = []
        for row in info.get("symbols") or []:
            if not isinstance(row, dict) or row.get("quoteAsset") != "USDT":
                continue
            if row.get("isSpotTradingAllowed") is not True:
                continue
            symbol = str(row.get("symbol") or "")
            ticker = by_symbol.get(symbol, {})
            try:
                volume = positive(ticker.get("quoteVolume"), "24h quote volume") if ticker else Decimal("0")
                spread = _spread(ticker) if ticker else None
                precision = row.get("quoteAssetPrecision", row.get("quotePrecision"))
                quote_precision = precision if isinstance(precision, int) else -1
                min_notional = _min_notional(row)
            except (ValueError, MarketDataError):
                volume = Decimal("0")
                spread = None
                quote_precision = -1
                min_notional = Decimal("0")
            permissions: set[str] = set()
            for group in row.get("permissionSets") or []:
                if isinstance(group, list):
                    permissions.update(item for item in group if isinstance(item, str))
            candidates.append(
                Candidate(
                    symbol=symbol,
                    quote_volume=volume,
                    spread=spread,
                    tradable=row.get("status") == "TRADING",
                    min_notional=min_notional,
                    quote_precision=quote_precision,
                    tokenized_equity=BSTOCK_PERMISSION in permissions,
                )
            )
        return select_universe(
            candidates,
            allow=set(self.cfg.discovery_allow),
            deny=set(self.cfg.deny_symbols),
            min_volume=self.cfg.min_quote_volume,
            max_spread=self.cfg.max_spread,
            limit=self.cfg.max_candidates,
        )

    def _exchange_info_all(self, now: datetime) -> dict:
        if self._info is not None and self._info_at is not None:
            age = (now - self._info_at).total_seconds()
            if 0 <= age < self.cfg.scan_seconds:
                return self._info
        payload = self._get("/api/v3/exchangeInfo", {})
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise MarketDataError("exchangeInfo payload is not an object")
        self._info = payload
        self._info_at = now
        return payload

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

    def _fetch_klines(self, symbol: str, interval: str, count: int) -> list:
        """Most recent ``count`` klines, oldest first. Pages backward past the exchange cap."""
        collected: list = []
        seen: set[int] = set()
        end_ms: int | None = None
        while len(collected) < count:
            limit = min(_KLINE_PAGE, count - len(collected))
            params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
            if end_ms is not None:
                params["endTime"] = end_ms
            rows = self._get("/api/v3/klines", params)
            if not isinstance(rows, list) or not rows:
                break
            older: list = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 8:
                    raise MarketDataError("Kline row is incomplete")
                open_ms = int(row[0])
                if end_ms is not None and (open_ms in seen or open_ms > end_ms):
                    continue
                seen.add(open_ms)
                older.append(row)
            if not older:
                break
            older.sort(key=lambda row: int(row[0]))
            collected = older + collected
            if len(rows) < limit:
                break
            end_ms = int(older[0][0]) - 1
        collected.sort(key=lambda row: int(row[0]))
        if len(collected) > count:
            return collected[-count:]
        return collected

    def _one(self, symbol: str, now: datetime) -> MarketEvidence:
        rules = self._rules(symbol)
        ticker = self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        if not isinstance(ticker, dict):
            raise MarketDataError("Ticker payload is not an object")
        need = int(getattr(self.cfg, "backtest_candles", 120) or 120)
        if need < 120:
            need = 120
        # The default request stays 120. A longer window asks for one extra raw bar
        # so the still-forming candle does not leave the closed series short.
        raw_count = 120 if need == 120 else need + 1
        klines = self._fetch_klines(symbol, "15m", raw_count)
        closed = closed_candles(klines, now=now)
        if need == 120:
            self.last_candles[symbol] = closed
            decision = closed
        else:
            # Regime and RSI stay on the same recent window the 120-candle scan used.
            decision = closed_candles(klines[-120:], now=now)
            self.last_candles[symbol] = closed[-need:] if len(closed) >= need else closed
        item = analyze(
            symbol,
            decision,
            quote_volume_24h=positive(ticker.get("quoteVolume"), "24h quote volume"),
            min_notional=rules["min_notional"],
            quote_precision=rules["quote_precision"],
            tradable=rules["tradable"],
            cfg=self.cfg,
            now=now,
            min_qty=rules["min_qty"],
            step_size=rules["step_size"],
        )
        if self.cfg.discovery == "auto" and item.passed:
            spread = _spread(ticker)
            if spread is None:
                item = replace(item, passed=False, reason="spread_unknown")
            elif spread > self.cfg.max_spread:
                item = replace(item, passed=False, reason="spread_too_wide")
        return item

    def price_at(self, symbol: str, moment: datetime) -> Decimal | None:
        """Close of the 1h candle that contains this time. Missing data stays unresolved."""
        start = int(moment.astimezone(timezone.utc).timestamp() * 1000)
        try:
            rows = self._get("/api/v3/klines", {"symbol": symbol, "interval": "1h", "startTime": start, "limit": 1})
        except MarketDataError:
            return None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], list) or len(rows[0]) < 5:
            return None
        try:
            return positive(rows[0][4], "close", allow_zero=False)
        except ValueError:
            return None

    def last_price(self, symbol: str) -> Decimal:
        """Last trade for a held asset that is outside the entry universe."""
        ticker = self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        if not isinstance(ticker, dict):
            raise MarketDataError("Ticker payload is not an object")
        return positive(ticker.get("lastPrice"), "last price", allow_zero=False)

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
        try:
            min_qty, step_size = _lot(row)
        except MarketDataError:
            # Missing LOT_SIZE is not a zero quantity. The entry check refuses it.
            min_qty, step_size = None, None
        return {
            "tradable": row.get("status") == "TRADING",
            "quote_precision": precision,
            "min_notional": min_notional,
            "min_qty": min_qty,
            "step_size": step_size,
        }

    def tradebook_context(self, symbols: tuple[str, ...], *, now: datetime | None = None) -> dict:
        """BTC regime, correlation, and spot gates. Any failed read raises; the caller must not enter."""
        moment = now or datetime.now(timezone.utc)
        tape = self._btc_tape(moment)
        needed = tuple(dict.fromkeys(("BTCUSDT", *symbols)))
        closes = self._daily_closes(needed, moment)
        corr = _correlation_table(closes)
        gates = {symbol: self._symbol_gate(symbol, moment) for symbol in symbols}
        return {"tape": tape, "correlation": corr, "gates": gates, "as_of": moment.isoformat()}

    def _btc_tape(self, now: datetime):
        from .capital import BtcTape

        four = _parse_klines(self._get("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "4h", "limit": 240}), now, minimum=200)
        hourly = _parse_klines(self._get("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1h", "limit": 800}), now, minimum=14 + 24 * 29)
        ticker = self._get("/api/v3/ticker/24hr", {"symbol": "BTCUSDT"})
        if not isinstance(ticker, dict):
            raise MarketDataError("BTC ticker missing")
        change = positive(ticker.get("priceChangePercent"), "btc change") / Decimal("100")
        closes_4h = [row["close"] for row in four]
        closes_1h = [row["close"] for row in hourly]
        slope = ema(closes_1h, 50) - ema(closes_1h[:-1], 50)
        atr_now = atr_fraction([c["high"] for c in hourly], [c["low"] for c in hourly], closes_1h, 14)
        samples = []
        for day in range(30):
            end = len(hourly) - day * 24
            if end <= 20:
                break
            window = hourly[:end]
            samples.append(
                atr_fraction([c["high"] for c in window], [c["low"] for c in window], [c["close"] for c in window], 14)
            )
        if len(samples) < 30:
            raise MarketDataError("BTC ATR history is short")
        ordered = sorted(samples)
        median = ordered[len(ordered) // 2]
        return BtcTape(closes_4h[-1], ema(closes_4h, 200), slope, change, atr_now, median)

    def _daily_closes(self, symbols: tuple[str, ...], now: datetime) -> dict[str, list]:
        cached = getattr(self, "_daily_cache", {})
        stamp = getattr(self, "_daily_at", None)
        fresh = stamp is not None and 0 <= (now - stamp).total_seconds() <= 4 * 3600
        if not fresh or any(symbol not in cached for symbol in symbols):
            loaded = {}
            for symbol in symbols:
                rows = _parse_klines(
                    self._get("/api/v3/klines", {"symbol": symbol, "interval": "1d", "limit": 40}),
                    now,
                    minimum=30,
                )
                loaded[symbol] = [row["close"] for row in rows]
            self._daily_cache = {**cached, **loaded}
            self._daily_at = now
        return {symbol: self._daily_cache[symbol] for symbol in symbols}

    def _symbol_gate(self, symbol: str, now: datetime) -> dict:
        payload = self._get("/api/v3/exchangeInfo", {"symbol": symbol})
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise MarketDataError("exchangeInfo payload is not an object")
        match = [row for row in payload["symbols"] if isinstance(row, dict) and row.get("symbol") == symbol]
        if len(match) != 1:
            raise MarketDataError("exchangeInfo did not return the requested symbol")
        info = match[0]
        if info.get("quoteAsset") != "USDT" or info.get("isSpotTradingAllowed") is not True:
            raise MarketDataError("Symbol is not a spot USDT market")
        rules = self._rules(symbol)
        lot_min, _step = _lot(info)
        ticker = self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        book = self._get("/api/v3/ticker/bookTicker", {"symbol": symbol})
        if not isinstance(ticker, dict) or not isinstance(book, dict):
            raise MarketDataError("Symbol ticker missing")
        close_ms = ticker.get("closeTime")
        if not isinstance(close_ms, int):
            raise MarketDataError("Quote timestamp missing")
        quote_age = (now - datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc)).total_seconds()
        onboard = info.get("onboardDate")
        if not isinstance(onboard, int):
            raise MarketDataError("Listing date missing")
        listed_days = (now - datetime.fromtimestamp(onboard / 1000, tz=timezone.utc)).days
        hourly = _parse_klines(
            self._get("/api/v3/klines", {"symbol": symbol, "interval": "1h", "limit": 40}),
            now,
            minimum=20,
        )
        atr = atr_fraction([c["high"] for c in hourly], [c["low"] for c in hourly], [c["close"] for c in hourly], 14)
        spread = _spread(book)
        if spread is None:
            raise MarketDataError("Spread missing")
        return {
            "tradable": rules["tradable"] and rules.get("spot") is not False,
            "on_binance_spot": True,
            "quote_volume": positive(ticker.get("quoteVolume"), "24h quote volume"),
            "spread": spread,
            "change_24h": positive(ticker.get("priceChangePercent"), "change") / Decimal("100"),
            "listed_days": listed_days,
            "min_notional": max(rules["min_notional"], lot_min * positive(book.get("askPrice"), "ask", allow_zero=False)),
            "atr_1h_pct": atr,
            "quote_age": quote_age,
            "price": positive(book.get("askPrice"), "ask", allow_zero=False),
        }
