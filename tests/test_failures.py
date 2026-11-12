import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from test_core import evidence, sig, strategy

from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.market import MarketDataError
from warp3c.models import Account, EquityMark
from warp3c.risk import GlobalRisk, circuit_reason
from warp3c.state import OrderJournal
from warp3c.threecommas import IntegrationError, StrategyNotFound
from warp3c.vibe import ResearchError


class ExplodingBroker:
    def __init__(self):
        self.creates = 0
        self.reads = 0

    def get_account(self, code, exchange_code):
        self.reads += 1
        return Account(code, True, Decimal("1000"), Decimal("0"), Decimal("1000"))

    def get_live(self):
        return []

    def get_strategy(self, client_id):
        raise StrategyNotFound(client_id)

    def create_strategy(self, payload):
        self.creates += 1
        raise IntegrationError("timeout after accept window", uncertain=True)


class Research:
    def research(self, symbols, age):
        return [sig(symbol) for symbol in symbols[:1]]


class Markets:
    def scan(self, symbols, now=None):
        return {symbol: evidence(symbol) for symbol in symbols}


class StaleMarkets:
    def scan(self, symbols, now=None):
        old = evidence(symbols[0])
        stale = type(old)(
            old.symbol, datetime.now(timezone.utc) - timedelta(hours=2), old.last_price, old.quote_volume,
            old.trend, old.momentum, old.rsi, old.volatility, old.regime, old.min_notional,
            old.quote_precision, old.tradable, False, "stale_market_data",
        )
        return {symbols[0]: stale}


class DownBroker(ExplodingBroker):
    def get_account(self, code, exchange_code):
        raise IntegrationError("exchange disconnected", uncertain=True)


def paper(tmp):
    return Settings(
        mode="paper", db_path=os.path.join(tmp, "warp.db"),
        api_key="k", api_secret="s", profile_code="p123",
    )


class FailureTests(unittest.TestCase):
    def test_timeout_then_restart_does_not_submit_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            broker = ExplodingBroker()
            engine = Engine(cfg, broker, Research(), journal, Markets())
            with self.assertRaises(IntegrationError):
                engine.cycle()
            self.assertEqual(broker.creates, 1)
            self.assertEqual(len(journal.unresolved()), 1)
            restarted = Engine(cfg, broker, Research(), journal, Markets())
            with self.assertRaises(IntegrationError):
                restarted.cycle()
            self.assertEqual(broker.creates, 1)
            journal.close()

    def test_duplicate_signal_and_insufficient_capital_do_not_order(self):
        account = Account("p123", True, Decimal("30"), Decimal("0"), Decimal("30"))
        plans = GlobalRisk(Settings()).plan(account, [], [sig(), sig("ETHUSDT")], equity=Decimal("30"))
        self.assertEqual(plans, [])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)

            class DupResearch:
                def research(self, symbols, age):
                    raise ResearchError("Duplicate symbols in AI response")

            broker = ExplodingBroker()
            with self.assertRaises(ResearchError):
                Engine(cfg, broker, DupResearch(), journal, Markets()).cycle()
            self.assertEqual(broker.creates, 0)
            journal.close()

    def test_exchange_disconnect_stale_data_and_missing_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            broker = ExplodingBroker()

            class DeadMarket:
                def scan(self, symbols, now=None):
                    raise MarketDataError("exchange disconnected")

            with self.assertRaises(MarketDataError):
                Engine(cfg, broker, Research(), journal, DeadMarket()).cycle()
            self.assertEqual(broker.creates, 0)

            broker = ExplodingBroker()
            self.assertEqual(Engine(cfg, broker, Research(), journal, StaleMarkets()).cycle(), 0)
            self.assertEqual(broker.creates, 0)

            class Missing:
                def get_account(self, code, exchange_code):
                    return Account(code, True, Decimal("1000"), Decimal("0"), Decimal("1000"))

                def get_live(self):
                    row = strategy()
                    row.pop("extraOrderCount")
                    return [row]

                def get_strategy(self, client_id):
                    raise StrategyNotFound(client_id)

                def create_strategy(self, payload):
                    raise AssertionError("missing ladder must not create an order")

            with self.assertRaises(IntegrationError):
                Engine(cfg, Missing(), Research(), journal, Markets()).cycle()
            journal.close()

    def test_daily_loss_and_halt_block_entries(self):
        now = datetime.now(timezone.utc)
        marks = [EquityMark(now - timedelta(minutes=5), Decimal("1000"), True)]
        reason = circuit_reason(marks, Decimal("900"), True, Settings(), now)
        self.assertEqual(reason, "daily_loss")
        account = Account("p123", True, Decimal("900"), Decimal("0"), Decimal("900"))
        decision = GlobalRisk(Settings()).assess(account, [], [sig()], prices={"BTCUSDT": Decimal("100")}, marks=marks)
        self.assertFalse(decision.execution_permitted)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            open(os.path.join(tmp, "HALT"), "w").close()
            broker = ExplodingBroker()
            self.assertEqual(Engine(cfg, broker, Research(), journal, Markets()).cycle(), 0)
            self.assertGreaterEqual(broker.reads, 1)
            self.assertEqual(broker.creates, 0)
            journal.close()

    def test_definite_broker_rejection_can_be_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)

            class RejectOnce(ExplodingBroker):
                def create_strategy(self, payload):
                    self.creates += 1
                    if self.creates == 1:
                        raise IntegrationError("rejected", uncertain=False, status_code=400)
                    return {"strategyId": "ok", "clientId": payload["clientId"]}

            broker = RejectOnce()
            engine = Engine(cfg, broker, Research(), journal, Markets())
            with self.assertRaises(IntegrationError):
                engine.cycle()
            self.assertEqual(journal.unresolved(), [])
            self.assertEqual(Engine(cfg, broker, Research(), journal, Markets()).cycle(), 1)
            self.assertEqual(broker.creates, 2)
            journal.close()

    def test_drawdown_blocks_a_recovered_peak(self):
        now = datetime.now(timezone.utc)
        marks = [EquityMark(now - timedelta(days=2), Decimal("1000"), True)]
        self.assertEqual(circuit_reason(marks, Decimal("800"), True, Settings(), now), "drawdown")
        self.assertIsNone(circuit_reason([], Decimal("1000"), True, Settings(), now))
        self.assertEqual(circuit_reason([], None, False, Settings(), now), "mtm_unreliable")

    def test_liveness_does_not_mean_trading_is_allowed(self):
        from warp3c.__main__ import HEALTH, HealthHandler

        previous = dict(HEALTH)
        HEALTH.update({
            "alive": True, "dependencies_healthy": False, "market_data_fresh": False,
            "execution_permitted": False, "mode": "observe", "last_cycle_ok": False, "reason": "halt",
        })
        server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_address[1]
            conn = HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                conn.request("GET", "/live")
                live = conn.getresponse()
                live.read()
                self.assertEqual(live.status, 200)
                conn.request("GET", "/ready")
                ready = conn.getresponse()
                ready.read()
                self.assertEqual(ready.status, 503)
                conn.request("GET", "/health")
                health = conn.getresponse()
                body = json.loads(health.read().decode())
                self.assertEqual(health.status, 200)
                self.assertFalse(body["execution_permitted"])
            finally:
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
            HEALTH.update(previous)
