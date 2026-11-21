"""Phase 1: screener parsing, risk gate, payloads, idempotency, and session auth."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
from test_core import evidence, sig

from warp3c.auth import note_login_success
from warp3c.backtest import run_backtest
from warp3c.bots import (
    PRESETS,
    BotTemplate,
    allocate_client_id,
    build_dca_bot_payload,
    build_grid_payload,
    build_strategy_dca_payload,
    client_id_for,
    execution_fractions,
)
from warp3c.config import DEFAULT_NVIDIA_MODEL, Settings, parse_groups
from warp3c.engine import Engine
from warp3c.httpapi import web_root
from warp3c.levels import derive_levels
from warp3c.llm import LlmAnalyst, strip_price_fields
from warp3c.models import Account, PlannedOrder
from warp3c.operator import publish
from warp3c.qualify import blend_confidence, qualify
from warp3c.risk import GlobalRisk, make_trade_payload
from warp3c.screener import (
    Screener,
    ScreenerFilters,
    ScreenerHit,
    apply_filters,
    parse_binance_tickers,
    parse_tradingview,
    select_symbols,
)
from warp3c.state import OrderJournal
from warp3c.vibe import ResearchError


def _candles(count: int, step: str) -> list[dict]:
    price = Decimal("100")
    delta = Decimal(step)
    rows = []
    for _ in range(count):
        price *= Decimal("1") + delta
        rows.append(
            {
                "high": price * Decimal("1.002"),
                "low": price * Decimal("0.998"),
                "close": price,
            }
        )
    return rows


class ScreenerTests(unittest.TestCase):
    def test_tradingview_rows_are_filtered_and_ranked(self):
        payload = {
            "data": [
                {"s": "BINANCE:ETHUSDT", "d": ["ETH", 10, 1.2, 1, 0.3, 55, 8_000_000]},
                {"s": "BINANCE:BTCUSDT", "d": ["BTC", 100, 0.4, 1, 0.5, 60, 20_000_000]},
                {"s": "BINANCE:DOGEUSDT", "d": ["DOGE", 0.1, 1, 1, -0.4, 20, 9_000_000]},
                {"s": "BINANCE:USDCUSDT", "d": ["USDC", 1, 0.01, 1, 0.2, 50, 90_000_000_000]},
                {"s": "BINANCE:PUMPUSDT", "d": ["PUMP", 1, 40, 1, 0.6, 50, 9_000_000]},
                {"s": "BINANCE:LOWUSDT", "d": ["LOW", 1, 1, 1, 0.6, 50, 10]},
                {"s": "BYBIT:SOLUSDT", "d": ["SOL", 1, 1, 1, 0.6, 50, 9_000_000]},
                {"s": "BINANCE:HOTUSDT.P", "d": ["HOT", 1, 1, 1, 0.6, 50, 9_000_000]},
            ]
        }
        hits = apply_filters(parse_tradingview(payload), ScreenerFilters())
        self.assertEqual([hit.symbol for hit in hits], ["BTCUSDT", "DOGEUSDT", "ETHUSDT"])
        self.assertEqual(hits[0].quote_volume, Decimal("20000000"))
        self.assertEqual(hits[0].source, "tradingview")

    def test_binance_fallback_parses_quote_volume(self):
        hits = parse_binance_tickers(
            [
                {
                    "symbol": "SOLUSDT",
                    "lastPrice": "150",
                    "priceChangePercent": "2.5",
                    "quoteVolume": "3000000",
                }
            ]
        )
        kept = apply_filters(hits, ScreenerFilters())
        self.assertEqual(kept[0].symbol, "SOLUSDT")
        self.assertIsNone(kept[0].rsi)
        self.assertEqual(kept[0].source, "binance")

    def test_scanner_http_failure_uses_binance(self):
        def scanner(_request):
            return httpx.Response(503, json={"error": "down"})

        def market(_request):
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "100",
                        "priceChangePercent": "1",
                        "quoteVolume": "5000000",
                    }
                ],
            )

        screener = Screener(
            "https://scanner.tradingview.com",
            "https://data-api.binance.vision",
            scanner_transport=httpx.MockTransport(scanner),
            market_transport=httpx.MockTransport(market),
        )
        hits = screener.scan(ScreenerFilters())
        self.assertEqual(screener.source, "binance")
        self.assertEqual(hits[0].symbol, "BTCUSDT")
        screener.close()

    def test_allow_and_deny_lists_bound_the_universe(self):
        hits = [
            ScreenerHit("BTCUSDT", Decimal("1"), Decimal("1"), Decimal("3"), None, None, "binance"),
            ScreenerHit("ETHUSDT", Decimal("1"), Decimal("1"), Decimal("2"), None, None, "binance"),
            ScreenerHit("SOLUSDT", Decimal("1"), Decimal("1"), Decimal("1"), None, None, "binance"),
        ]
        chosen = select_symbols(hits, allow={"ETHUSDT", "SOLUSDT"}, deny={"SOLUSDT"}, limit=5)
        self.assertEqual(chosen, ("ETHUSDT",))


class RiskGateTests(unittest.TestCase):
    def test_backtest_accepts_a_rise_and_rejects_a_drop(self):
        rising = run_backtest(_candles(90, "0.004"), Decimal("0.05"), Decimal("0.01"), min_trades=3)
        self.assertTrue(rising.passed)
        self.assertGreaterEqual(rising.trades, 3)
        falling = run_backtest(_candles(90, "-0.004"), Decimal("0.02"), Decimal("0.02"), min_trades=3)
        self.assertFalse(falling.passed)

    def test_levels_ignore_model_prices_and_confidence_cannot_increase(self):
        raw = {"symbol": "BTCUSDT", "action": "BUY", "entry": "1", "stop_loss": "0.5", "confidence": 0.9}
        cleaned = strip_price_fields(raw)
        self.assertNotIn("entry", cleaned)
        item = evidence()
        levels = derive_levels(item, Settings())
        self.assertIsNotNone(levels)
        assert levels is not None
        self.assertEqual(levels.entry, item.last_price)
        self.assertLess(levels.stop, levels.entry)
        self.assertGreater(levels.take, levels.entry)
        self.assertLess(blend_confidence(Decimal("0.9"), None), Decimal("0.9"))
        self.assertEqual(blend_confidence(Decimal("0.9"), item), Decimal("0.9"))
        self.assertEqual(levels.stop_fraction, Settings().stop_loss)
        payload = make_trade_payload(Settings(profile_code="p123"), PlannedOrder("BTCUSDT", Decimal("20"), Decimal("120"), Decimal("0.9")), "warp3c-" + "ab" * 16)
        self.assertEqual(payload["stopLoss"], levels.stop_fraction)
        aggressive = BotTemplate("dca", "strategy", "aggressive")
        _gated, card = qualify(sig(), item, None, Settings(), aggressive)
        self.assertEqual(Decimal(card["stop_fraction"]), execution_fractions(Settings(), aggressive)[0])
        self.assertEqual(Decimal(card["stop_fraction"]), min(PRESETS["aggressive"].stop_loss, Settings().stop_loss))

    def test_each_untracked_bot_counts_in_its_own_group(self):
        cfg = Settings(
            correlation=parse_groups("majors:BTCUSDT|ETHUSDT,alts:SOLUSDT"),
            max_group=Decimal("0.25"),
        )
        account = Account("p123", True, Decimal("1000"), Decimal("0"), Decimal("1000"))
        plans = GlobalRisk(cfg).plan(
            account,
            [],
            [sig("ETHUSDT")],
            equity=Decimal("1000"),
            extra_commitments=(("BTCUSDT", Decimal("150")), ("SOLUSDT", Decimal("60"))),
        )
        self.assertEqual(len(plans), 1)
        self.assertLessEqual(plans[0].full_commitment, Decimal("100"))

    def test_monitor_keeps_the_last_research_cards(self):
        from test_failures import ExplodingBroker, Markets, Research, paper

        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            engine = Engine(cfg, ExplodingBroker(), Research(), journal, Markets())
            engine.last_opportunities = [{"symbol": "BTCUSDT", "blended": "0.9"}]
            engine.monitor()
            self.assertEqual(engine.last_opportunities[0]["symbol"], "BTCUSDT")
            journal.close()

    def test_failed_backtest_blocks_a_paper_entry(self):
        from test_failures import ExplodingBroker, Markets, Research, paper

        with __import__("tempfile").TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            cfg = Settings(
                mode=cfg.mode,
                db_path=cfg.db_path,
                api_key=cfg.api_key,
                api_secret=cfg.api_secret,
                profile_code=cfg.profile_code,
                require_backtest=True,
            )
            journal = OrderJournal(cfg.db_path)
            broker = ExplodingBroker()
            self.assertEqual(Engine(cfg, broker, Research(), journal, Markets()).cycle(), 0)
            self.assertEqual(broker.creates, 0)
            journal.close()


class PayloadTests(unittest.TestCase):
    def _plan(self) -> PlannedOrder:
        return PlannedOrder("BTCUSDT", Decimal("20"), Decimal("120"), Decimal("0.9"))

    def test_moderate_strategy_matches_the_historical_payload(self):
        cfg = Settings(profile_code="p123")
        plan = self._plan()
        client_id = "warp3c-" + "ab" * 16
        built = build_strategy_dca_payload(cfg, plan, client_id, BotTemplate())
        self.assertEqual(built, make_trade_payload(cfg, plan, client_id))
        self.assertNotIn("profilesCodes", built)

    def test_preset_cannot_exceed_the_configured_ladder(self):
        cfg = Settings(profile_code="p123", dca_orders=4, max_base=Decimal("100"))
        plan = self._plan()
        client_id = "warp3c-" + "cd" * 16
        payload = build_strategy_dca_payload(cfg, plan, client_id, BotTemplate("dca", "strategy", "aggressive"))
        self.assertLessEqual(payload["extraOrderCount"], cfg.dca_orders)
        self.assertLessEqual(payload["amountPerTrade"], plan.base_amount)
        self.assertLessEqual(payload["stopLoss"], cfg.stop_loss)
        self.assertEqual(payload["amountPerTradeType"], "quote")

    def test_dca_and_grid_payloads_use_deterministic_levels(self):
        cfg = Settings(profile_code="p123")
        plan = self._plan()
        client_id = "warp3c-" + "ef" * 16
        dca = build_dca_bot_payload(cfg, plan, client_id, BotTemplate("dca", "dca_bot", "conservative"))
        self.assertEqual(dca["pairsCodes"], ["BTCUSDT"])
        self.assertEqual(dca["profilesCodes"], ["p123"])
        self.assertEqual(dca["amountPerTradeType"], "quote")
        self.assertNotIn("price", dca)
        self.assertGreater(dca["amountPerTrade"], 0)
        levels = derive_levels(evidence(), cfg)
        assert levels is not None
        grid, total = build_grid_payload(cfg, plan, levels, client_id, BotTemplate("grid", "grid_bot", "moderate"))
        self.assertLess(grid["lowPrice"], levels.entry)
        self.assertGreater(grid["highPrice"], levels.entry)
        self.assertEqual(grid["midPrice"], grid["highPrice"])
        self.assertEqual(grid["amountPerTradeType"], "quote")
        self.assertEqual(grid["profilesCodes"], ["p123"])
        self.assertNotIn("profiles", grid)
        self.assertGreater(grid["stopLoss"], 1)
        self.assertLessEqual(total, plan.full_commitment)
        self.assertIn(client_id, grid["name"])


class IdempotencyTests(unittest.TestCase):
    def test_same_setup_is_not_reserved_twice_until_rejected(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            now = datetime.now(timezone.utc)
            template = BotTemplate()
            first = allocate_client_id(journal, "BTCUSDT", template, now)
            self.assertEqual(first, client_id_for("BTCUSDT", "dca", "moderate", now.date().isoformat(), 0))
            journal.reserve(first, "BTCUSDT", {"pairCode": "BTCUSDT"})
            self.assertIsNone(allocate_client_id(journal, "BTCUSDT", template, now))
            journal.reject(first)
            second = allocate_client_id(journal, "BTCUSDT", template, now)
            self.assertIsNotNone(second)
            self.assertNotEqual(second, first)
            self.assertEqual(allocate_client_id(journal, "BTCUSDT", template, now), second)
            journal.close()


class AuthTests(unittest.TestCase):
    def setUp(self):
        self._env = {
            "WARP_DASHBOARD_PASSWORD": os.environ.get("WARP_DASHBOARD_PASSWORD"),
            "WARP_OPERATOR_TOKEN": os.environ.get("WARP_OPERATOR_TOKEN"),
            "WARP_COOKIE_SECURE": os.environ.get("WARP_COOKIE_SECURE"),
        }
        os.environ["WARP_DASHBOARD_PASSWORD"] = "correct-horse-battery"
        os.environ["WARP_OPERATOR_TOKEN"] = "operator-token-16"
        os.environ["WARP_COOKIE_SECURE"] = "0"
        note_login_success()

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        note_login_success()

    def test_password_session_and_bearer_both_work(self):
        from fastapi.testclient import TestClient

        from warp3c.__main__ import HEALTH
        from warp3c.webapi import create_app

        with __import__("tempfile").TemporaryDirectory() as tmp:
            cfg = Settings(
                db_path=os.path.join(tmp, "warp.db"),
                api_key="super-secret-key",
                api_secret="s",
                profile_code="p123",
            )
            journal = OrderJournal(cfg.db_path)
            from test_failures import Markets, Research

            engine = Engine(cfg, _Quiet(), Research(), journal, Markets())
            engine.monitor()
            publish(cfg, journal, engine)
            saved = dict(HEALTH)
            HEALTH["halt_path"] = os.path.join(tmp, "HALT")
            client = TestClient(create_app())
            try:
                denied = client.get("/api/config")
                self.assertEqual(denied.status_code, 401)
                bad = client.post("/api/login", json={"password": "nope-nope-nope"})
                self.assertEqual(bad.status_code, 401)
                self.assertNotIn("correct-horse-battery", bad.text)
                good = client.post("/api/login", json={"password": "correct-horse-battery"})
                self.assertEqual(good.status_code, 200)
                self.assertIn("warp_session", good.headers.get("set-cookie", ""))
                allowed = client.get("/api/config")
                self.assertEqual(allowed.status_code, 200)
                self.assertEqual(allowed.json()["mode"], "observe")
                status = client.get("/api/status")
                self.assertEqual(status.status_code, 200)
                providers = status.json()["providers"]
                self.assertEqual(providers["nvidia"]["state"], "unconfigured")
                self.assertEqual(providers["nvidia"]["model"], DEFAULT_NVIDIA_MODEL)
                self.assertEqual(providers["openrouter"]["state"], "unconfigured")
                self.assertNotIn("super-secret-key", allowed.text)
                paused = client.post("/api/pause")
                self.assertEqual(paused.status_code, 200)
                self.assertFalse(paused.json()["closes_positions"])
                self.assertTrue(os.path.exists(HEALTH["halt_path"]))
                saved_filters = client.post(
                    "/api/screener/filters",
                    json={"min_quote_volume": "2500000", "limit": 5},
                )
                self.assertEqual(saved_filters.status_code, 200)
                self.assertEqual(saved_filters.json()["filters"]["limit"], 5)
                preview = client.post("/api/bots/preview", json={"kind": "grid", "preset": "conservative"})
                self.assertEqual(preview.status_code, 200)
                self.assertFalse(preview.json()["places_orders"])
                client.post("/api/logout")
                self.assertEqual(client.get("/api/config").status_code, 401)
                bearer = client.get("/api/config", headers={"Authorization": "Bearer operator-token-16"})
                self.assertEqual(bearer.status_code, 200)
                page = client.get("/dashboard")
                self.assertEqual(page.status_code, 200)
                self.assertIn("viewport", page.text)
                self.assertNotIn("super-secret-key", page.text)
            finally:
                HEALTH.clear()
                HEALTH.update(saved)
                journal.close()
                client.close()


class HaltFreshnessTests(unittest.TestCase):
    def test_halt_file_wins_before_the_next_publish_and_the_snapshot_stays_frozen(self):
        from fastapi.testclient import TestClient

        from warp3c.__main__ import HEALTH
        from warp3c.operator import SNAPSHOT
        from warp3c.webapi import create_app

        previous = {
            "WARP_DASHBOARD_PASSWORD": os.environ.get("WARP_DASHBOARD_PASSWORD"),
            "WARP_OPERATOR_TOKEN": os.environ.get("WARP_OPERATOR_TOKEN"),
            "WARP_COOKIE_SECURE": os.environ.get("WARP_COOKIE_SECURE"),
        }
        os.environ["WARP_DASHBOARD_PASSWORD"] = "correct-horse-battery"
        os.environ["WARP_OPERATOR_TOKEN"] = "operator-token-16"
        os.environ["WARP_COOKIE_SECURE"] = "0"
        note_login_success()
        saved = dict(HEALTH)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"), api_key="k", api_secret="s", profile_code="p123")
            journal = OrderJournal(cfg.db_path)
            from test_failures import Markets, Research

            engine = Engine(cfg, _Quiet(), Research(), journal, Markets())
            engine.monitor()
            engine.status.execution_permitted = True
            engine.status.risk_permitted = True
            engine.status.reason = "ok"
            publish(cfg, journal, engine)
            HEALTH["halt_path"] = os.path.join(tmp, "HALT")
            client = TestClient(create_app())
            try:
                self.assertEqual(client.post("/api/login", json={"password": "correct-horse-battery"}).status_code, 200)
                first = client.get("/api/status")
                second = client.get("/api/status")
                self.assertEqual(first.status_code, 200)
                self.assertFalse(first.json()["halt"])
                self.assertTrue(first.json()["execution_permitted"])
                published = first.json()["published_at"]
                self.assertEqual(second.json()["published_at"], published)
                self.assertEqual(SNAPSHOT["/api/status"]["published_at"], published)
                paused = client.post("/api/pause")
                self.assertEqual(paused.json()["halt"], True)
                held = client.get("/api/status").json()
                self.assertTrue(held["halt"])
                self.assertFalse(held["execution_permitted"])
                self.assertEqual(held["pipeline"]["risk"]["reason"], "halt")
                self.assertEqual(held["published_at"], published)
                self.assertFalse(SNAPSHOT["/api/status"]["halt"])
                self.assertTrue(SNAPSHOT["/api/status"]["execution_permitted"])
                self.assertEqual(client.get("/api/breakers").json()["halt"], True)
                resumed = client.post("/api/resume")
                self.assertEqual(resumed.json()["halt"], False)
                client.post("/api/pause")
                again = client.get("/api/status").json()
                self.assertTrue(again["halt"])
                self.assertEqual(again["published_at"], published)
                client.post("/api/resume")
                released = client.get("/api/status").json()
                self.assertFalse(released["halt"])
                self.assertTrue(released["execution_permitted"])
                self.assertEqual(released["published_at"], published)
                self.assertNotEqual(released["pipeline"]["risk"]["reason"], "halt")
            finally:
                HEALTH.clear()
                HEALTH.update(saved)
                journal.close()
                client.close()
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                note_login_success()

    def test_resume_after_a_halted_publish_waits_for_the_worker(self):
        from fastapi.testclient import TestClient

        from warp3c.__main__ import HEALTH
        from warp3c.operator import SNAPSHOT
        from warp3c.webapi import create_app

        previous = {
            "WARP_DASHBOARD_PASSWORD": os.environ.get("WARP_DASHBOARD_PASSWORD"),
            "WARP_OPERATOR_TOKEN": os.environ.get("WARP_OPERATOR_TOKEN"),
            "WARP_COOKIE_SECURE": os.environ.get("WARP_COOKIE_SECURE"),
        }
        os.environ["WARP_DASHBOARD_PASSWORD"] = "correct-horse-battery"
        os.environ["WARP_OPERATOR_TOKEN"] = "operator-token-16"
        os.environ["WARP_COOKIE_SECURE"] = "0"
        note_login_success()
        saved = dict(HEALTH)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"), api_key="k", api_secret="s", profile_code="p123")
            journal = OrderJournal(cfg.db_path)
            from test_failures import Markets, Research

            engine = Engine(cfg, _Quiet(), Research(), journal, Markets())
            engine.monitor()
            halt = os.path.join(tmp, "HALT")
            HEALTH["halt_path"] = halt
            Path(halt).write_text("pause\n", encoding="utf-8")
            engine.status.execution_permitted = False
            engine.status.risk_permitted = False
            engine.status.reason = "halt"
            publish(cfg, journal, engine)
            client = TestClient(create_app())
            try:
                self.assertEqual(client.post("/api/login", json={"password": "correct-horse-battery"}).status_code, 200)
                before = client.get("/api/status").json()
                self.assertTrue(before["halt"])
                self.assertEqual(before["gating"], "halt")
                self.assertFalse(before["execution_permitted"])
                self.assertFalse(before["risk_permitted"])
                self.assertEqual(before["pipeline"]["risk"], {"state": "blocked", "reason": "halt"})
                published = before["published_at"]
                resumed = client.post("/api/resume")
                self.assertEqual(resumed.json()["halt"], False)
                self.assertFalse(os.path.exists(halt))
                after = client.get("/api/status").json()
                self.assertFalse(after["halt"])
                self.assertEqual(after["gating"], "awaiting_worker")
                self.assertFalse(after["execution_permitted"])
                self.assertFalse(after["risk_permitted"])
                self.assertEqual(after["reason"], "awaiting_worker")
                self.assertEqual(after["pipeline"]["risk"]["state"], "blocked")
                self.assertEqual(after["pipeline"]["risk"]["reason"], "awaiting_worker")
                self.assertNotEqual(after["pipeline"]["risk"]["reason"], "halt")
                self.assertEqual(after["published_at"], published)
                stored = SNAPSHOT["/api/status"]
                self.assertTrue(stored["halt"])
                self.assertEqual(stored["reason"], "halt")
                self.assertEqual(stored["pipeline"]["risk"]["reason"], "halt")
                self.assertFalse(stored["execution_permitted"])
                self.assertEqual(client.get("/api/breakers").json()["gating"], "awaiting_worker")
            finally:
                HEALTH.clear()
                HEALTH.update(saved)
                journal.close()
                client.close()
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                note_login_success()


class LlmTests(unittest.TestCase):
    def test_openrouter_fallback_strips_prices_and_the_cap_holds(self):
        item = evidence()

        def handler(request: httpx.Request) -> httpx.Response:
            if "nvidia" in request.url.host:
                return httpx.Response(503, json={"error": "down"})
            body = json.loads(request.content.decode())
            if "closer spot-crypto" in body["messages"][-1]["content"]:
                content = json.dumps({"symbols": ["BTCUSDT"]})
            else:
                content = json.dumps(
                    {
                        "decisions": [
                            {
                                "symbol": "BTCUSDT",
                                "action": "BUY",
                                "confidence": 0.91,
                                "entry": 1,
                                "stop_loss": 0.5,
                                "source": "llm evidence snapshot",
                                "thesis": "Trend is up on the supplied snapshot",
                            }
                        ]
                    }
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        analyst = LlmAnalyst(
            nvidia_key="nv-test",
            openrouter_key="or-test",
            timeout=5,
            daily_calls=4,
            daily_usd=Decimal("2"),
            usd_per_call=Decimal("0.02"),
            transport=httpx.MockTransport(handler),
        )
        signals = analyst.research(("BTCUSDT",), {"BTCUSDT": item}, 30)
        self.assertEqual(signals[0].action, "BUY")
        self.assertEqual(signals[0].symbol, "BTCUSDT")
        levels = derive_levels(item, Settings())
        assert levels is not None
        self.assertEqual(levels.entry, Decimal("100"))
        with self.assertRaises(ResearchError):
            analyst.budget.calls = 0
            analyst.research(("BTCUSDT",), {"BTCUSDT": item}, 30)
        analyst.close()

    def test_openrouter_reasoning_channel_still_publishes_a_signal(self):
        item = evidence()
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            seen["reasoning"] = body.get("reasoning")
            self.assertNotIn("reasoning_effort", body)
            answer = json.dumps(
                {
                    "decisions": [
                        {
                            "symbol": "BTCUSDT",
                            "action": "WAIT",
                            "confidence": 0.42,
                            "source": "llm evidence snapshot",
                            "thesis": "Evidence is thin on the supplied snapshot",
                        }
                    ]
                }
            )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": None, "reasoning_content": answer}}]},
            )

        analyst = LlmAnalyst(
            openrouter_key="or-test",
            timeout=5,
            daily_calls=4,
            transport=httpx.MockTransport(handler),
        )
        signals = analyst.research(("BTCUSDT",), {"BTCUSDT": item}, 30)
        self.assertEqual(seen["reasoning"], {"effort": "none"})
        self.assertEqual(signals[0].action, "WAIT")
        self.assertEqual(signals[0].symbol, "BTCUSDT")
        analyst.close()

    def test_gone_model_cools_down_and_is_not_retried(self):
        item = evidence()
        now = datetime(2026, 11, 21, tzinfo=timezone.utc)
        clock = {"now": now}
        nvidia_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            if "nvidia" in request.url.host:
                nvidia_calls.append(body["model"])
                self.assertFalse(body["chat_template_kwargs"]["enable_thinking"])
                return httpx.Response(410, json={"error": "gone"})
            if "closer spot-crypto" in body["messages"][-1]["content"]:
                content = json.dumps({"symbols": ["BTCUSDT"]})
            else:
                content = json.dumps(
                    {
                        "decisions": [
                            {
                                "symbol": "BTCUSDT",
                                "action": "WAIT",
                                "confidence": 0.4,
                                "source": "llm evidence snapshot",
                                "thesis": "Evidence is thin",
                            }
                        ]
                    }
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        analyst = LlmAnalyst(
            nvidia_key="nv-test",
            nvidia_model="nvidia/retired",
            openrouter_key="or-test",
            timeout=5,
            daily_calls=20,
            clock=lambda: clock["now"],
            transport=httpx.MockTransport(handler),
        )
        with self.assertLogs("warp3c", level="WARNING") as logs:
            analyst.research(("BTCUSDT",), {"BTCUSDT": item}, 30)
            clock["now"] = now + timedelta(minutes=30)
            analyst.research(("BTCUSDT",), {"BTCUSDT": item}, 30)
        self.assertEqual(nvidia_calls, ["nvidia/retired"])
        self.assertEqual(sum("llm_provider_unhealthy" in line and "HTTP 410" in line for line in logs.output), 1)
        health = analyst.health()
        self.assertEqual(health["nvidia"]["state"], "cooldown")
        self.assertEqual(health["nvidia"]["detail"], "HTTP 410")
        self.assertFalse(health["nvidia"]["healthy"])
        self.assertEqual(health["openrouter"]["state"], "ok")
        clock["now"] = now + timedelta(hours=1, seconds=1)
        with self.assertLogs("warp3c", level="WARNING"):
            analyst.research(("BTCUSDT",), {"BTCUSDT": item}, 30)
        self.assertEqual(nvidia_calls, ["nvidia/retired", "nvidia/retired"])
        analyst.close()

    def test_missing_model_and_transient_errors(self):
        item = evidence()
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "nvidia" in request.url.host:
                seen.append(404 if len(seen) == 0 else 503)
                return httpx.Response(seen[-1], json={"error": "no"})
            content = json.dumps({"symbols": ["BTCUSDT"]})
            if "closer spot-crypto" not in request.content.decode():
                content = json.dumps(
                    {
                        "decisions": [
                            {
                                "symbol": "BTCUSDT",
                                "action": "SKIP",
                                "confidence": 0.2,
                                "source": "llm evidence snapshot",
                                "thesis": "No setup",
                            }
                        ]
                    }
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        analyst = LlmAnalyst(
            nvidia_key="nv-test",
            openrouter_key="or-test",
            timeout=5,
            daily_calls=20,
            transport=httpx.MockTransport(handler),
        )
        with self.assertLogs("warp3c", level="WARNING") as gone:
            analyst.research(("BTCUSDT",), {"BTCUSDT": item}, 30)
        self.assertIn("HTTP 404", gone.output[0])
        self.assertEqual(analyst.health()["nvidia"]["state"], "cooldown")
        analyst._until.clear()
        before = len(seen)
        with self.assertNoLogs("warp3c", level="WARNING"):
            analyst.research(("BTCUSDT",), {"BTCUSDT": item}, 30)
            analyst.research(("BTCUSDT",), {"BTCUSDT": item}, 30)
        self.assertEqual(seen[before:], [503, 503])
        self.assertEqual(analyst.health()["nvidia"]["state"], "ok")
        analyst.close()

    def test_nvidia_model_comes_from_warp_env(self):
        keys = ("WARP_NVIDIA_MODEL", "NVIDIA_MODEL")
        saved = {key: os.environ.get(key) for key in keys}
        try:
            os.environ.pop("WARP_NVIDIA_MODEL", None)
            os.environ.pop("NVIDIA_MODEL", None)
            self.assertEqual(Settings.from_env().nvidia_model, DEFAULT_NVIDIA_MODEL)
            os.environ["NVIDIA_MODEL"] = "nvidia/legacy"
            self.assertEqual(Settings.from_env().nvidia_model, "nvidia/legacy")
            os.environ["WARP_NVIDIA_MODEL"] = "nvidia/nemotron-3.5-lightning-30b-a3b"
            self.assertEqual(Settings.from_env().nvidia_model, "nvidia/nemotron-3.5-lightning-30b-a3b")
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_status_snapshot_shows_a_cooling_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"))
            journal = OrderJournal(cfg.db_path)
            from test_failures import Markets, Research

            engine = Engine(cfg, _Quiet(), Research(), journal, Markets())
            engine.analyst = LlmAnalyst(nvidia_key="nv-test", nvidia_model=DEFAULT_NVIDIA_MODEL, openrouter_key="or-test")
            with self.assertLogs("warp3c", level="WARNING"):
                engine.analyst._cool("nvidia", DEFAULT_NVIDIA_MODEL, 410)
            publish(cfg, journal, engine)
            from warp3c.operator import SNAPSHOT

            nvidia = SNAPSHOT["/api/status"]["providers"]["nvidia"]
            self.assertEqual(nvidia["state"], "cooldown")
            self.assertEqual(nvidia["model"], DEFAULT_NVIDIA_MODEL)
            journal.close()
            engine.analyst.close()


class DashboardPathTests(unittest.TestCase):
    def test_explicit_asset_directory_wins(self):
        previous = os.environ.get("WARP_WEB_DIST")
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<!doctype html><meta name=viewport>", encoding="utf-8")
            os.environ["WARP_WEB_DIST"] = str(dist)
            try:
                self.assertEqual(web_root(), dist)
            finally:
                if previous is None:
                    os.environ.pop("WARP_WEB_DIST", None)
                else:
                    os.environ["WARP_WEB_DIST"] = previous


class _Quiet:
    def get_account(self, code, exchange_code):
        from warp3c.models import Account

        return Account(code, True, Decimal("1000"), Decimal("0"), Decimal("1000"))

    def get_live(self):
        return []

    def get_strategy(self, client_id):
        from warp3c.threecommas import StrategyNotFound

        raise StrategyNotFound(client_id)


if __name__ == "__main__":
    unittest.main()
