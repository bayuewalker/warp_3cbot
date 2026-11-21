"""Regression tests for the observe-mode Vibe screening/risk handoff.

Offline fixtures only. No live 3Commas orders, TradingView HTTP access, or API keys.
"""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
from test_core import evidence, sig

from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.market import MarketDataError
from warp3c.models import Account, AssetBalance
from warp3c.operator import _card_fresh
from warp3c.risk import signal_executable
from warp3c.screener import Screener, ScreenerError, ScreenerFilters
from warp3c.state import OrderJournal
from warp3c.universe import Candidate, classify


class SafeVibePipelineTests(unittest.TestCase):
    def test_retained_card_requires_both_fresh_signal_and_candle(self):
        now = datetime.now(timezone.utc)
        signal_time = (now - timedelta(minutes=20)).isoformat()
        stale_candle = (now - timedelta(minutes=35)).isoformat()
        recent_candle = (now - timedelta(minutes=10)).isoformat()
        card = {"as_of": signal_time, "evidence_as_of": stale_candle}
        self.assertFalse(_card_fresh(card, now, 30))
        self.assertFalse(_card_fresh({"as_of": signal_time}, now, 30))
        self.assertTrue(_card_fresh({**card, "evidence_as_of": recent_candle}, now, 30))

    def test_missing_scanner_does_not_substitute_binance(self):
        values = ScreenerFilters.from_mapping({"rsi_min": "50", "rsi_max": "65"})
        called = {"binance": 0}

        def binance(_request):
            called["binance"] += 1
            return httpx.Response(200, json=[])

        screener = Screener(
            "",
            "https://data-api.binance.vision",
            market_transport=httpx.MockTransport(binance),
        )
        try:
            with self.assertRaisesRegex(ScreenerError, "not configured"):
                screener.scan(values)
            self.assertEqual(called["binance"], 0)
        finally:
            screener.close()
        with self.assertRaises(ValueError):
            ScreenerFilters.from_mapping({"rsi_min": "50"}).require_binance_filters()

    def test_stale_signal_rejected_even_if_market_snapshot_is_fresh(self):
        now = datetime.now(timezone.utc)
        current = evidence("BTCUSDT")
        old_signal = replace(
            sig("BTCUSDT"),
            as_of=now - timedelta(minutes=45),
            evidence=replace(current, as_of=now),
        )
        self.assertFalse(signal_executable(old_signal, Settings(), now))
        fresh = replace(old_signal, as_of=now)
        self.assertTrue(signal_executable(fresh, Settings(), now))

    def test_static_mode_obeys_global_denylist(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OrderJournal(str(Path(tmp) / "warp.db"))
            try:
                cfg = Settings(
                    db_path=str(Path(tmp) / "warp.db"),
                    discovery="static",
                    symbols=("BTCUSDT", "ETHUSDT"),
                    deny_symbols=("BTCUSDT",),
                )
                engine = Engine(cfg, object(), object(), store, object())
                self.assertEqual(
                    engine._select_universe(datetime.now(timezone.utc)), ("ETHUSDT",)
                )
            finally:
                store.close()

    def test_stale_held_asset_quote_fails_closed_and_refreshes_if_available(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = OrderJournal(str(Path(tmp) / "warp.db"))
            try:
                cfg = Settings(db_path=str(Path(tmp) / "warp.db"))

                class Market:
                    def __init__(self):
                        self.can_quote = False
                        self.queries = 0

                    def last_price(self, symbol):
                        self.queries += 1
                        self.assert_symbol = symbol
                        if not self.can_quote:
                            raise MarketDataError("exchange offline")
                        return Decimal("98")

                market = Market()
                engine = Engine(cfg, object(), object(), store, market)
                engine.evidence = {
                    "SOLUSDT": replace(
                        evidence("SOLUSDT"),
                        as_of=now - timedelta(hours=2),
                        last_price=Decimal("100"),
                    )
                }
                account = Account(
                    "demo", True, Decimal("500"), Decimal("0"), Decimal("600"),
                    assets={
                        "USDT": AssetBalance(Decimal("500"), Decimal("0"), Decimal("500")),
                        "SOL": AssetBalance(Decimal("0"), Decimal("1"), Decimal("1")),
                    },
                )
                prices, reason = engine._price_map(account, [], now)
                self.assertEqual(reason, "mark_unavailable")
                self.assertNotEqual(prices.get("SOLUSDT"), Decimal("98"))
                market.can_quote = True
                prices, reason = engine._price_map(account, [], now)
                self.assertIsNone(reason)
                self.assertEqual(prices["SOLUSDT"], Decimal("98"))
                self.assertEqual(market.assert_symbol, "SOLUSDT")
                self.assertEqual(market.queries, 2)
            finally:
                store.close()

    def test_tradingview_screener_excludes_bstocks_and_does_not_use_binance(self):
        called = {"binance": 0}

        def scanner(request):
            self.assertTrue(request.url.path.endswith("/crypto/scan"))
            return httpx.Response(200, json={"data": [
                {"s": "BINANCE:BTCUSDT", "d": ["BTCUSDT", 90000, 2, 1, 0.4, 55, 80000000]},
                {"s": "BINANCE:SOXLBUSDT", "d": ["SOXLBUSDT", 40, 3, 1, 0.2, 60, 90000000]},
                {"s": "BINANCE:RWUSDUSDT", "d": ["RWUSDUSDT", 1, 0, 1, 0, 50, 70000000]},
            ]})

        def binance(_request):
            called["binance"] += 1
            return httpx.Response(200, json=[])

        screener = Screener(
            "https://scanner.tradingview.com",
            "https://data-api.binance.vision",
            scanner_transport=httpx.MockTransport(scanner),
            market_transport=httpx.MockTransport(binance),
        )
        try:
            hits = screener.scan(ScreenerFilters())
            self.assertEqual(screener.source, "tradingview")
            self.assertEqual([hit.symbol for hit in hits], ["BTCUSDT"])
            self.assertEqual(hits[0].rsi, Decimal("55"))
            self.assertTrue(screener.fetched_at)
            self.assertEqual(called["binance"], 0)
            down = Screener(
                "https://scanner.tradingview.com",
                "https://data-api.binance.vision",
                scanner_transport=httpx.MockTransport(lambda _request: httpx.Response(503, json={})),
                market_transport=httpx.MockTransport(binance),
            )
            try:
                with self.assertRaisesRegex(ScreenerError, "TradingView"):
                    down.scan(ScreenerFilters())
            finally:
                down.close()
            self.assertEqual(called["binance"], 0)
        finally:
            screener.close()

    def test_auto_universe_excludes_stables_and_leveraged(self):
        def candidate(symbol):
            return Candidate(
            symbol=symbol,
            quote_volume=Decimal("25000000"),
            spread=Decimal("0.001"),
            tradable=True,
            min_notional=Decimal("5"),
            quote_precision=2,
        )
        opts = {
            "allow": set(), "deny": set(),
            "min_volume": Decimal("1000000"),
            "max_spread": Decimal("0.005"),
        }
        self.assertEqual(classify(candidate("USDCUSDT"), **opts), "excluded_spot_instrument")
        self.assertEqual(classify(candidate("BTCUPUSDT"), **opts), "excluded_spot_instrument")
        self.assertEqual(classify(candidate("SOXLBUSDT"), **opts), "excluded_spot_instrument")
        self.assertEqual(classify(candidate("NVDABUSDT"), **opts), "excluded_spot_instrument")
        self.assertEqual(classify(candidate("UUSDT"), **opts), "excluded_spot_instrument")
        self.assertEqual(classify(candidate("RWUSDUSDT"), **opts), "excluded_spot_instrument")
        self.assertEqual(classify(candidate("CRVUSDUSDT"), **opts), "excluded_spot_instrument")
        self.assertEqual(classify(candidate("USUALUSDT"), **opts), "eligible")
        self.assertEqual(classify(candidate("JUPUSDT"), **opts), "eligible")
        self.assertEqual(classify(candidate("BTCUSDT"), **opts), "eligible")
        self.assertEqual(classify(candidate("ARBUSDT"), **opts), "eligible")
        self.assertEqual(classify(candidate("BNBUSDT"), **opts), "eligible")
        self.assertEqual(classify(candidate("SHIBUSDT"), **opts), "eligible")
        tagged = Candidate(
            "ZZZUSDT",
            Decimal("25000000"),
            Decimal("0.001"),
            True,
            Decimal("5"),
            2,
            True,
        )
        self.assertEqual(classify(tagged, **opts), "excluded_spot_instrument")

    def test_legacy_scanner_config_can_be_omitted(self):
        self.assertEqual(Settings().screener_url, "https://scanner.tradingview.com")
        with self.assertRaises(ValueError):
            Settings(screener_url="http://scanner.tradingview.com")


if __name__ == "__main__":
    unittest.main()
