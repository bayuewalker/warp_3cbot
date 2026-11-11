import base64
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.models import Account, Signal
from warp3c.risk import GlobalRisk, committed_quote, dca_ladder_factor, make_trade_payload
from warp3c.state import OrderJournal
from warp3c.threecommas import IntegrationError, ThreeCommas
from warp3c.vibe import VibeResearch


def sig(symbol="BTCUSDT", confidence="0.9"):
    return Signal(symbol, "BUY", Decimal(confidence), datetime.now(timezone.utc),
                  "verified feed at timestamp", "Fresh momentum with volume confirmation")


def strategy(symbol="SOLUSDT", client_id="warp3c-12345678901234567890123456789012"):
    return {
        "clientId": client_id, "strategyId": "abc", "createdBy": "open_api",
        "pair": symbol, "exchange": {"code": "BINANCE", "pairType": "spot", "pairSymbols": {"quote": "USDT"}},
        "profileStrategies": [{"profile": {"code": "p123"}}],
        "initFixedAmount": 20, "initFixedAmountSymbol": "quote",
        "extraOrderCount": 4, "extraOrderVolumeMultiplier": 1.5,
        "applyDcaForFirstSafetyOrder": False, "totalEntryCost": 20,
    }


class ConfigTests(unittest.TestCase):
    def test_no_live_without_ack(self):
        with self.assertRaises(ValueError):
            Settings(mode="live", api_key="x", api_secret="y", profile_code="p123")

    def test_bad_reserve(self):
        with self.assertRaises(ValueError):
            Settings(max_committed=Decimal("0.9"), min_reserve=Decimal("0.5"))


class RiskTests(unittest.TestCase):
    def test_ladder_reserves_full_safety_orders(self):
        self.assertEqual(dca_ladder_factor(4, Decimal("1.5")), Decimal("5.75"))
        self.assertEqual(dca_ladder_factor(4, Decimal("1.5"), order_averaging=True), Decimal("8.125"))
        self.assertEqual(committed_quote(strategy()), Decimal("115"))

    def test_dca_plans_respect_global_cap(self):
        cfg = Settings(max_base=Decimal("100"))
        account = Account("p123", True, Decimal("1000"), Decimal("0"), Decimal("1000"))
        plans = GlobalRisk(cfg).plan(account, [], [sig("BTCUSDT"), sig("ETHUSDT")])
        self.assertEqual(len(plans), 2)
        self.assertLessEqual(sum((p.full_commitment for p in plans), Decimal("0")), Decimal("400"))
        self.assertTrue(all(p.full_commitment <= Decimal("150") for p in plans))

    def test_external_strategy_blocks(self):
        account = Account("p123", True, Decimal("1000"), Decimal("0"), Decimal("1000"))
        with self.assertRaises(IntegrationError):
            GlobalRisk(Settings()).plan(account, [strategy(client_id="outside-bot")], [sig()])

    def test_duplicate_active_pair_is_skipped(self):
        account = Account("p123", True, Decimal("1000"), Decimal("0"), Decimal("1000"))
        plans = GlobalRisk(Settings()).plan(account, [strategy(symbol="BTCUSDT")], [sig()])
        self.assertEqual(plans, [])

    def test_payload_is_spot_quote_and_limited(self):
        cfg = Settings(profile_code="p123")
        plan = GlobalRisk(cfg).plan(Account("p123", True, Decimal("1000"), 0, Decimal("1000")), [], [sig()])[0]
        payload = make_trade_payload(cfg, plan, "warp3c-" + "0" * 32)
        self.assertEqual(payload["amountPerTradeType"], "quote")
        self.assertEqual(payload["side"], "long")
        self.assertEqual(payload["takeProfits"][0]["portfolio"], 1)


class BoundaryTests(unittest.TestCase):
    def test_stale_signal_is_rejected(self):
        raw = {"symbol": "BTCUSDT", "action": "BUY", "confidence": 0.9,
               "as_of": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
               "source": "actual source", "thesis": "sufficiently long thesis"}
        with self.assertRaises(ValueError):
            Signal.parse(raw, {"BTCUSDT"}, max_age_minutes=30)

    def test_hmac_matches_official_contract(self):
        api = ThreeCommas("https://trade.3commas.io", "key", "secret",
                          transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[])))
        path = "/open_api/api_profiles?exchanges=BINANCE"
        signature = api.signed_headers("GET", path, timestamp="12345")["X-Signature"]
        message = "GET\n" + path + "\n12345\n60000\n"
        expected = base64.b64encode(hmac.new(b"secret", message.encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(signature, expected)
        api.close()

    def test_profile_and_live_adapters(self):
        def handler(req):
            if req.url.path.endswith("api_profiles"):
                return httpx.Response(200, json=[{"code": "p123", "status": "active", "demo": True,
                                                   "exchange": {"code": "BINANCE", "name": "BINANCE-SPOT"},
                                                   "balance": {"assets": {"USDT": {"free": 950, "used": 50, "total": 1000}}}}])
            return httpx.Response(200, json=[])
        api = ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))
        self.assertEqual(api.get_account("p123").free_usdt, Decimal("950"))
        self.assertEqual(api.get_live(), [])
        api.close()

    def test_vibe_reads_linked_attempt_only(self):
        data = {"decisions": [{"symbol": "BTCUSDT", "action": "BUY", "confidence": 0.95,
                               "as_of": datetime.now(timezone.utc).isoformat(),
                               "source": "verified Binance candles", "thesis": "Trend breakout on volume"}]}
        def handler(req):
            if req.url.path == "/sessions" and req.method == "POST":
                return httpx.Response(201, json={"session_id": "s1"})
            if req.method == "POST":
                return httpx.Response(200, json={"attempt_id": "a1"})
            return httpx.Response(200, json=[{"role": "assistant", "linked_attempt_id": "a1",
                                              "tool_trail": [{"tool": "get_market_data", "status": "ok"}],
                                              "content": json.dumps(data)}])
        vibe = VibeResearch("http://localhost:8899", transport=httpx.MockTransport(handler))
        self.assertEqual(vibe.research(("BTCUSDT",), 30)[0].symbol, "BTCUSDT")
        vibe.close()


class JournalTests(unittest.TestCase):
    def test_unknown_order_blocks_until_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OrderJournal(os.path.join(tmp, "store.db"))
            client_id = "warp3c-" + "0" * 32
            store.reserve(client_id, "BTCUSDT", {"hello": "world"})
            self.assertEqual(store.reconcile([]), [client_id])
            self.assertTrue(store.recently_submitted("BTCUSDT"))
            self.assertEqual(store.reconcile([{"clientId": client_id, "strategyId": "confirmed123"}]), [])
            store.close()

    def test_observe_mode_never_places_orders(self):
        class Broker:
            def get_account(self, code, exchange_code):
                return Account(code, False, Decimal("1000"), Decimal("0"), Decimal("1000"))
            def get_live(self):
                return []
            def create_strategy(self, payload):
                raise AssertionError("observe should not create trades")
        class Research:
            def research(self, symbols, age):
                return [sig()]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"), api_key="read", api_secret="read", profile_code="p123")
            journal = OrderJournal(cfg.db_path)
            self.assertEqual(Engine(cfg, Broker(), Research(), journal).cycle(), 1)
            self.assertEqual(journal.unresolved(), [])
            journal.close()


if __name__ == "__main__":
    unittest.main()
