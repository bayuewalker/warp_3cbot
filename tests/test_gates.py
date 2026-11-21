"""Live tradebook inputs: BTC regime, correlation, universe gates, and fail-closed entries."""

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from test_failures import ExplodingBroker, Markets, Research, paper

from warp3c.capital import BtcTape, Policy, apply_rule_edits
from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.market import MarketData, MarketDataError
from warp3c.state import OrderJournal
from warp3c.threecommas import IntegrationError

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def candles(count: int, step_ms: int) -> list:
    rows = []
    for index in range(count):
        close = NOW_MS - (count - index) * step_ms
        price = 100 + index * Decimal("0.1")
        rows.append([
            close - step_ms + 1, format(price, "f"), format(price + 1, "f"), format(price - 1, "f"),
            format(price, "f"), "10", close, "1000", 10, "1", "100", "0",
        ])
    return rows


def info(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "status": "TRADING",
        "quoteAsset": "USDT",
        "isSpotTradingAllowed": True,
        "quoteAssetPrecision": 2,
        "onboardDate": NOW_MS - 200 * 24 * 3600 * 1000,
        "filters": [
            {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
            {"filterType": "LOT_SIZE", "minQty": "0.0001", "stepSize": "0.0001"},
        ],
    }


class ContextTests(unittest.TestCase):
    def test_public_klines_feed_regime_correlation_and_gates(self):
        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            interval = req.url.params.get("interval")
            if path.endswith("/klines") and interval == "4h":
                return httpx.Response(200, json=candles(240, 4 * 3600 * 1000))
            if path.endswith("/klines") and interval == "1h":
                return httpx.Response(200, json=candles(800, 3600 * 1000))
            if path.endswith("/klines") and interval == "1d":
                return httpx.Response(200, json=candles(40, 24 * 3600 * 1000))
            if path.endswith("/exchangeInfo"):
                symbol = req.url.params.get("symbol") or "SOLUSDT"
                return httpx.Response(200, json={"symbols": [info(symbol)]})
            if path.endswith("/ticker/bookTicker"):
                return httpx.Response(200, json={"bidPrice": "100", "askPrice": "100.05"})
            if path.endswith("/ticker/24hr"):
                return httpx.Response(200, json={
                    "symbol": "BTCUSDT",
                    "priceChangePercent": "1",
                    "quoteVolume": "20000000",
                    "closeTime": NOW_MS - 1000,
                    "lastPrice": "100",
                })
            raise AssertionError(path)

        data = MarketData("https://data-api.binance.vision", Settings(), transport=httpx.MockTransport(handler))
        context = data.tradebook_context(("SOLUSDT",), now=NOW)
        self.assertIsInstance(context["tape"], BtcTape)
        self.assertIn(("BTCUSDT", "SOLUSDT"), context["correlation"])
        gate = context["gates"]["SOLUSDT"]
        self.assertGreaterEqual(gate["listed_days"], 90)
        self.assertLessEqual(gate["spread"], Decimal("0.0025"))
        self.assertGreaterEqual(gate["quote_volume"], Decimal("10000000"))
        self.assertLessEqual(gate["quote_age"], 30)
        self.assertIsNotNone(gate["atr_1h_pct"])
        self.assertGreater(gate["min_notional"], 0)
        data.close()

    def test_a_failed_feed_blocks_new_entries(self):
        class Dead(Markets):
            def tradebook_context(self, symbols, now=None):
                raise MarketDataError("binance down")

        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            broker = ExplodingBroker()
            engine = Engine(cfg, broker, Research(), journal, Dead())
            self.assertEqual(engine.cycle(), 0)
            self.assertEqual(broker.creates, 0)
            self.assertEqual(engine.last_aegis[0]["status"], "WAIT_DATA_INCOMPLETE")
            journal.close()

    def test_rule_edits_reject_a_score_outside_50_to_100(self):
        rules = Policy.from_settings(Settings())
        edited = apply_rule_edits(rules, {"min_score": 70, "sleeve_pct": "0.2"})
        self.assertEqual(edited.min_score, 70)
        self.assertEqual(edited.sleeve_pct, Decimal("0.2"))
        with self.assertRaises(ValueError):
            apply_rule_edits(rules, {"min_score": 49})

    def test_a_finished_strategy_frees_its_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            stamp = "2026-11-21T00:00:00+00:00"
            journal.insert_warp_bot({
                "warp_id": "abcd1234", "name": "WARP-abcd1234", "client_id": "warp3c-done",
                "external_code": "strat-1", "kind": "dca", "product": "strategy",
                "account_code": "p123", "market": "spot", "symbol": "SOLUSDT",
                "status": "confirmed", "commitment": "20", "created_at": stamp, "updated_at": stamp,
            })

            class Done(ExplodingBroker):
                def get_strategy(self, client_id):
                    return {"status": "completed", "clientId": client_id, "strategyId": "strat-1"}

            engine = Engine(cfg, Done(), Research(), journal, Markets())
            engine._retire_finished([])
            self.assertEqual(journal.warp_bot("abcd1234")["status"], "closed")
            journal.close()

    def test_readback_mismatch_keeps_the_accepted_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)

            class Echo(ExplodingBroker):
                def create_strategy(self, payload):
                    self.creates += 1
                    return {"strategyId": "live-1", "clientId": payload["clientId"], "stopLoss": None}

            broker = Echo()
            engine = Engine(cfg, broker, Research(), journal, Markets())
            with self.assertRaises(IntegrationError):
                engine.cycle()
            self.assertEqual(broker.creates, 1)
            self.assertEqual(journal.recent_submissions()[0]["state"], "confirmed")
            self.assertEqual(journal.warp_bots()[0]["status"], "confirmed")
            self.assertEqual(journal.warp_bots()[0]["external_code"], "live-1")
            journal.close()
