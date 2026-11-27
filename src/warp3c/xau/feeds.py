"""Public candle, spot, and DXY reads. Own httpx clients, timeout 10s. No secrets."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import httpx

from .candles import Bar

D = Decimal
BINANCE = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com"
OKX = "https://www.okx.com"
TV = "https://scanner.tradingview.com"
YAHOO = "https://query1.finance.yahoo.com"

_BINANCE_TF = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
_BYBIT_TF = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
_OKX_TF = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")


class FeedError(RuntimeError):
    """One source failed. The caller tries the next name."""


class FeedUnavailable(RuntimeError):
    """No candle source answered. The desk stays quiet and DCA is untouched."""


def _dec(value: object) -> D:
    return D(str(value))


def _ms(value: object) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def parse_binance(payload: object, timeframe: str) -> list[Bar]:
    """Oldest-first klines. The caller drops the still-open bar."""
    if not isinstance(payload, list):
        raise FeedError("binance klines")
    bars: list[Bar] = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            raise FeedError("binance kline row")
        bars.append(
            Bar(_ms(row[0]), _dec(row[1]), _dec(row[2]), _dec(row[3]), _dec(row[4]), _dec(row[5]), timeframe)
        )
    return bars


def parse_bybit(payload: object, timeframe: str) -> list[Bar]:
    """Newest-first list. Reversed to oldest-first."""
    if not isinstance(payload, dict):
        raise FeedError("bybit kline")
    rows = payload.get("result", {}).get("list") if isinstance(payload.get("result"), dict) else None
    if not isinstance(rows, list):
        raise FeedError("bybit kline list")
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise FeedError("bybit kline row")
        bars.append(
            Bar(_ms(row[0]), _dec(row[1]), _dec(row[2]), _dec(row[3]), _dec(row[4]), _dec(row[5]), timeframe)
        )
    bars.reverse()
    return bars


def parse_okx(payload: object, timeframe: str) -> list[Bar]:
    """Newest-first candles. confirm=0 is the open bar and is left for the closed-bar filter."""
    if not isinstance(payload, dict):
        raise FeedError("okx candles")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise FeedError("okx candle list")
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise FeedError("okx candle row")
        if len(row) >= 9 and str(row[8]) == "0":
            continue
        bars.append(
            Bar(_ms(row[0]), _dec(row[1]), _dec(row[2]), _dec(row[3]), _dec(row[4]), _dec(row[5]), timeframe)
        )
    bars.reverse()
    return bars


def parse_spot(payload: object) -> D:
    if not isinstance(payload, dict):
        raise FeedError("spot")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise FeedError("spot empty")
    row = data[0]
    if not isinstance(row, dict):
        raise FeedError("spot row")
    values = row.get("d")
    if not isinstance(values, list) or not values or values[0] is None:
        raise FeedError("spot close")
    return _dec(values[0])


def parse_dxy(payload: object) -> str:
    if not isinstance(payload, dict):
        raise FeedError("dxy")
    result = payload.get("chart", {}).get("result") if isinstance(payload.get("chart"), dict) else None
    if not isinstance(result, list) or not result:
        raise FeedError("dxy empty")
    meta = result[0].get("meta") if isinstance(result[0], dict) else None
    price = meta.get("regularMarketPrice") if isinstance(meta, dict) else None
    if price is None:
        raise FeedError("dxy price")
    return f"DXY {_dec(price)}"


class HttpFeeds:
    """One client per host. Timeout is 10 seconds. Tests inject a transport."""

    def __init__(self, primary: str, fallbacks: tuple[str, ...], *, transport: Callable[[str], httpx.BaseTransport] | None = None):
        self.order = (primary,) + tuple(name for name in fallbacks if name != primary)
        self._transport = transport
        self._clients: dict[str, httpx.Client] = {}

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def _client(self, base: str) -> httpx.Client:
        if base not in self._clients:
            transport = None if self._transport is None else self._transport(base)
            headers = {}
            if "finance.yahoo" in base:
                headers["User-Agent"] = "Mozilla/5.0"
            self._clients[base] = httpx.Client(base_url=base, timeout=10, transport=transport, headers=headers)
        return self._clients[base]

    def candles(self, timeframe: str) -> tuple[str, list[Bar]]:
        errors: list[str] = []
        for name in self.order:
            try:
                return name, self._candles_from(name, timeframe)
            except (FeedError, httpx.HTTPError):
                errors.append(name)
        raise FeedUnavailable(",".join(errors) or "none")

    def book(self) -> tuple[str, dict[str, list[Bar]]]:
        """Same source for every timeframe. The first source that answers wins."""
        errors: list[str] = []
        for name in self.order:
            try:
                bars = {timeframe: self._candles_from(name, timeframe) for timeframe in TIMEFRAMES}
            except (FeedError, httpx.HTTPError):
                errors.append(name)
                continue
            return name, bars
        raise FeedUnavailable(",".join(errors) or "none")

    def perp_now(self) -> D | None:
        """Binance USDⓈ-M last price, sampled in the same cycle as spot()."""
        try:
            response = self._client(BINANCE).get("/fapi/v1/ticker/price", params={"symbol": "XAUUSDT"})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("price") is None:
                return None
            return _dec(payload["price"])
        except (FeedError, httpx.HTTPError, ValueError):
            return None

    def spot(self) -> D | None:
        body = {"symbols": {"tickers": ["OANDA:XAUUSD"]}, "columns": ["close"]}
        try:
            response = self._client(TV).post("/cfd/scan", json=body)
            response.raise_for_status()
            return parse_spot(response.json())
        except (FeedError, httpx.HTTPError, ValueError):
            return None

    def dxy(self) -> str:
        try:
            response = self._client(YAHOO).get("/v8/finance/chart/DX-Y.NYB", params={"interval": "60m", "range": "5d"})
            response.raise_for_status()
            return parse_dxy(response.json())
        except (FeedError, httpx.HTTPError, ValueError):
            return "DXY unavailable"

    def ff_calendar(self) -> object | None:
        try:
            response = self._client("https://nfs.faireconomy.media").get("/ff_calendar_thisweek.json")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None

    def _candles_from(self, name: str, timeframe: str) -> list[Bar]:
        if name == "binance":
            response = self._client(BINANCE).get(
                "/fapi/v1/klines",
                params={"symbol": "XAUUSDT", "interval": _BINANCE_TF[timeframe], "limit": 500},
            )
            response.raise_for_status()
            return parse_binance(response.json(), timeframe)
        if name == "bybit":
            response = self._client(BYBIT).get(
                "/v5/market/kline",
                params={"category": "linear", "symbol": "XAUUSDT", "interval": _BYBIT_TF[timeframe], "limit": 500},
            )
            response.raise_for_status()
            return parse_bybit(response.json(), timeframe)
        if name == "okx":
            response = self._client(OKX).get(
                "/api/v5/market/candles",
                params={"instId": "XAU-USDT-SWAP", "bar": _OKX_TF[timeframe], "limit": 300},
            )
            response.raise_for_status()
            return parse_okx(response.json(), timeframe)
        raise FeedError(name)
