"""Research observability and tradebook P2 fixes. Fixtures only. No network."""

import io
import json
import logging
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx
from test_core import evidence
from test_failures import ExplodingBroker, Markets, paper
from test_shared_account import _closed, live_cfg
from test_tradebook import fresh, policy

from warp3c.capital import (
    Exits,
    apply_ladder,
    broker_safety_levels,
    build_ladder,
    constant_step_levels,
    portfolio_stress,
    readback_ok,
    scenario_stress,
    search_ladder,
    stress_loss,
)
from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.httpapi import _health
from warp3c.llm import LlmAnalyst, warn_budget_short
from warp3c.operator import HEALTH, SNAPSHOT, publish
from warp3c.risk import held_base_residual, managed_strategies, realized_pnl
from warp3c.schedule import Clocks, due_actions
from warp3c.state import OrderJournal
from warp3c.supervisor import ResearchGate, once, retry_delay
from warp3c.threecommas import IntegrationError
from warp3c.vibe import ResearchError


def _decision(symbol, action="WAIT"):
    return {
        "symbol": symbol,
        "action": action,
        "confidence": 0.5,
        "source": "llm evidence snapshot",
        "thesis": "Evidence snapshot supplied to the model",
    }


def _names(count):
    pool = (
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
        "AVAXUSDT", "LINKUSDT", "DOTUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT",
    )
    return pool[:count]


class ResearchLogTests(unittest.TestCase):
    def test_one_research_failed_line_per_failure_not_per_loop(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = Engine(cfg, ExplodingBroker(), None, journal, Markets())
            gate = ResearchGate()
            gate._error = ResearchError("LLM providers failed")
            gate._finished_at = now
            clocks = Clocks(scan=now, research=now, monitor=now, reconcile=now)
            with self.assertLogs("warp3c", level="INFO") as logs:
                for _ in range(50):
                    clocks = once(engine, gate, clocks, now=now)
            failed = [line for line in logs.output if "research_failed" in line]
            blocked = [line for line in logs.output if "research_blocked" in line]
            self.assertEqual(len(failed), 1)
            self.assertEqual(blocked, [])
            payload = json.loads(failed[0][failed[0].index("{"):])
            self.assertEqual(payload["error_class"], "ResearchError")
            self.assertEqual(payload["error"], "LLM providers failed")
            self.assertIsNone(payload["cause_class"])

    def test_backoff_schedule_and_reset_on_success(self):
        self.assertEqual(
            [retry_delay(n, 60, 300) for n in range(1, 6)],
            [60, 120, 240, 300, 300],
        )
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        calls = {"n": 0}

        def collect():
            calls["n"] += 1
            if calls["n"] < 5:
                raise ResearchError("LLM providers failed")
            return []

        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            cfg = Settings(
                mode="paper", db_path=cfg.db_path, api_key="k", api_secret="s", profile_code="p123",
                sleeve_pct=Decimal("1"), research_seconds=300, research_retry_seconds=60,
            )
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = Engine(cfg, ExplodingBroker(), None, journal, Markets())
            engine.collect_research = collect
            gate = ResearchGate()
            clocks = Clocks()

            def drive(moment, clocks):
                clocks = once(engine, gate, clocks, now=moment)
                if gate._thread is not None:
                    gate._thread.join(2)
                return once(engine, gate, clocks, now=moment)

            clocks = drive(now, clocks)
            self.assertEqual(engine.research_status["consecutive_failures"], 1)
            self.assertEqual(engine.research_status["state"], "backoff")
            self.assertEqual(
                engine.research_status["next_attempt_at"],
                (now + timedelta(seconds=60)).isoformat(),
            )
            self.assertNotIn("research", due_actions(clocks, now + timedelta(seconds=59), cfg))
            clocks = drive(now + timedelta(seconds=60), clocks)
            self.assertEqual(engine.research_status["consecutive_failures"], 2)
            self.assertEqual(
                engine.research_status["next_attempt_at"],
                (now + timedelta(seconds=60 + 120)).isoformat(),
            )
            clocks = drive(now + timedelta(seconds=180), clocks)
            self.assertEqual(engine.research_status["consecutive_failures"], 3)
            self.assertEqual(
                engine.research_status["next_attempt_at"],
                (now + timedelta(seconds=180 + 240)).isoformat(),
            )
            clocks = drive(now + timedelta(seconds=420), clocks)
            self.assertEqual(engine.research_status["consecutive_failures"], 4)
            self.assertEqual(engine.research_status["state"], "backoff")
            self.assertEqual(
                engine.research_status["next_attempt_at"],
                (now + timedelta(seconds=420 + 300)).isoformat(),
            )
            clocks = drive(now + timedelta(seconds=720), clocks)
            self.assertEqual(calls["n"], 5)
            self.assertEqual(engine.research_status["state"], "ok")
            self.assertEqual(engine.research_status["consecutive_failures"], 0)
            calls["n"] = 0

            def fail_again():
                calls["n"] += 1
                raise ResearchError("LLM providers failed")

            engine.collect_research = fail_again
            moment = now + timedelta(seconds=720 + 300)
            clocks = drive(moment, clocks)
            self.assertEqual(engine.research_status["consecutive_failures"], 1)
            self.assertEqual(
                engine.research_status["next_attempt_at"],
                (moment + timedelta(seconds=60)).isoformat(),
            )

    def test_cap_and_unconfigured_do_not_retry_early(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        for message, state, code in (
            ("LLM daily cap reached", "capped", "llm_cap"),
            ("LLM providers are not configured", "failed", "unconfigured"),
        ):
            with self.subTest(message=message):
                calls = {"n": 0}

                def collect(message=message, counter=calls):
                    counter["n"] += 1
                    raise ResearchError(message)

                with tempfile.TemporaryDirectory() as tmp:
                    cfg = paper(tmp)
                    journal = OrderJournal(cfg.db_path)
                    self.addCleanup(journal.close)
                    engine = Engine(cfg, ExplodingBroker(), None, journal, Markets())
                    engine.collect_research = collect
                    gate = ResearchGate()
                    clocks = once(engine, gate, Clocks(), now=now)
                    if gate._thread is not None:
                        gate._thread.join(2)
                    clocks = once(engine, gate, clocks, now=now)
                    self.assertEqual(calls["n"], 1)
                    self.assertEqual(engine.research_status["state"], state)
                    self.assertEqual(engine.research_status["error_code"], code)
                    self.assertNotIn("research", due_actions(clocks, now + timedelta(seconds=60), cfg))
                    self.assertIn("research", due_actions(clocks, now + timedelta(seconds=cfg.research_seconds), cfg))

    def test_a_late_failure_is_settled_before_the_next_start(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        calls = {"n": 0}

        def collect():
            calls["n"] += 1
            raise ResearchError("LLM providers failed")

        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            cfg = Settings(
                mode="paper", db_path=cfg.db_path, api_key="k", api_secret="s", profile_code="p123",
                sleeve_pct=Decimal("1"), research_seconds=30, research_retry_seconds=30,
            )
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = Engine(cfg, ExplodingBroker(), None, journal, Markets())
            engine.collect_research = collect
            gate = ResearchGate()
            gate._error = ResearchError("LLM providers failed")
            gate._finished_at = now
            gate._generation = 1
            clocks = Clocks(
                scan=now, monitor=now, reconcile=now,
                research=now - timedelta(seconds=30),
            )
            with self.assertLogs("warp3c", level="INFO") as logs:
                clocks = once(engine, gate, clocks, now=now)
            failed = [line for line in logs.output if "research_failed" in line]
            self.assertEqual(len(failed), 1)
            self.assertEqual(calls["n"], 0)
            self.assertEqual(engine.research_status["consecutive_failures"], 1)
            self.assertEqual(engine.research_status["state"], "backoff")
            self.assertNotIn("research", due_actions(clocks, now, cfg))
            self.assertIn("research", due_actions(clocks, now + timedelta(seconds=30), cfg))


class ResearchBudgetTests(unittest.TestCase):
    def test_budget_persists_across_a_new_analyst(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "warp.db")
            first = OrderJournal(path)
            analyst = LlmAnalyst(openrouter_key="present", daily_calls=3, daily_usd=Decimal("2"), usd_per_call=Decimal("0.02"))
            analyst.bind_journal(first)
            analyst.budget.charge()
            analyst.budget.charge()
            first.close()
            second = OrderJournal(path)
            self.addCleanup(second.close)
            restarted = LlmAnalyst(
                openrouter_key="present", daily_calls=3, daily_usd=Decimal("2"), usd_per_call=Decimal("0.02"),
            )
            restarted.bind_journal(second)
            view = restarted.budget.snapshot()
            self.assertEqual(view["calls_used"], 2)
            self.assertEqual(view["calls_cap"], 3)
            self.assertEqual(Decimal(view["usd_used"]), Decimal("0.04"))
            restarted.budget.charge()
            with self.assertRaisesRegex(ResearchError, "LLM daily cap reached"):
                restarted.budget.charge()
            self.assertEqual(restarted.budget.snapshot()["calls_used"], 3)

    def test_short_budget_is_logged_and_defaults_stay(self):
        warn_budget_short.fired = False
        self.assertEqual(Settings().llm_daily_calls, 48)
        self.assertEqual(Settings().research_seconds, 900)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"))
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            with self.assertLogs("warp3c", level="WARNING") as logs:
                Engine(cfg, None, None, journal, None)
        self.assertTrue(any("llm_budget_short" in line for line in logs.output))
        payload = json.loads(logs.output[0][logs.output[0].index("{"):])
        self.assertEqual(payload["calls_cap"], 48)
        self.assertEqual(payload["calls_needed"], "192")
        self.assertEqual(payload["assumed_calls_per_run"], 2)
        self.assertEqual(payload["fallback_calls_per_run"], 4)

    def test_unreadable_budget_does_not_crash_publish_or_call_the_provider(self):
        requests = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            requests["n"] += 1
            return httpx.Response(500, json={"error": "no"})

        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            journal.set_setting("llm_budget", "{")
            analyst = LlmAnalyst(
                openrouter_key="present",
                timeout=5,
                transport=httpx.MockTransport(handler),
            )
            self.addCleanup(analyst.close)
            engine = Engine(cfg, ExplodingBroker(), None, journal, Markets(), analyst=analyst)
            publish(cfg, journal, engine)
            budget = SNAPSHOT["/api/status"]["providers"]["budget"]
            self.assertEqual(budget["state"], "unreadable")
            self.assertEqual(budget["calls_cap"], cfg.llm_daily_calls)
            self.assertNotIn("calls_used", budget)
            with self.assertRaisesRegex(ResearchError, "LLM budget unreadable"):
                analyst.research(("BTCUSDT",), {"BTCUSDT": evidence("BTCUSDT")}, 30)
            self.assertEqual(requests["n"], 0)
            gate = ResearchGate()
            now = datetime(2026, 11, 26, tzinfo=timezone.utc)
            engine.universe = ("BTCUSDT",)
            engine.evidence = {"BTCUSDT": evidence("BTCUSDT")}
            engine.status.reason = "ok"
            clocks = once(
                engine, gate,
                Clocks(scan=now, monitor=now, reconcile=now),
                now=now,
            )
            if gate._thread is not None:
                gate._thread.join(2)
            once(engine, gate, clocks, now=now)
            self.assertEqual(requests["n"], 0)
            self.assertEqual(engine.status.reason, "research_blocked:research_error")
            self.assertEqual(engine.research_status["error_code"], "research_error")


class ResearchSurfaceTests(unittest.TestCase):
    def _engine(self, tmp):
        cfg = paper(tmp)
        journal = OrderJournal(cfg.db_path)
        self.addCleanup(journal.close)
        return cfg, journal, Engine(cfg, ExplodingBroker(), None, journal, Markets())

    def test_cap_surfaces_on_health_without_flipping_dependencies(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            cfg, journal, engine = self._engine(tmp)
            gate = ResearchGate()
            gate._error = ResearchError("LLM daily cap reached")
            gate._finished_at = now
            once(engine, gate, Clocks(scan=now, research=now, reconcile=now), now=now)
            self.assertEqual(engine.status.reason, "research_blocked:llm_cap")
            self.assertTrue(engine.status.dependencies_healthy)
            self.assertTrue(engine.status.risk_permitted)
            self.assertFalse(engine.status.reason in {"ok", "observe_without_account"} and not engine.status.risk_permitted)
            notify = engine.status.reason not in {"ok", "observe_without_account"} and not engine.status.risk_permitted
            self.assertFalse(notify)
            HEALTH["last_cycle_ok"] = engine.status.dependencies_healthy
            HEALTH["dependencies_healthy"] = engine.status.dependencies_healthy
            HEALTH["risk_permitted"] = engine.status.risk_permitted
            HEALTH["reason"] = engine.status.reason
            for path in ("/health", "/live", "/ready"):
                status, _headers, body = _health(path)
                payload = json.loads(body)
                self.assertEqual(status, 200, path)
                self.assertEqual(payload["reason"], "research_blocked:llm_cap")
                self.assertTrue(payload["dependencies_healthy"])
                self.assertTrue(payload["last_cycle_ok"])
            publish(cfg, journal, engine)
            self.assertEqual(SNAPSHOT["/api/status"]["research"]["state"], "capped")
            self.assertEqual(SNAPSHOT["/api/status"]["research"]["error_code"], "llm_cap")
            self.assertEqual(SNAPSHOT["/api/status"]["pipeline"]["analysis"]["state"], "blocked")
            self.assertIn("budget", SNAPSHOT["/api/status"]["providers"])

    def test_halt_reason_beats_research_blocked(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, _journal, engine = self._engine(tmp)
            open(os.path.join(tmp, "HALT"), "w").close()
            gate = ResearchGate()
            gate._error = ResearchError("LLM daily cap reached")
            gate._finished_at = now
            once(engine, gate, Clocks(scan=now, research=now, reconcile=now), now=now)
            self.assertEqual(engine.status.reason, "halt")


class ResearchParseTests(unittest.TestCase):
    def _analyst(self, handler):
        return LlmAnalyst(
            openrouter_key="present",
            timeout=5,
            daily_calls=20,
            transport=httpx.MockTransport(handler),
        )

    def test_twelve_decisions_for_an_eight_symbol_shortlist_keep_eight(self):
        names = _names(12)
        short = names[:8]
        seen = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["n"] += 1
            content = json.dumps({"decisions": [_decision(symbol) for symbol in names]})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        analyst = self._analyst(handler)
        signals = analyst.research(tuple(names), {symbol: evidence(symbol) for symbol in names}, 30)
        analyst.close()
        self.assertEqual(seen["n"], 1)
        self.assertEqual([item.symbol for item in signals], list(short))

    def test_out_of_set_symbol_is_dropped_and_duplicates_still_fail(self):
        def handler(request: httpx.Request) -> httpx.Response:
            content = json.dumps({"decisions": [_decision("BTCUSDT"), _decision("ETHUSDT")]})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        analyst = self._analyst(handler)
        signals = analyst.research(("BTCUSDT",), {"BTCUSDT": evidence()}, 30)
        analyst.close()
        self.assertEqual([item.symbol for item in signals], ["BTCUSDT"])

        def duplicate(request: httpx.Request) -> httpx.Response:
            content = json.dumps({"decisions": [_decision("BTCUSDT"), _decision("BTCUSDT")]})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        analyst = self._analyst(duplicate)
        with self.assertRaisesRegex(ResearchError, "Duplicate symbols"):
            analyst.research(("BTCUSDT",), {"BTCUSDT": evidence()}, 30)
        analyst.close()

        def missing(request: httpx.Request) -> httpx.Response:
            content = json.dumps({"decisions": [{"symbol": "BTCUSDT"}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        analyst = self._analyst(missing)
        with self.assertRaises(ValueError):
            analyst.research(("BTCUSDT",), {"BTCUSDT": evidence()}, 30)
        analyst.close()

        def empty(request: httpx.Request) -> httpx.Response:
            content = json.dumps({"decisions": [_decision("ETHUSDT")]})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        analyst = self._analyst(empty)
        self.assertEqual(analyst.research(("BTCUSDT",), {"BTCUSDT": evidence()}, 30), [])
        analyst.close()

    def test_logs_do_not_contain_the_provider_key(self):
        secret = "sk-TESTKEY"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers.get("Authorization") != "Bearer " + secret:
                raise AssertionError("header missing in the mock")
            return httpx.Response(500, json={"error": secret})

        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            analyst = LlmAnalyst(openrouter_key=secret, timeout=5, transport=httpx.MockTransport(handler))
            engine = Engine(cfg, ExplodingBroker(), None, journal, Markets())
            engine.analyst = analyst

            def collect():
                return analyst.research(("BTCUSDT",), {"BTCUSDT": evidence()}, 30)

            engine.collect_research = collect
            gate = ResearchGate()
            logger = logging.getLogger("warp3c")
            buf = io.StringIO()
            sink = logging.StreamHandler(buf)
            sink.setLevel(logging.INFO)
            logger.addHandler(sink)
            previous = logger.level
            logger.setLevel(logging.INFO)
            try:
                clocks = once(engine, gate, Clocks(), now=now)
                if gate._thread is not None:
                    gate._thread.join(2)
                once(engine, gate, clocks, now=now)
            finally:
                logger.removeHandler(sink)
                logger.setLevel(previous)
                analyst.close()
            text = buf.getvalue()
            self.assertIn("research_failed", text)
            self.assertIn("openrouter", text)
            self.assertIn("500", text)
            self.assertNotIn(secret, text)
            self.assertNotIn("Authorization", text)
            self.assertNotIn("Bearer", text)


class LadderSpacingTests(unittest.TestCase):
    def test_payload_forces_constant_step_and_position_averaging(self):
        ladder = build_ladder(
            price=Decimal("100"), commitment=Decimal("300"), safety_orders=5,
            scale=Decimal("1.2"), step=Decimal("0.02"), policy=policy(),
        )
        payload = apply_ladder(
            {
                "extraOrderDeviationMultiplier": Decimal("1.25"),
                "applyDcaForFirstSafetyOrder": True,
            },
            ladder,
            Exits(),
        )
        self.assertEqual(payload["extraOrderDeviationMultiplier"], Decimal("1"))
        self.assertIs(payload["applyDcaForFirstSafetyOrder"], False)
        self.assertTrue(readback_ok(payload, Exits(), ladder))
        echoed = dict(payload)
        echoed["extraOrderDeviationMultiplier"] = Decimal("1.2")
        self.assertFalse(readback_ok(echoed, Exits(), ladder))
        flagged = dict(payload)
        flagged["applyDcaForFirstSafetyOrder"] = True
        self.assertFalse(readback_ok(flagged, Exits(), ladder))
        self.assertTrue(readback_ok({"strategyId": "sid"}, Exits(), ladder))

    def test_multiplier_one_matches_the_constant_step_for_five_through_eight(self):
        price = Decimal("100")
        step = Decimal("0.02")
        for count in range(5, 9):
            ladder = build_ladder(
                price=price, commitment=Decimal("300"), safety_orders=count,
                scale=Decimal("1"), step=step, policy=policy(),
            )
            self.assertIsNotNone(ladder)
            simulated = constant_step_levels(price, step, count)
            formula = broker_safety_levels(price, step, Decimal("1"), count)
            self.assertEqual(formula, simulated)
            coins = ladder.base / price
            for index, level in enumerate(formula, start=1):
                coins += ladder.orders[index] / level
            self.assertEqual(coins, ladder.coins)
            self.assertNotEqual(
                broker_safety_levels(price, step, Decimal("1.2"), count),
                simulated,
            )


class RealizedFeeTests(unittest.TestCase):
    def _row(self):
        row = _closed("warp3c-" + "ab" * 16, entry="110.8984", exit_cost="110.8436")
        row["totalEntryAmount"] = "1"
        row["totalExitAmount"] = "1"
        row["totalEntryCommissionCost"] = "0.04435936"
        row["totalExitCommissionCost"] = "0.04433744"
        return row

    def test_schema_example_subtracts_quote_commission(self):
        net, fee = realized_pnl(self._row())
        self.assertEqual(fee, Decimal("0.08869680"))
        self.assertEqual(net, Decimal("-0.0548") - Decimal("0.08869680"))

    def test_missing_commission_field_halts(self):
        row = self._row()
        row.pop("totalExitCommissionCost")
        with self.assertRaisesRegex(IntegrationError, "Realized P&L unreadable"):
            realized_pnl(row)

    def test_more_negative_total_profit_loss_is_used(self):
        row = self._row()
        computed = Decimal("-0.0548") - Decimal("0.08869680")
        row["totalProfitLoss"] = "-20000"
        net, _fee = realized_pnl(row)
        self.assertEqual(net, Decimal("-2"))
        self.assertLess(net, computed)
        higher = self._row()
        higher["totalProfitLoss"] = "100000"
        self.assertEqual(realized_pnl(higher)[0], computed)

    def test_official_schema_example_with_base_commission_books(self):
        row = self._row()
        row["totalEntryAmount"] = "404"
        row["totalExitAmount"] = "404"
        row["totalEntryCommission"] = "0.1616"
        row["totalExitCommission"] = "0.1616"
        row["totalProfitLoss"] = "-1256"
        self.assertEqual(held_base_residual(row), Decimal("0"))
        net, fee = realized_pnl(row, mark=Decimal("0.2744"), min_notional=Decimal("5"))
        self.assertEqual(fee, Decimal("0.08869680"))
        self.assertEqual(net, Decimal("-0.14349680"))
        managed_strategies(
            [row], "p123", demo=False, exchange_code="BINANCE", shared=True,
            journaled=frozenset({row["clientId"]}),
        )

    def test_exit_above_entry_still_raises(self):
        row = self._row()
        row["totalEntryAmount"] = "404"
        row["totalExitAmount"] = "405"
        with self.assertRaisesRegex(IntegrationError, "Exit amount exceeds entry amount"):
            held_base_residual(row)


class DustTests(unittest.TestCase):
    def _row(self, *, entry="1", exit_amount="0.997", entry_fee="0"):
        row = _closed("warp3c-" + "cd" * 16, entry="100", exit_cost="99.70")
        row["totalEntryAmount"] = entry
        row["totalExitAmount"] = exit_amount
        if entry_fee != "0":
            row["totalEntryCommission"] = entry_fee
        return row

    def test_small_residual_is_booked_and_a_large_one_halts(self):
        net, _fee = realized_pnl(
            self._row(), mark=Decimal("100"), min_notional=Decimal("5"), dust_usdt=Decimal("1"),
        )
        self.assertEqual(net, Decimal("99.70") - Decimal("100") - Decimal("0.30"))
        with self.assertRaisesRegex(IntegrationError, "still holds base"):
            realized_pnl(
                self._row(exit_amount="0.94"),
                mark=Decimal("100"), min_notional=Decimal("10"), dust_usdt=Decimal("1"),
            )

    def test_missing_mark_halts_and_base_fee_can_net_to_zero(self):
        with self.assertRaisesRegex(IntegrationError, "still holds base"):
            realized_pnl(self._row(), mark=None, min_notional=Decimal("5"))
        stale = self._row()
        with self.assertRaisesRegex(IntegrationError, "still holds base"):
            realized_pnl(stale, min_notional=Decimal("5"))
        row = self._row(entry="1", exit_amount="0.999", entry_fee="0.001")
        self.assertEqual(held_base_residual(row), Decimal("0"))
        with self.assertNoLogs("warp3c", level="INFO"):
            net, fee = realized_pnl(row)
        self.assertEqual(fee, Decimal("0"))
        self.assertEqual(net, Decimal("99.70") - Decimal("100"))
        with self.assertRaisesRegex(IntegrationError, "still holds base"):
            realized_pnl(
                self._row(exit_amount="0.94"),
                mark=Decimal("100"), min_notional=Decimal("10"), dust_usdt=Decimal("1"),
            )

    def test_engine_books_dust_inside_the_sleeve_and_does_not_sell(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = live_cfg(tmp, sleeve_usdt=Decimal("250"))
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = Engine(cfg, ExplodingBroker(), None, journal, Markets())
            engine.evidence = {"SOLUSDT": evidence("SOLUSDT")}
            row = self._row()
            row["pair"] = "SOLUSDT"
            bot = {"client_id": row["clientId"], "symbol": "SOLUSDT", "warp_id": "dustdust"}
            journal.reserve(row["clientId"], "SOLUSDT", {"pairCode": "SOLUSDT"})
            with self.assertLogs("warp3c", level="INFO") as logs:
                engine._book_realized(bot, row)
            self.assertTrue(any("dust_residual" in line for line in logs.output))
            booked = journal.realized_pnl()[0]["realized_usdt"]
            self.assertEqual(booked, Decimal("-0.60"))
            self.assertFalse(hasattr(engine.broker, "sell"))
            marks = {"SOLUSDT": (Decimal("100"), Decimal("5"))}
            managed_strategies(
                [row], "p123", demo=False, exchange_code="BINANCE", shared=True,
                journaled=frozenset({row["clientId"]}), dust_marks=marks, dust_usdt=Decimal("1"),
            )
            with self.assertRaisesRegex(IntegrationError, "still holds base"):
                managed_strategies(
                    [self._row(exit_amount="0.94")], "p123", demo=False, exchange_code="BINANCE",
                    shared=True, journaled=frozenset({row["clientId"]}),
                    dust_marks=marks, dust_usdt=Decimal("1"),
                )


class GapStressTests(unittest.TestCase):
    def _old(self, deals, chosen):
        shocks = (Decimal("0.10"), Decimal("0.20"), Decimal("0.30"), Decimal("0.40"))
        worst = Decimal("0")
        per_shock = []
        for shock in shocks:
            total = Decimal("0")
            for commitment, stop in deals:
                shocked = commitment * shock
                slip = commitment * chosen.stress_slippage
                total += (shocked + slip) if shocked <= stop else (stop + slip)
            per_shock.append(total)
            worst = max(worst, total)
        return worst, per_shock

    def _new_shock(self, commitment, stop, shock, chosen):
        shocked = commitment * shock
        if shocked <= stop or commitment <= 0:
            return shocked + commitment * chosen.stress_slippage
        gap = (shock - (stop / commitment)) * chosen.stress_gap_share
        return stop + commitment * max(chosen.stress_slippage, gap)

    def test_new_loss_is_never_below_the_old_one(self):
        chosen = policy()
        shocks = (Decimal("0.10"), Decimal("0.20"), Decimal("0.30"), Decimal("0.40"))
        books = [
            [(Decimal("100"), Decimal("5"))],
            [(Decimal("100"), Decimal("17"))],
            [(Decimal("100"), Decimal("50"))],
            [(Decimal("37.50"), Decimal("2.916")), (Decimal("80"), Decimal("10"))],
        ]
        for deals in books:
            old, old_shocks = self._old(deals, chosen)
            new_shocks = []
            for shock in shocks:
                new_shocks.append(sum((self._new_shock(c, s, shock, chosen) for c, s in deals), Decimal("0")))
            for new_total, old_total in zip(new_shocks, old_shocks, strict=True):
                self.assertGreaterEqual(new_total, old_total)
            self.assertGreaterEqual(scenario_stress(deals, chosen), old)
            self.assertEqual(scenario_stress(deals, chosen), max(new_shocks))

    def test_forty_percent_gap_loses_more_than_stop_plus_one_percent(self):
        chosen = policy()
        loss = scenario_stress([(Decimal("100"), Decimal("17"))], chosen)
        self.assertEqual(loss, Decimal("28.5"))
        self.assertGreater(loss, Decimal("17") + Decimal("100") * chosen.stress_slippage)

    def test_e250_single_deal_stays_approved(self):
        chosen = policy(sleeve_usdt=Decimal("250"), sleeve_pct=Decimal("0"))
        decision = fresh(
            policy=chosen,
            account_usdt=Decimal("250"),
            free_usdt=Decimal("250"),
            max_commitment=Decimal("37.50"),
            min_qty=Decimal("0.00001"),
            step_size=Decimal("0.00001"),
        )
        self.assertEqual(decision.decision, "APPROVED")
        self.assertEqual(decision.ladder.commitment, Decimal("37.50"))
        stop = decision.ladder.stop_loss
        self.assertEqual(stop.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), Decimal("2.916"))
        self.assertLessEqual(stop, Decimal("0.08") * Decimal("250"))
        flat = stress_loss([decision.ladder.commitment], chosen)
        gap = scenario_stress([(decision.ladder.commitment, stop)], chosen)
        self.assertGreaterEqual(portfolio_stress(
            [(decision.ladder.commitment, stop)], chosen, flat_commitments=[decision.ladder.commitment],
        ), max(flat, gap))
        self.assertLessEqual(max(flat, gap), Decimal("20"))

    def test_three_deals_at_e1200_are_deterministic(self):
        chosen = policy(sleeve_usdt=Decimal("1200"), sleeve_pct=Decimal("0"))
        deals = [(Decimal("300"), Decimal("26"))] * 3
        shocks = (Decimal("0.10"), Decimal("0.20"), Decimal("0.30"), Decimal("0.40"))
        worst = Decimal("0")
        for shock in shocks:
            total = sum((self._new_shock(c, s, shock, chosen) for c, s in deals), Decimal("0"))
            worst = max(worst, total)
        self.assertEqual(scenario_stress(deals, chosen), worst)
        self.assertEqual(worst, Decimal("219"))
        one = search_ladder(
            price=Decimal("100"), cap=Decimal("300"), nav=Decimal("1200"),
            open_stop_risk=Decimal("0"), open_commitment=Decimal("0"),
            minimum=Decimal("5"), atr_1h_pct=Decimal("0.035"), policy=chosen,
        )
        self.assertIsNotNone(one)
        self.assertGreater(scenario_stress([(one.commitment, one.stop_loss)] * 3, chosen), Decimal("96"))


class RiskDecisionRetentionTests(unittest.TestCase):
    def _row(self, index):
        return {
            "recorded_at": "2026-11-26T00:00:00+00:00",
            "signal_id": f"SOLUSDT|{index}",
            "risk_decision_id": f"{index:08x}",
            "account_hash": "abc",
            "nav": "250",
            "free_usdt": "250",
            "reserved_so": "0",
            "commitment": "37.50",
            "ladder": "37.50",
            "stop_usdt": "2.916",
            "stop_pct": "0.01",
            "open_stop_after": "2.916",
            "decision": "APPROVED",
            "reasons": "[]",
        }

    def test_inserts_keep_the_newest_rows_and_the_status_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"))
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            for index in range(5100):
                journal.record_risk_decision(self._row(index))
            count = journal.db.execute("SELECT COUNT(*) FROM risk_decisions").fetchone()[0]
            self.assertEqual(count, 5000)
            ids = [row[0] for row in journal.db.execute("SELECT signal_id FROM risk_decisions ORDER BY id")]
            self.assertEqual(ids[0], "SOLUSDT|100")
            self.assertEqual(ids[-1], "SOLUSDT|5099")
            self.assertNotIn("SOLUSDT|99", ids)
            recent = journal.recent_risk_decisions(20)
            self.assertEqual(len(recent), 20)
            self.assertEqual(recent[0]["signal_id"], "SOLUSDT|5099")
            self.assertEqual(
                set(recent[0]),
                {
                    "recorded_at", "signal_id", "risk_decision_id", "account_hash", "nav",
                    "free_usdt", "reserved_so", "commitment", "stop_usdt", "stop_pct",
                    "open_stop_after", "decision", "reasons",
                },
            )
            engine = Engine(cfg, None, None, journal, None)
            publish(cfg, journal, engine)
            self.assertEqual(len(SNAPSHOT["/api/status"]["risk_decisions"]), 20)
            self.assertEqual(SNAPSHOT["/api/status"]["risk_decisions"][0]["signal_id"], "SOLUSDT|5099")

    def test_opening_an_existing_database_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "warp.db")
            raw = sqlite3.connect(path)
            raw.execute(
                """
                CREATE TABLE risk_decisions (
                    id INTEGER PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    signal_id TEXT NOT NULL,
                    risk_decision_id TEXT NOT NULL,
                    account_hash TEXT NOT NULL,
                    nav TEXT NOT NULL,
                    free_usdt TEXT NOT NULL,
                    reserved_so TEXT NOT NULL,
                    commitment TEXT NOT NULL,
                    ladder TEXT NOT NULL,
                    stop_usdt TEXT NOT NULL,
                    stop_pct TEXT NOT NULL,
                    open_stop_after TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reasons TEXT NOT NULL
                )
                """
            )
            raw.execute(
                """
                INSERT INTO risk_decisions(
                    recorded_at, signal_id, risk_decision_id, account_hash, nav, free_usdt,
                    reserved_so, commitment, ladder, stop_usdt, stop_pct, open_stop_after, decision, reasons
                ) VALUES('t','SOLUSDT|1','a','h','1','1','0','1','1','1','1','1','APPROVED','[]')
                """
            )
            raw.commit()
            raw.close()
            first = OrderJournal(path)
            second = OrderJournal(path)
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            self.assertEqual(first.db.execute("SELECT COUNT(*) FROM risk_decisions").fetchone()[0], 1)
            self.assertEqual(second.db.execute("SELECT COUNT(*) FROM risk_decisions").fetchone()[0], 1)
            second.record_risk_decision(self._row(2), keep=5000)
            self.assertEqual(first.db.execute("SELECT COUNT(*) FROM risk_decisions").fetchone()[0], 2)


class ConfigBoundTests(unittest.TestCase):
    def test_new_bounds_fail_closed(self):
        with self.assertRaises(ValueError):
            Settings(research_retry_seconds=10)
        with self.assertRaises(ValueError):
            Settings(research_seconds=900, research_retry_seconds=901)
        with self.assertRaises(ValueError):
            Settings(dust_usdt=Decimal("5.01"))
        with self.assertRaises(ValueError):
            Settings(stress_gap_share=Decimal("1.1"))
        with self.assertRaises(ValueError):
            Settings(risk_decisions_keep=99)
        self.assertEqual(Settings().research_retry_seconds, 60)
        self.assertEqual(Settings().dust_usdt, Decimal("1.00"))
        self.assertEqual(Settings().stress_gap_share, Decimal("0.50"))
        self.assertEqual(Settings().risk_decisions_keep, 5000)
