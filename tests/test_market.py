import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from warp3c.config import Settings
from warp3c.market import MarketData, MarketDataError, analyze, closed_candles


def rising(n=80):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = Decimal("100")
    for i in range(n):
        price += Decimal("0.4")
        candles.append(
            {
                "close_at": start + timedelta(minutes=15 * (i + 1)),
                "high": price + Decimal("0.1"),
                "low": price - Decimal("0.1"),
                "close": price,
                "quote_volume": Decimal("1000"),
            }
        )
    return candles


class MarketMathTests(unittest.TestCase):
    def test_rising_market_is_an_uptrend(self):
        candles = rising()
        now = candles[-1]["close_at"] + timedelta(minutes=1)
        cfg = Settings(rsi_max=Decimal("100"))
        evidence = analyze(
            "BTCUSDT", candles, quote_volume_24h=Decimal("5000000"), min_notional=Decimal("5"),
            quote_precision=2, tradable=True, cfg=cfg, now=now,
        )
        self.assertEqual(evidence.regime, "trend_up")
        self.assertTrue(evidence.passed)
        self.assertEqual(evidence.reason, "ok")

    def test_stale_candle_does_not_pass(self):
        candles = rising()
        now = candles[-1]["close_at"] + timedelta(hours=3)
        evidence = analyze(
            "BTCUSDT", candles, quote_volume_24h=Decimal("5000000"), min_notional=Decimal("5"),
            quote_precision=2, tradable=True, cfg=Settings(rsi_max=Decimal("100")), now=now,
        )
        self.assertFalse(evidence.passed)
        self.assertEqual(evidence.reason, "stale_market_data")

    def test_open_candle_is_dropped(self):
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        raw = [
            [0, "1", "2", "1", "100", "1", int((now - timedelta(minutes=15)).timestamp() * 1000), "10", 0, "0", "0", "0"]
            for _ in range(60)
        ]
        future = int((now + timedelta(minutes=14)).timestamp() * 1000)
        raw.append([future, "1", "2", "1", "999", "1", future, "10", 0, "0", "0", "0"])
        candles = closed_candles(raw, now=now)
        self.assertEqual(len(candles), 60)
        self.assertNotEqual(candles[-1]["close"], Decimal("999"))

    def test_exchange_rules_reject_a_missing_notional(self):
        def handler(req):
            if req.url.path.endswith("exchangeInfo"):
                return httpx.Response(200, json={"symbols": [{
                    "symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT",
                    "isSpotTradingAllowed": True, "quoteAssetPrecision": 2, "filters": [],
                }]})
            raise AssertionError(req.url.path)

        data = MarketData("https://api.binance.com", Settings(), transport=httpx.MockTransport(handler))
        with self.assertRaises(MarketDataError):
            data.scan(("BTCUSDT",))
        data.close()
