"""One mock pipeline across scan, risk, execution, restart, and the kill switch.

The market, research, and broker doubles are mocks. They are not a Binance
session, a Vibe-Trading session, or a 3Commas demo or live account.
"""

import os
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from test_core import evidence, sig, strategy

from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.market import MarketDataError
from warp3c.models import Account
from warp3c.state import OrderJournal
from warp3c.threecommas import IntegrationError, StrategyNotFound


class MockMarket:
    """Deterministic public-market mock."""

    def __init__(self):
        self.books = {"BTCUSDT": evidence("BTCUSDT"), "ETHUSDT": evidence("ETHUSDT")}
        self.quotes = {"SOLUSDT": Decimal("100")}
        self.missing = set()
        self.priced = []

    def scan(self, symbols, now=None):
        return {symbol: self.books[symbol] for symbol in symbols if symbol in self.books}

    def last_price(self, symbol):
        self.priced.append(symbol)
        if symbol in self.missing or symbol not in self.quotes:
            raise MarketDataError(f"mock mark missing for {symbol}")
        return self.quotes[symbol]


class MockVibe:
    """Deterministic research mock. It cannot submit an order."""

    def __init__(self):
        self.signals = [sig("BTCUSDT")]
        self.calls = 0

    def research(self, symbols, age):
        self.calls += 1
        allowed = set(symbols)
        return [item for item in self.signals if item.symbol in allowed]


class MockBroker:
    """Deterministic broker mock. Not a 3Commas demo or live account."""

    def __init__(self):
        self.reads = 0
        self.creates = []
        self.live = []
        self.found = {}
        self.fail_next = None
        self.free = Decimal("1000")
        self.total = Decimal("1000")

    def get_account(self, code, exchange_code):
        self.reads += 1
        return Account(code, True, self.free, Decimal("0"), self.total)

    def get_live(self):
        self.reads += 1
        return list(self.live)

    def get_strategy(self, client_id):
        row = self.found.get(client_id)
        if row is None:
            raise StrategyNotFound(client_id)
        return row

    def create_strategy(self, payload):
        self.creates.append(dict(payload))
        if self.fail_next == "timeout":
            self.fail_next = None
            raise IntegrationError("mock timeout", uncertain=True)
        row = strategy(payload["pairCode"], payload["clientId"])
        row["strategyId"] = f"mock-{len(self.creates)}"
        self.live.append(row)
        self.found[payload["clientId"]] = row
        return {"strategyId": row["strategyId"], "clientId": payload["clientId"]}


class PipelineTests(unittest.TestCase):
    def test_scan_risk_execution_restart_and_kill_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "warp.db")
            broker = MockBroker()
            broker.live.append(strategy("SOLUSDT"))
            market = MockMarket()
            vibe = MockVibe()
            journal = OrderJournal(path)
            wide = {"max_group": Decimal("0.90"), "max_committed": Decimal("0.70"), "min_reserve": Decimal("0.20")}
            observe_cfg = Settings(db_path=path, api_key="k", api_secret="s", profile_code="p123", **wide)
            paper_cfg = Settings(
                mode="paper", db_path=path, api_key="k", api_secret="s", profile_code="p123", **wide
            )

            observe = Engine(observe_cfg, broker, vibe, journal, market)
            self.assertEqual(observe.cycle(), 1)
            self.assertEqual(broker.creates, [])
            self.assertFalse(observe.status.execution_permitted)
            self.assertEqual(journal.recent_submissions(), [])
            self.assertIn("SOLUSDT", market.priced)
            stages = {row["stage"] for row in journal.recent_decisions()}
            self.assertTrue({"market", "research", "risk", "broker"} <= stages)

            paper = Engine(paper_cfg, broker, vibe, journal, market)
            self.assertEqual(paper.cycle(), 1)
            self.assertEqual(len(broker.creates), 1)
            self.assertEqual(broker.creates[0]["pairCode"], "BTCUSDT")
            self.assertEqual(broker.creates[0]["amountPerTradeType"], "quote")
            self.assertNotIn("profilesCodes", broker.creates[0])
            self.assertTrue(paper.status.execution_permitted)
            self.assertEqual(journal.recent_submissions()[0]["state"], "confirmed")

            stale = evidence("ETHUSDT")
            market.books["ETHUSDT"] = replace(
                stale, passed=False, reason="stale_market_data",
                as_of=stale.as_of - timedelta(hours=2),
            )
            vibe.signals = [sig("ETHUSDT"), sig("BTCUSDT")]
            restarted = Engine(paper_cfg, broker, vibe, journal, market)
            self.assertEqual(restarted.cycle(), 0)
            self.assertEqual(len(broker.creates), 1)
            rejected = [row for row in journal.recent_decisions() if row["symbol"] == "ETHUSDT"]
            self.assertTrue(any(row["detail"] == "rejected" for row in rejected))

            market.books["ETHUSDT"] = evidence("ETHUSDT")
            broker.fail_next = "timeout"
            with self.assertRaises(IntegrationError):
                restarted.cycle()
            self.assertEqual(len(broker.creates), 2)
            self.assertEqual(len(journal.unresolved()), 1)
            blocked = Engine(paper_cfg, broker, vibe, journal, market)
            with self.assertRaises(IntegrationError):
                blocked.cycle()
            self.assertEqual(len(broker.creates), 2)

            client_id = journal.unresolved()[0]
            landed = strategy("ETHUSDT", client_id)
            landed["strategyId"] = "mock-landed"
            broker.found[client_id] = landed
            broker.live.append(landed)
            recovered = Engine(paper_cfg, broker, vibe, journal, market)
            self.assertEqual(recovered.cycle(), 0)
            self.assertEqual(journal.unresolved(), [])
            self.assertEqual(len(broker.creates), 2)

            open(os.path.join(tmp, "HALT"), "w").close()
            reads = broker.reads
            self.assertEqual(recovered.monitor(), 0)
            self.assertGreater(broker.reads, reads)
            self.assertEqual(len(broker.creates), 2)
            self.assertEqual(recovered.status.reason, "halt")
            self.assertFalse(recovered.status.execution_permitted)
            self.assertEqual(recovered.cycle(), 0)
            self.assertEqual(len(broker.creates), 2)

            os.remove(os.path.join(tmp, "HALT"))
            market.missing.add("SOLUSDT")
            unmarked = Engine(paper_cfg, broker, vibe, journal, market)
            self.assertEqual(unmarked.cycle(), 0)
            self.assertEqual(unmarked.status.reason, "mark_unavailable")
            self.assertEqual(len(broker.creates), 2)
            self.assertEqual(journal.unresolved(), [])
            journal.close()


if __name__ == "__main__":
    unittest.main()
