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

    def test_unsupported_legacy_tradingview_filters_fail_closed(self):
        values = ScreenerFilters.from_mapping({"rsi_min": "50", "rsi_max": "65"})
        with self.assertRaisesRegex(ScreenerError, "Unsupported Binance"):
            Screener("", "https://data-api.binance.vision").scan(values)
        normalized = ScreenerFilters.from_mapping({"min_quote_volume": "2500000", "limit": 5})
        normalized.require_binance_filters()

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

    def test_binance_screener_does_not_call_tradingview(self):
        called = {"scanner": 0, "binance": 0}

        def blocked_scanner(request):
            called["scanner"] += 1
            raise AssertionError("Unlicensed TradingView scanner endpoint contacted")

        def binance(request):
            called["binance"] += 1
            self.assertEqual(request.url.path, "/api/v3/ticker/24hr")
            return httpx.Response(200, json=[
                {
                    "symbol": "BTCUSDT",
                    "lastPrice": "90000",
                    "quoteVolume": "80000000",
                    "priceChangePercent": "2",
                },
            ])

        screener = Screener(
            "https://scanner.tradingview.com",
            "https://data-api.binance.vision",
            scanner_transport=httpx.MockTransport(blocked_scanner),
            market_transport=httpx.MockTransport(binance),
        )
        try:
            hits = screener.scan(ScreenerFilters())
            self.assertEqual(screener.source, "binance")
            self.assertEqual([hit.symbol for hit in hits], ["BTCUSDT"])
            self.assertEqual(called, {"scanner": 0, "binance": 1})
            with self.assertRaises(ScreenerError):
                screener._tradingview(ScreenerFilters())
        finally:
            screener.close()

    def test_auto_universe_excludes_stables_and_leveraged(self):
        candidate = lambda symbol: Candidate(
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
        self.assertEqual(classify(candidate("JUPUSDT"), **opts), "eligible")
        self.assertEqual(classify(candidate("BTCUSDT"), **opts), "eligible")

    def test_legacy_scanner_config_can_be_omitted(self):
        self.assertEqual(Settings().screener_url, "")
        with self.assertRaises(ValueError):
            Settings(screener_url="http://scanner.tradingview.com")


if __name__ == "__main__":
    unittest.main()
