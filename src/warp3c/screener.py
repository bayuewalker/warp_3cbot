"""Read-only spot screener. Automated discovery uses Binance public data only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx

from .models import positive

COLUMNS = ["name", "close", "change", "volume", "Recommend.All", "RSI", "24h_vol|5"]
# Quote-like bases. They dominate raw volume and are not spot swing candidates.
_STABLE_BASES = {
    "USDC",
    "FDUSD",
    "TUSD",
    "USDP",
    "DAI",
    "USD1",
    "BUSD",
    "USDE",
    "USDS",
    "RLUSD",
    "PYUSD",
    "EUR",
    "AEUR",
    "EURI",
}


class ScreenerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScreenerFilters:
    exchange: str = "BINANCE"
    quote: str = "USDT"
    min_quote_volume: Decimal = Decimal("1000000")
    min_change_pct: Decimal = Decimal("-8")
    max_change_pct: Decimal = Decimal("20")
    rsi_min: Decimal = Decimal("0")
    rsi_max: Decimal = Decimal("100")
    min_recommendation: Decimal = Decimal("-1")
    limit: int = 12

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "quote": self.quote,
            "min_quote_volume": format(self.min_quote_volume, "f"),
            "min_change_pct": format(self.min_change_pct, "f"),
            "max_change_pct": format(self.max_change_pct, "f"),
            "rsi_min": format(self.rsi_min, "f"),
            "rsi_max": format(self.rsi_max, "f"),
            "min_recommendation": format(self.min_recommendation, "f"),
            "limit": self.limit,
        }

    @classmethod
    def from_settings(cls, cfg) -> ScreenerFilters:
        # RSI and recommendation stay open here. The entry gate still applies its own band.
        return cls(min_quote_volume=cfg.min_quote_volume, limit=cfg.max_candidates)

    @classmethod
    def from_mapping(cls, raw: dict) -> ScreenerFilters:
        if not isinstance(raw, dict):
            raise ValueError("Screener filters must be an object")
        exchange = str(raw.get("exchange", "BINANCE")).upper().strip()
        quote = str(raw.get("quote", "USDT")).upper().strip()
        if exchange != "BINANCE" or quote != "USDT":
            raise ValueError("Phase 1 screens Binance USDT pairs")
        min_volume = positive(raw.get("min_quote_volume", "1000000"), "min quote volume", allow_zero=False)
        min_change = Decimal(str(raw.get("min_change_pct", "-8")))
        max_change = Decimal(str(raw.get("max_change_pct", "20")))
        rsi_min = Decimal(str(raw.get("rsi_min", "40")))
        rsi_max = Decimal(str(raw.get("rsi_max", "72")))
        recommendation = Decimal(str(raw.get("min_recommendation", "0.1")))
        limit = int(raw.get("limit", 12))
        if not min_change.is_finite() or not max_change.is_finite() or min_change >= max_change:
            raise ValueError("Invalid change band")
        if abs(min_change) > 100 or abs(max_change) > 100:
            raise ValueError("Change band is outside +/- 100")
        if rsi_min < 0 or rsi_max > 100 or rsi_min >= rsi_max:
            raise ValueError("Invalid RSI band")
        if not Decimal("-1") <= recommendation <= Decimal("1"):
            raise ValueError("Recommendation must be between -1 and 1")
        if not 1 <= limit <= 50:
            raise ValueError("Invalid screener limit")
        return cls(
            exchange, quote, min_volume, min_change, max_change, rsi_min, rsi_max, recommendation, limit
        )


@dataclass(frozen=True)
class ScreenerHit:
    symbol: str
    last: Decimal | None
    change_pct: Decimal | None
    quote_volume: Decimal
    recommendation: Decimal | None
    rsi: Decimal | None
    source: str

    def to_dict(self) -> dict:
        def text(value: Decimal | None) -> str | None:
            return None if value is None else format(value, "f")

        return {
            "symbol": self.symbol,
            "last": text(self.last),
            "change_pct": text(self.change_pct),
            "quote_volume": format(self.quote_volume, "f"),
            "recommendation": text(self.recommendation),
            "rsi": text(self.rsi),
            "source": self.source,
        }


def scan_body(filters: ScreenerFilters) -> dict:
    return {
        "columns": COLUMNS,
        "filter": [
            {"left": "exchange", "operation": "equal", "right": filters.exchange},
            {"left": "24h_vol|5", "operation": "greater", "right": float(filters.min_quote_volume)},
        ],
        "ignore_unknown_fields": True,
        "options": {"lang": "en"},
        "range": [0, max(filters.limit * 4, 20)],
        "sort": {"sortBy": "24h_vol|5", "sortOrder": "desc"},
        "symbols": {},
        "markets": ["crypto"],
    }


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    if not number.is_finite():
        return None
    return number


def _symbol_from_ticker(ticker: str) -> str | None:
    text = ticker.strip().upper()
    if ":" in text:
        exchange, text = text.split(":", 1)
        if exchange != "BINANCE":
            return None
    if not text.isalnum() or not text.endswith("USDT"):
        return None
    return text


def parse_tradingview(payload: object, *, source: str = "tradingview") -> list[ScreenerHit]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ScreenerError("TradingView scan payload is not a data object")
    hits: list[ScreenerHit] = []
    for row in payload["data"]:
        if not isinstance(row, dict):
            continue
        symbol = _symbol_from_ticker(str(row.get("s") or ""))
        values = row.get("d")
        if symbol is None or not isinstance(values, list):
            continue
        indexed = {name: values[i] if i < len(values) else None for i, name in enumerate(COLUMNS)}
        traded = _decimal(indexed.get("24h_vol|5"))
        if traded is None or traded <= 0:
            traded = _decimal(indexed.get("Value.Traded"))
        volume = _decimal(indexed.get("volume"))
        quote_volume = traded if traded is not None and traded > 0 else volume
        if quote_volume is None or quote_volume < 0:
            continue
        hits.append(
            ScreenerHit(
                symbol=symbol,
                last=_decimal(indexed.get("close")),
                change_pct=_decimal(indexed.get("change")),
                quote_volume=quote_volume,
                recommendation=_decimal(indexed.get("Recommend.All")),
                rsi=_decimal(indexed.get("RSI")),
                source=source,
            )
        )
    return hits


def parse_binance_tickers(payload: object) -> list[ScreenerHit]:
    if isinstance(payload, dict):
        rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ScreenerError("Binance ticker payload is not a list")
    hits: list[ScreenerHit] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = _symbol_from_ticker(str(row.get("symbol") or ""))
        volume = _decimal(row.get("quoteVolume"))
        if symbol is None or volume is None or volume < 0:
            continue
        hits.append(
            ScreenerHit(
                symbol=symbol,
                last=_decimal(row.get("lastPrice")),
                change_pct=_decimal(row.get("priceChangePercent")),
                quote_volume=volume,
                recommendation=None,
                rsi=None,
                source="binance",
            )
        )
    return hits


def apply_filters(hits: list[ScreenerHit], filters: ScreenerFilters) -> list[ScreenerHit]:
    kept: list[ScreenerHit] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.symbol in seen or not hit.symbol.endswith(filters.quote):
            continue
        base = hit.symbol[: -len(filters.quote)]
        if base in _STABLE_BASES:
            continue
        if hit.quote_volume < filters.min_quote_volume:
            continue
        if hit.change_pct is None or not filters.min_change_pct <= hit.change_pct <= filters.max_change_pct:
            continue
        if hit.rsi is not None and not filters.rsi_min <= hit.rsi <= filters.rsi_max:
            continue
        if hit.recommendation is not None and hit.recommendation < filters.min_recommendation:
            continue
        seen.add(hit.symbol)
        kept.append(hit)
    kept.sort(key=lambda item: item.quote_volume, reverse=True)
    return kept[: filters.limit]


def select_symbols(hits: list[ScreenerHit], *, allow: set[str], deny: set[str], limit: int) -> tuple[str, ...]:
    chosen: list[str] = []
    for hit in hits:
        if hit.symbol in deny or hit.symbol in chosen:
            continue
        if allow and hit.symbol not in allow:
            continue
        if not hit.symbol.isalnum() or not hit.symbol.endswith("USDT"):
            continue
        chosen.append(hit.symbol)
        if len(chosen) >= limit:
            break
    return tuple(chosen)


class Screener:
    """Read-only Binance screener; TradingView automation is deliberately disabled.

    The constructor keeps its legacy positional arguments for backwards
    compatibility, but does not open or call a TradingView HTTP client.
    Unofficial TradingView scanner endpoints are not a licensed bot feed.
    """

    def __init__(self, scanner_url: str, market_url: str, *, scanner_transport=None, market_transport=None):
        self.market = httpx.Client(
            base_url=market_url.rstrip("/"),
            timeout=12,
            transport=market_transport,
            headers={"User-Agent": "warp3c", "Accept": "application/json"},
        )
        self.source = ""

    def close(self) -> None:
        self.market.close()

    def scan(self, filters: ScreenerFilters) -> list[ScreenerHit]:
        """Return Binance-sourced ranking or raise; never use an unofficial TV API."""
        self.source = ""
        hits = self._binance()
        selected = apply_filters(hits, filters)
        self.source = "binance"
        return selected

    def _tradingview(self, filters: ScreenerFilters) -> list[ScreenerHit]:
        """Legacy method fails closed; cannot be used to bypass licensed data access."""
        raise ScreenerError("Automated TradingView scanner access is disabled")

    def _binance(self) -> list[ScreenerHit]:
        try:
            response = self.market.get("/api/v3/ticker/24hr")
            response.raise_for_status()
            return parse_binance_tickers(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise ScreenerError("Binance public spot screener unavailable") from exc
