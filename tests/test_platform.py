"""Feature tests for discovery, alerts, scheduling, and the operator API."""

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import httpx
from test_core import evidence, sig, strategy
from test_failures import ExplodingBroker

from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.market import MarketData, MarketDataError
from warp3c.models import Signal
from warp3c.notify import flush_alerts
from warp3c.operator import SNAPSHOT, public_config, publish
from warp3c.regime import profile_for
from warp3c.risk import GlobalRisk
from warp3c.schedule import Clocks, due_actions
from warp3c.state import OrderJournal
from warp3c.supervisor import ResearchGate, once
from warp3c.telemetry import send_telegram
from warp3c.universe import Candidate, select_universe


class Research:
    def research(self, symbols, age):
        return [sig(symbol) for symbol in symbols[:1]]


class Markets:
    def scan(self, symbols, now=None):
        return {symbol: evidence(symbol) for symbol in symbols}


def candidate(symbol, volume="5000000", spread="0.001", tradable=True):
    return Candidate(symbol, Decimal(volume), Decimal(spread), tradable, Decimal("5"), 2)


class UniverseTests(unittest.TestCase):
    def test_filters_rank_and_cap(self):
        allow = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"}
        chosen, rejected = select_universe(
            [
                candidate("BTCUSDT", "5000000"),
                candidate("BNBUSDT", "9000000"),
                candidate("ETHUSDT", "8000000", spread="0.02"),
                candidate("SOLUSDT", "10"),
                candidate("XRPUSDT", "8000000"),
                candidate("DOGEUSDT", "8000000"),
                candidate("BTCUSDT", "1"),
            ],
            allow=allow,
            deny={"XRPUSDT"},
            min_volume=Decimal("1000000"),
            max_spread=Decimal("0.005"),
            limit=1,
        )
        self.assertEqual(chosen, ("BNBUSDT",))
        reasons = dict(rejected)
        self.assertEqual(reasons["ETHUSDT"], "spread_too_wide")
        self.assertEqual(reasons["SOLUSDT"], "illiquid")
        self.assertEqual(reasons["XRPUSDT"], "denylist")
        self.assertEqual(reasons["DOGEUSDT"], "outside_allowlist")
        self.assertEqual(reasons["BTCUSDT"], "above_candidate_cap")


class DiscoveryTests(unittest.TestCase):
    def test_auto_discovery_uses_the_documented_ticker_shape_and_caches(self):
        calls = []

        def row(symbol, volume, bid, ask):
            return {
                "symbol": symbol,
                "status": "TRADING",
                "quoteAsset": "USDT",
                "isSpotTradingAllowed": True,
                "quoteAssetPrecision": 2,
                "filters": [{"filterType": "NOTIONAL", "minNotional": "5"}],
            }

        def handler(req):
            calls.append(req.url.path)
            if req.url.path.endswith("exchangeInfo"):
                return httpx.Response(200, json={"symbols": [
                    row("BTCUSDT", "1", "100", "100.1"),
                    row("ETHUSDT", "1", "100", "102"),
                ]})
            if req.url.path.endswith("ticker/24hr"):
                return httpx.Response(200, json=[
                    {"symbol": "BTCUSDT", "quoteVolume": "5000000", "bidPrice": "100", "askPrice": "100.2"},
                    {"symbol": "ETHUSDT", "quoteVolume": "5000000", "bidPrice": "100", "askPrice": "102"},
                ])
            raise AssertionError(req.url.path)

        cfg = Settings(symbols=("BTCUSDT", "ETHUSDT"), scan_seconds=300)
        data = MarketData("https://data-api.binance.vision", cfg, transport=httpx.MockTransport(handler))
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        chosen, rejected = data.discover(now=now)
        self.assertEqual(chosen, ("BTCUSDT",))
        self.assertEqual(dict(rejected)["ETHUSDT"], "spread_too_wide")
        data.discover(now=now + timedelta(seconds=10))
        self.assertEqual(calls.count("/api/v3/exchangeInfo"), 1)
        data.close()

    def test_discovery_failure_does_not_trade_the_static_list(self):
        class Boom(Markets):
            def discover(self, now=None):
                raise MarketDataError("down")

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(discovery="auto", db_path=os.path.join(tmp, "warp.db"), api_key="k", api_secret="s", profile_code="p123")
            journal = OrderJournal(cfg.db_path)
            engine = Engine(cfg, _QuietBroker(), Research(), journal, Boom())
            engine.cycle()
            reasons = [row["reason"] for row in journal.recent_decisions()]
            self.assertIn("discovery_unavailable", reasons)
            self.assertEqual(engine.universe, ())
            self.assertEqual(engine.status.reason, "discovery_unavailable")
            journal.close()

    def test_open_discovery_can_select_a_coin_outside_the_static_list(self):
        def row(symbol):
            return {
                "symbol": symbol, "status": "TRADING", "quoteAsset": "USDT",
                "isSpotTradingAllowed": True, "quoteAssetPrecision": 2,
                "filters": [{"filterType": "NOTIONAL", "minNotional": "5"}],
            }

        def ticker(symbol):
            return {"symbol": symbol, "quoteVolume": "8000000", "bidPrice": "100", "askPrice": "100.1"}

        def handler(req):
            if req.url.path.endswith("exchangeInfo"):
                return httpx.Response(200, json={"symbols": [row("BTCUSDT"), row("ADAUSDT")]})
            if req.url.path.endswith("ticker/24hr"):
                return httpx.Response(200, json=[ticker("BTCUSDT"), ticker("ADAUSDT")])
            raise AssertionError(req.url.path)

        open_cfg = Settings(deny_symbols=("XRPUSDT",))
        data = MarketData("https://data-api.binance.vision", open_cfg, transport=httpx.MockTransport(handler))
        chosen, _rejected = data.discover(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertIn("ADAUSDT", chosen)
        self.assertIn("BTCUSDT", chosen)
        data.close()

        limited = Settings(discovery_allow=("ADAUSDT",), deny_symbols=("XRPUSDT",))
        data = MarketData("https://data-api.binance.vision", limited, transport=httpx.MockTransport(handler))
        chosen, rejected = data.discover(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(chosen, ("ADAUSDT",))
        self.assertEqual(dict(rejected)["BTCUSDT"], "outside_allowlist")
        data.close()
        Settings(discovery="auto", correlation=(("ADAUSDT", "alts"),))

    def test_auto_scan_fails_closed_without_a_spread(self):
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        candles = []
        price = 100
        for i in range(80):
            close_at = now - timedelta(minutes=15 * (80 - i))
            price += 0.4
            candles.append([
                0, str(price), str(price + 1), str(price - 1), str(price), "1",
                int(close_at.timestamp() * 1000), "1000", 0, "0", "0", "0",
            ])

        def handler(req):
            if req.url.path.endswith("exchangeInfo"):
                return httpx.Response(200, json={"symbols": [{
                    "symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT",
                    "isSpotTradingAllowed": True, "quoteAssetPrecision": 2,
                    "filters": [{"filterType": "NOTIONAL", "minNotional": "5"}],
                }]})
            if req.url.path.endswith("ticker/24hr"):
                return httpx.Response(200, json={
                    "symbol": "BTCUSDT", "quoteVolume": "5000000", "lastPrice": "130",
                })
            if req.url.path.endswith("klines"):
                return httpx.Response(200, json=candles)
            raise AssertionError(req.url.path)

        cfg = Settings(discovery="auto", rsi_max=Decimal("100"), symbols=("BTCUSDT",))
        data = MarketData("https://data-api.binance.vision", cfg, transport=httpx.MockTransport(handler))
        item = data.scan(("BTCUSDT",), now=now)["BTCUSDT"]
        self.assertFalse(item.passed)
        self.assertEqual(item.reason, "spread_unknown")
        data.close()


class _QuietBroker:
    def get_account(self, code, exchange_code):
        from warp3c.models import Account
        return Account(code, True, Decimal("1000"), Decimal("0"), Decimal("1000"))

    def get_live(self):
        return []

    def create_strategy(self, payload):
        raise AssertionError("this test must not submit")


class RegimeTests(unittest.TestCase):
    def test_only_trend_profile_can_size_and_model_amounts_are_ignored(self):
        cfg = Settings()
        trend = profile_for("trend_up", cfg)
        volatile = profile_for("high_volatility", cfg)
        self.assertTrue(trend.entries_allowed)
        self.assertEqual(trend.size_scale, Decimal("1"))
        self.assertFalse(volatile.entries_allowed)
        self.assertEqual(volatile.size_scale, Decimal("0"))
        raw = {
            "symbol": "BTCUSDT", "action": "BUY", "confidence": "0.9", "amount": "999999",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "source": "verified feed at timestamp", "thesis": "Fresh momentum with volume confirmation",
        }
        parsed = Signal.parse(raw, {"BTCUSDT"}, max_age_minutes=30)
        signal = Signal(parsed.symbol, parsed.action, parsed.confidence, parsed.as_of, parsed.source, parsed.thesis, evidence())
        plan = GlobalRisk(cfg).plan(
            _QuietBroker().get_account("p123", "BINANCE"), [], [signal], equity=Decimal("1000")
        )[0]
        self.assertLessEqual(plan.base_amount, cfg.max_base)
        self.assertNotEqual(plan.base_amount, Decimal("999999"))


class AlertTests(unittest.TestCase):
    def test_failed_delivery_stays_pending_and_is_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            now = datetime.now(timezone.utc)
            self.assertTrue(journal.enqueue_alert("drawdown", "paused", now=now))
            self.assertFalse(journal.enqueue_alert("drawdown", "paused", now=now))
            calls = []

            def deliver(event, detail):
                calls.append(event)
                return len(calls) > 1

            sent = flush_alerts(journal, deliver, now=now, delay_for=lambda _attempts: 0)
            self.assertEqual(sent, 0)
            self.assertEqual(journal.recent_alerts(1)[0]["state"], "pending")
            journal.clear_alert()
            self.assertEqual(journal.due_alerts(now)[0][0], journal.recent_alerts(1)[0]["id"])
            sent = flush_alerts(journal, deliver, now=now, delay_for=lambda _attempts: 0)
            self.assertEqual(sent, 1)
            self.assertEqual(journal.recent_alerts(1)[0]["state"], "delivered")
            journal.close()

    def test_telegram_body_does_not_contain_the_bot_token(self):
        seen = {}

        def handler(req):
            seen["url"] = str(req.url)
            seen["body"] = json.loads(req.content.decode())
            return httpx.Response(200, json={"ok": True})

        token = "123456:secret-token"
        ok = send_telegram(token, "99", "halt", "paused", transport=httpx.MockTransport(handler))
        self.assertTrue(ok)
        self.assertNotIn(token, seen["body"]["text"])
        self.assertIn(token, seen["url"])


class ScheduleTests(unittest.TestCase):
    def test_monitor_stays_due_while_research_is_fresh(self):
        now = datetime.now(timezone.utc)
        clocks = Clocks(scan=now, research=now, monitor=now - timedelta(seconds=120), reconcile=now)
        self.assertEqual(due_actions(clocks, now, Settings()), frozenset({"monitor"}))

    def test_busy_research_does_not_submit(self):
        started = threading.Event()
        release = threading.Event()

        class Slow:
            def research(self, symbols, age):
                started.set()
                release.wait(2)
                return [sig()]

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(
                mode="paper", db_path=os.path.join(tmp, "warp.db"),
                api_key="k", api_secret="s", profile_code="p123",
            )
            journal = OrderJournal(cfg.db_path)
            broker = ExplodingBroker()
            engine = Engine(cfg, broker, Slow(), journal, Markets())
            gate = ResearchGate()
            self.assertTrue(gate.start(engine.collect_research))
            self.assertTrue(started.wait(1))
            self.assertTrue(gate.busy())
            now = datetime.now(timezone.utc)
            once(engine, gate, Clocks(scan=now, research=now, reconcile=now), now=now)
            self.assertEqual(broker.creates, 0)
            self.assertGreaterEqual(broker.reads, 1)
            release.set()
            gate._thread.join(2)
            journal.close()

    def test_halt_keeps_the_research_result_for_a_later_cycle(self):
        class RecordingBroker(ExplodingBroker):
            def create_strategy(self, payload):
                self.creates += 1
                return {"strategyId": "mock-1", "clientId": payload["clientId"]}

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(
                mode="paper", db_path=os.path.join(tmp, "warp.db"),
                api_key="k", api_secret="s", profile_code="p123",
            )
            journal = OrderJournal(cfg.db_path)
            broker = RecordingBroker()
            engine = Engine(cfg, broker, Research(), journal, Markets())
            engine.evidence = {"BTCUSDT": evidence("BTCUSDT")}
            engine.universe = ("BTCUSDT",)
            gate = ResearchGate()
            self.assertTrue(gate.start(lambda: [sig()]))
            gate._thread.join(2)
            now = datetime.now(timezone.utc)
            open(os.path.join(tmp, "HALT"), "w").close()
            once(engine, gate, Clocks(scan=now, research=now, reconcile=now), now=now)
            self.assertEqual(broker.creates, 0)
            self.assertGreaterEqual(broker.reads, 1)
            os.remove(os.path.join(tmp, "HALT"))
            later = now + timedelta(seconds=1)
            fresh = Clocks(scan=later, research=later, monitor=later, reconcile=later)
            once(engine, gate, fresh, now=later)
            self.assertEqual(broker.creates, 1)
            journal.close()


class OperatorTests(unittest.TestCase):
    def test_dashboard_requires_a_token_and_pause_does_not_close_positions(self):
        token = "operator-token-16"
        previous = os.environ.get("WARP_OPERATOR_TOKEN")
        os.environ["WARP_OPERATOR_TOKEN"] = token
        from warp3c.__main__ import HEALTH, HealthHandler

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"), api_key="super-secret-key", api_secret="s", profile_code="p123")
            journal = OrderJournal(cfg.db_path)
            engine = Engine(cfg, _QuietBroker(), Research(), journal, Markets())
            engine.monitor()
            publish(cfg, journal, engine)
            self.assertNotIn("super-secret-key", json.dumps(public_config(cfg)))
            self.assertNotIn("super-secret-key", json.dumps(SNAPSHOT["/api/config"]))
            saved = dict(HEALTH)
            HEALTH["halt_path"] = os.path.join(tmp, "HALT")
            server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                port = server.server_address[1]
                conn = HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    conn.request("GET", "/api/config")
                    denied = conn.getresponse()
                    denied.read()
                    self.assertEqual(denied.status, 401)
                    conn.request("GET", "/api/config", headers={"Authorization": "Bearer " + token})
                    allowed = conn.getresponse()
                    body = json.loads(allowed.read().decode())
                    self.assertEqual(allowed.status, 200)
                    self.assertEqual(body["mode"], "observe")
                    self.assertNotIn("api_key", body)
                    conn.request("POST", "/api/pause", headers={"Authorization": "Bearer " + token})
                    paused = conn.getresponse()
                    payload = json.loads(paused.read().decode())
                    self.assertEqual(paused.status, 200)
                    self.assertFalse(payload["closes_positions"])
                    self.assertTrue(os.path.exists(HEALTH["halt_path"]))
                    conn.request("GET", "/dashboard")
                    page = conn.getresponse()
                    html = page.read().decode()
                    self.assertEqual(page.status, 200)
                    self.assertIn("viewport", html)
                    self.assertNotIn("super-secret-key", html)
                finally:
                    conn.close()
            finally:
                server.shutdown()
                server.server_close()
                HEALTH.clear()
                HEALTH.update(saved)
                journal.close()
        if previous is None:
            os.environ.pop("WARP_OPERATOR_TOKEN", None)
        else:
            os.environ["WARP_OPERATOR_TOKEN"] = previous


class BackupTests(unittest.TestCase):
    def test_backup_restores_an_unresolved_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = OrderJournal(os.path.join(tmp, "live.db"))
            client_id = "warp3c-" + "ab" * 16
            source.reserve(client_id, "BTCUSDT", {"pairCode": "BTCUSDT"})
            dest = os.path.join(tmp, "backup.db")
            source.backup(dest)
            restored = OrderJournal(os.path.join(tmp, "restored.db"))
            self.assertEqual(restored.unresolved(), [])
            restored.restore_from(dest)
            self.assertEqual(restored.unresolved(), [client_id])
            source.close()
            restored.close()


class RiskEventTests(unittest.TestCase):
    def test_open_pair_volatility_pauses_new_entries(self):
        hot = evidence("BTCUSDT")
        hot = type(hot)(
            hot.symbol, hot.as_of, hot.last_price, hot.quote_volume, hot.trend, hot.momentum,
            hot.rsi, Decimal("0.2"), "high_volatility", hot.min_notional, hot.quote_precision,
            hot.tradable, False, "high_volatility",
        )
        account = _QuietBroker().get_account("p123", "BINANCE")
        decision = GlobalRisk(Settings()).assess(
            account, [strategy(symbol="BTCUSDT")], [], prices={"BTCUSDT": Decimal("100")},
            marks=[], evidence={"BTCUSDT": hot},
        )
        self.assertEqual(decision.reason, "abnormal_volatility")
        self.assertFalse(decision.execution_permitted)
        self.assertEqual(decision.orders, [])


if __name__ == "__main__":
    unittest.main()
