"""Zero-error behavior. No live credentials and no broker writes."""

import json
import logging
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from test_core import evidence, sig
from test_failures import ExplodingBroker, Markets, Research

from warp3c.__main__ import (
    CycleBlockTracker,
    note_blocked_cycle,
    note_cycle_recovered,
)
from warp3c.backtest import candles_for_backtest, run_backtest
from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.forward import evaluate_due
from warp3c.httpapi import dispatch
from warp3c.llm import LlmAnalyst, _evidence_lines
from warp3c.market import MarketData, MarketDataError, analyze, closed_candles
from warp3c.operator import HEALTH, _research_view, _xau_block
from warp3c.qualify import qualify
from warp3c.schedule import Clocks
from warp3c.state import OrderJournal
from warp3c.supervisor import ResearchGate, clocks_from_journal, once, paced_research_seconds
from warp3c.telemetry import _QUIET, _REDACT, _QuietHttpSuccess, install_http_redaction
from warp3c.threecommas import IntegrationError
from warp3c.xau.candles import Bar
from warp3c.xau.config import XauSettings
from warp3c.xau.profiles import load_profile
from warp3c.xau.runner import XauRunner
from warp3c.xau.status import HUB

T0 = datetime(2026, 11, 26, 4, 0, tzinfo=timezone.utc)


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


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


def _events(journal) -> list[str]:
    return [row["event"] for row in journal.recent_alerts(30)]


class _Clock:
    def __init__(self, moment: datetime):
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


class _Feeds:
    def __init__(self):
        self.name = "binance"
        self.spot_price = Decimal("2300")
        self.bars: dict = {"1m": []}

    def book(self):
        return self.name, self.bars

    def spot(self):
        return self.spot_price

    def dxy(self):
        return None

    def ff_calendar(self):
        return None


def _arm(feeds: _Feeds, moment: datetime, gap: str) -> None:
    opened = moment - timedelta(minutes=1)
    price = Decimal("2300") + Decimal(gap)
    feeds.spot_price = Decimal("2300")
    feeds.bars = {"1m": [Bar(opened, price, price, price, price, Decimal("1"), "1m")]}


def _runner(tmp: str, moment: datetime):
    alerts: list[str] = []
    settings = XauSettings()
    profile = load_profile(settings.profile_path)
    clock = _Clock(moment)
    feeds = _Feeds()
    runner = XauRunner(
        os.path.join(tmp, "xau.db"),
        lambda event, _detail: alerts.append(event) or True,
        settings,
        profile,
        clock=clock,
        feeds=feeds,
        cycle=None,
    )
    return runner, clock, feeds, alerts


def _basis_alerts(alerts: list[str]) -> list[str]:
    return [event for event in alerts if "basis_" in event]


class ForwardTests(unittest.TestCase):
    def test_attempt_cap_skips_horizons_that_already_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            start = T0 - timedelta(hours=80)
            for _ in range(10):
                journal.record_verdict("BTCUSDT", "BUY", Decimal("0.8"), Decimal("100"), start)
            calls = {"n": 0}

            def price_at(_symbol, _moment):
                calls["n"] += 1
                return None

            self.assertEqual(evaluate_due(journal, price_at, T0, limit=20), 0)
            self.assertEqual(calls["n"], 20)
            self.assertEqual(evaluate_due(journal, price_at, T0, limit=20), 0)
            self.assertEqual(calls["n"], 30)
            journal.close()

    def test_supervisor_scores_rows_without_cycle_and_a_lookup_failure_stays_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"), scan_seconds=30, research_seconds=3600)
            journal = OrderJournal(cfg.db_path)
            journal.record_verdict("BTCUSDT", "BUY", Decimal("0.8"), Decimal("100"), T0 - timedelta(hours=80))
            market = Markets()
            market.price_at = lambda _symbol, _moment: Decimal("110")
            engine = Engine(cfg, ExplodingBroker(), Research(), journal, market)
            engine.cycle = lambda: (_ for _ in ()).throw(AssertionError("cycle"))
            gate = ResearchGate()
            engine.collect_research = lambda: []
            once(engine, gate, Clocks(research=T0, monitor=T0, reconcile=T0), now=T0)
            if gate._thread is not None:
                gate._thread.join(2)
            row = journal.verdict_rows()[0]
            self.assertIsNotNone(row["move_4h"])
            self.assertIsNotNone(row["move_24h"])
            self.assertIsNotNone(row["move_72h"])

            def broken(_symbol, _moment):
                raise MarketDataError("lookup failed")

            market.price_at = broken
            later = T0 + timedelta(seconds=30)
            once(engine, gate, Clocks(research=later, monitor=later, reconcile=later), now=later)

            def explode(_now):
                raise RuntimeError("forward broke")

            engine._evaluate = explode
            grab = _Grab()
            logger = logging.getLogger("warp3c")
            previous = logger.level
            logger.setLevel(logging.INFO)
            logger.addHandler(grab)
            try:
                once(
                    engine,
                    gate,
                    Clocks(research=later, monitor=later, reconcile=later),
                    now=later + timedelta(seconds=30),
                )
                once(
                    engine,
                    gate,
                    Clocks(research=later, monitor=later, reconcile=later),
                    now=later + timedelta(seconds=60),
                )
            finally:
                logger.removeHandler(grab)
                logger.setLevel(previous)
            failed = [line for line in grab.lines if "forward_eval_failed" in line]
            self.assertEqual(len(failed), 1)
            journal.close()

    def test_failed_lookup_retries_after_backoff_and_then_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            journal.record_verdict("BTCUSDT", "BUY", Decimal("0.8"), Decimal("100"), T0 - timedelta(hours=80))
            calls = {"n": 0}

            def price_at(_symbol, _moment):
                calls["n"] += 1
                if calls["n"] <= 3:
                    return None
                return Decimal("110")

            self.assertEqual(evaluate_due(journal, price_at, T0, limit=20), 0)
            self.assertEqual(calls["n"], 3)
            self.assertEqual(evaluate_due(journal, price_at, T0 + timedelta(seconds=59), limit=20), 0)
            self.assertEqual(calls["n"], 3)
            self.assertEqual(evaluate_due(journal, price_at, T0 + timedelta(seconds=60), limit=20), 3)
            self.assertEqual(calls["n"], 6)
            row = journal.verdict_rows()[0]
            self.assertIsNotNone(row["move_4h"])
            self.assertIsNotNone(row["move_24h"])
            self.assertIsNotNone(row["move_72h"])
            journal.close()

    def test_a_second_miss_doubles_the_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            journal.record_verdict("BTCUSDT", "BUY", Decimal("0.8"), Decimal("100"), T0 - timedelta(hours=80))
            calls = {"n": 0}

            def price_at(_symbol, _moment):
                calls["n"] += 1
                return None

            evaluate_due(journal, price_at, T0, limit=20)
            evaluate_due(journal, price_at, T0 + timedelta(seconds=60), limit=20)
            self.assertEqual(calls["n"], 6)
            evaluate_due(journal, price_at, T0 + timedelta(seconds=179), limit=20)
            self.assertEqual(calls["n"], 6)
            evaluate_due(journal, price_at, T0 + timedelta(seconds=180), limit=20)
            self.assertEqual(calls["n"], 9)
            journal.close()

    def test_hourly_retries_do_not_starve_a_later_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            start = T0 - timedelta(hours=80)
            for _ in range(7):
                journal.record_verdict("OLDUSDT", "BUY", Decimal("0.8"), Decimal("100"), start)
            journal.record_verdict("NEWUSDT", "BUY", Decimal("0.8"), Decimal("100"), start)
            calls = {"n": 0, "new": 0}

            def price_at(symbol, _moment):
                calls["n"] += 1
                if symbol == "NEWUSDT":
                    calls["new"] += 1
                    return Decimal("110")
                return None

            evaluate_due(journal, price_at, T0, limit=20)
            self.assertEqual(calls["new"], 0)
            evaluate_due(journal, price_at, T0 + timedelta(hours=1), limit=20)
            new = next(row for row in journal.verdict_rows() if row["symbol"] == "NEWUSDT")
            self.assertEqual(calls["new"], 3)
            self.assertIsNotNone(new["move_4h"])
            self.assertIsNotNone(new["move_24h"])
            self.assertIsNotNone(new["move_72h"])
            self.assertLess(calls["n"], 24 * 20)
            journal.close()

    def test_retry_after_backoff_stays_inside_the_lookup_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            start = T0 - timedelta(hours=80)
            for _ in range(10):
                journal.record_verdict("BTCUSDT", "BUY", Decimal("0.8"), Decimal("100"), start)
            calls = {"n": 0}

            def price_at(_symbol, _moment):
                calls["n"] += 1
                return None

            evaluate_due(journal, price_at, T0, limit=20)
            self.assertEqual(calls["n"], 20)
            evaluate_due(journal, price_at, T0 + timedelta(seconds=60), limit=20)
            self.assertEqual(calls["n"], 40)
            journal.close()

    def test_a_later_scan_retries_a_transient_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"), scan_seconds=30, research_seconds=3600)
            journal = OrderJournal(cfg.db_path)
            journal.record_verdict("BTCUSDT", "BUY", Decimal("0.8"), Decimal("100"), T0 - timedelta(hours=80))
            market = Markets()
            calls = {"n": 0}

            def price_at(_symbol, _moment):
                calls["n"] += 1
                if calls["n"] <= 3:
                    raise MarketDataError("lookup failed")
                return Decimal("110")

            market.price_at = price_at
            engine = Engine(cfg, ExplodingBroker(), Research(), journal, market)
            engine.cycle = lambda: (_ for _ in ()).throw(AssertionError("cycle"))
            engine.collect_research = lambda: []
            gate = ResearchGate()
            clocks = Clocks(research=T0, monitor=T0, reconcile=T0)
            clocks = once(engine, gate, clocks, now=T0)
            self.assertIsNone(journal.verdict_rows()[0]["move_4h"])
            self.assertEqual(calls["n"], 3)
            clocks = once(engine, gate, clocks, now=T0 + timedelta(seconds=30))
            self.assertEqual(calls["n"], 3)
            once(engine, gate, clocks, now=T0 + timedelta(seconds=60))
            row = journal.verdict_rows()[0]
            self.assertIsNotNone(row["move_4h"])
            self.assertIsNotNone(row["move_24h"])
            self.assertIsNotNone(row["move_72h"])
            self.assertEqual(calls["n"], 6)
            journal.close()


class ResearchPaceTests(unittest.TestCase):
    def _engine(self, tmp, **overrides):
        cfg = Settings(db_path=os.path.join(tmp, "warp.db"), research_seconds=3600, **overrides)
        journal = OrderJournal(cfg.db_path)
        engine = Engine(cfg, ExplodingBroker(), Research(), journal, Markets())
        return cfg, journal, engine

    def test_pacing_never_runs_faster_and_a_tight_budget_waits(self):
        noon = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
        cfg = Settings(research_seconds=3600)
        self.assertEqual(paced_research_seconds(cfg, noon, 48, 2), 3600)
        self.assertEqual(paced_research_seconds(cfg, noon, 2, 2), 12 * 3600)
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, journal, engine = self._engine(tmp)
            analyst = self._analyst(journal)
            engine.analyst = analyst
            self._store_budget(journal, 44)
            journal.note_research_started(noon - timedelta(hours=2))
            calls = {"n": 0}
            engine.collect_research = lambda: calls.__setitem__("n", calls["n"] + 1) or []
            gate = ResearchGate()
            once(engine, gate, clocks_from_journal(journal), now=noon)
            self.assertEqual(calls["n"], 0)
            self.assertIsNone(gate._thread)
            self.assertNotEqual(engine.research_status["state"] if engine.research_status else "", "budget_wait")
            analyst.close()
            journal.close()

    def test_status_next_attempt_is_the_paced_time(self):
        noon = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, journal, engine = self._engine(tmp)
            analyst = self._analyst(journal)
            engine.analyst = analyst
            self._store_budget(journal, 44)
            started = noon - timedelta(hours=2)
            journal.note_research_started(started)
            engine.collect_research = lambda: []
            try:
                once(engine, ResearchGate(), clocks_from_journal(journal), now=noon)
                expected = (started + timedelta(seconds=6 * 3600)).isoformat()
                plain = (started + timedelta(seconds=3600)).isoformat()
                self.assertNotEqual(expected, plain)
                self.assertEqual(engine.research_status["next_attempt_at"], expected)
                self.assertEqual(_research_view(engine)["next_attempt_at"], expected)
                self.assertEqual(engine.research_status["state"], "ok")
            finally:
                analyst.close()
                journal.close()

    def test_vibe_keeps_its_cadence_when_the_local_budget_is_spent(self):
        noon = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, journal, engine = self._engine(tmp, analysis="vibe")
            analyst = self._analyst(journal)
            engine.analyst = analyst
            self._store_budget(journal, 48)
            journal.note_research_started(noon - timedelta(hours=2))
            calls = {"n": 0}
            engine.collect_research = lambda: calls.__setitem__("n", calls["n"] + 1) or []
            gate = ResearchGate()
            try:
                once(engine, gate, clocks_from_journal(journal), now=noon)
                if gate._thread is not None:
                    gate._thread.join(2)
                self.assertEqual(calls["n"], 1)
                self.assertNotEqual(engine.research_status["state"], "budget_wait")
            finally:
                analyst.close()
                journal.close()

    def test_restart_inside_the_window_runs_research_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, journal, engine = self._engine(tmp)
            calls = {"n": 0}

            def collect():
                calls["n"] += 1
                return []

            engine.collect_research = collect
            first = ResearchGate()
            once(engine, first, clocks_from_journal(journal), now=T0)
            first._thread.join(2)
            self.assertEqual(calls["n"], 1)

            second = ResearchGate()
            once(engine, second, clocks_from_journal(journal), now=T0 + timedelta(minutes=10))
            self.assertIsNone(second._thread)
            self.assertEqual(calls["n"], 1)

            third = ResearchGate()
            once(engine, third, clocks_from_journal(journal), now=T0 + timedelta(seconds=3600))
            third._thread.join(2)
            self.assertEqual(calls["n"], 2)
            journal.close()

    def _analyst(self, journal):
        analyst = LlmAnalyst(
            nvidia_key="nv-test",
            openrouter_key="or-test",
            daily_calls=48,
            daily_usd=Decimal("2"),
            usd_per_call=Decimal("0.02"),
        )
        analyst.bind_journal(journal)
        return analyst

    def _store_budget(self, journal, calls: int, day: str | None = None) -> None:
        stamp = day or datetime.now(timezone.utc).date().isoformat()
        journal.set_setting("llm_budget", json.dumps({"day": stamp, "calls": calls, "usd": "0"}))

    def test_one_run_fits_then_budget_wait_and_the_day_roll_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, journal, engine = self._engine(tmp)
            analyst = self._analyst(journal)
            engine.analyst = analyst
            self._store_budget(journal, 46)
            calls = {"n": 0}

            def collect():
                calls["n"] += 1
                analyst.budget.charge()
                analyst.budget.charge()
                return []

            engine.collect_research = collect
            now = datetime.now(timezone.utc)
            gate = ResearchGate()
            clocks = once(engine, gate, Clocks(), now=now)
            gate._thread.join(2)
            self.assertEqual(calls["n"], 1)
            once(engine, gate, clocks, now=now + timedelta(seconds=1))
            self.assertEqual(calls["n"], 1)
            self.assertEqual(engine.research_status["state"], "budget_wait")
            self.assertNotIn("llm_cap", _events(journal))

            quiet = ResearchGate()
            self._store_budget(journal, 47)
            engine.research_status = None
            once(engine, quiet, Clocks(), now=now + timedelta(seconds=2))
            self.assertIsNone(quiet._thread)
            self.assertEqual(engine.research_status["state"], "budget_wait")
            self.assertNotIn("llm_cap", _events(journal))
            self.assertEqual(journal.get_setting("budget_wait_day"), now.date().isoformat())

            yesterday = (now - timedelta(days=1)).date().isoformat()
            self._store_budget(journal, 48, yesterday)
            rolled = ResearchGate()
            once(engine, rolled, Clocks(), now=now + timedelta(seconds=3))
            rolled._thread.join(2)
            self.assertEqual(calls["n"], 2)
            journal.close()


class BasisAlertTests(unittest.TestCase):
    def tearDown(self):
        HUB.publish({"enabled": False, "state": "disabled"})

    def test_warmup_is_silent_and_a_real_episode_alerts_once_each_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, clock, feeds, alerts = _runner(tmp, T0)
            try:
                for step in range(10):
                    clock.moment = T0 + timedelta(minutes=step)
                    _arm(feeds, clock.moment, "1")
                    runner.cycle()
                self.assertEqual(_basis_alerts(alerts), [])
                self.assertEqual(runner._episode, 0)
                self.assertNotEqual(runner.state, "warming")
            finally:
                runner.store.close()

        with tempfile.TemporaryDirectory() as tmp:
            runner, clock, feeds, alerts = _runner(tmp, T0)
            try:
                clock.moment = T0 + timedelta(minutes=16)
                feeds.bars = {"1m": []}
                runner.cycle()
                runner.cycle()
                unhealthy = [event for event in alerts if event.startswith("XAU basis_unhealthy")]
                self.assertEqual(unhealthy, ["XAU basis_unhealthy #1"])
                self.assertEqual(runner.state, "degraded")
            finally:
                runner.store.close()

        with tempfile.TemporaryDirectory() as tmp:
            moment = T0 + timedelta(hours=1)
            runner, clock, feeds, alerts = _runner(tmp, moment)
            runner._started_at = moment - timedelta(minutes=20)
            try:
                runner.basis.seed([(moment - timedelta(minutes=30 - i), Decimal("1")) for i in range(10)])
                _arm(feeds, moment, "1")
                runner.cycle()
                self.assertEqual(_basis_alerts(alerts), [])
                self.assertEqual(runner.state, "degraded")

                clock.moment = moment + timedelta(minutes=1)
                runner.basis.seed(
                    [(clock.moment - timedelta(minutes=20 - i), Decimal("30")) for i in range(10)]
                )
                _arm(feeds, clock.moment, "30")
                runner.cycle()
                self.assertEqual(
                    [event for event in alerts if event.startswith("XAU basis_unhealthy")],
                    ["XAU basis_unhealthy #1"],
                )

                clock.moment = moment + timedelta(minutes=2)
                runner.basis.seed(
                    [(clock.moment - timedelta(minutes=20 - i), Decimal("1")) for i in range(10)]
                )
                _arm(feeds, clock.moment, "1")
                runner.cycle()
                self.assertEqual(
                    [event for event in alerts if event.startswith("XAU basis_recovered")],
                    ["XAU basis_recovered #1"],
                )
            finally:
                runner.store.close()

    def test_status_shows_the_live_feed_while_warming_with_no_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, clock, feeds, _alerts = _runner(tmp, T0)
            try:
                _arm(feeds, clock.moment, "1")
                runner.cycle()
                runner._beat()
                block = _xau_block()
                self.assertEqual(block["state"], "warming")
                self.assertTrue(block["warming"])
                self.assertEqual(block["live_feed"], "binance")
                self.assertEqual(block["basis_samples"], 1)
                self.assertEqual(block["basis_reason"], "few_samples")
                self.assertIsNone(block["basis_value"])
                self.assertIsInstance(block["last_bar_age_s"], int)
                self.assertGreaterEqual(block["last_bar_age_s"], 0)
                self.assertIsNone(block["feed"])
                self.assertIsNone(block["basis"])
            finally:
                runner.store.close()


class CycleBlockTests(unittest.TestCase):
    def test_transient_502_is_debounced_and_other_errors_alert_immediately(self):
        saved = dict(HEALTH)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = Settings(db_path=os.path.join(tmp, "warp.db"), research_seconds=3600)
                journal = OrderJournal(cfg.db_path)
                engine = Engine(cfg, ExplodingBroker(), Research(), journal, Markets())
                transient = IntegrationError("upstream HTTP 502", status_code=502)
                tracker = CycleBlockTracker()
                engine.status.dependencies_healthy = True
                engine.status.execution_permitted = True
                engine.status.risk_permitted = True
                HEALTH["dependencies_healthy"] = True
                HEALTH["execution_permitted"] = True
                HEALTH["risk_permitted"] = True
                grab = _Grab()
                logger = logging.getLogger("warp3c")
                previous = logger.level
                logger.setLevel(logging.INFO)
                logger.addHandler(grab)
                try:
                    engine._mark_blocked(transient)
                    note_blocked_cycle(cfg, journal, transient, tracker, T0)
                finally:
                    logger.removeHandler(grab)
                    logger.setLevel(previous)
                self.assertFalse(engine.status.dependencies_healthy)
                self.assertFalse(engine.status.execution_permitted)
                self.assertFalse(engine.status.risk_permitted)
                self.assertFalse(HEALTH["dependencies_healthy"])
                self.assertFalse(HEALTH["execution_permitted"])
                self.assertFalse(HEALTH["risk_permitted"])
                self.assertEqual(HEALTH["reason"], "cycle_blocked")
                logged = [line for line in grab.lines if '"event":"cycle_blocked"' in line]
                self.assertEqual(len(logged), 1)
                payload = json.loads(logged[0])
                self.assertEqual(payload["mode"], cfg.mode)
                self.assertEqual(payload["error_type"], "IntegrationError")
                self.assertIn("502", payload["detail"])
                self.assertNotIn("cycle_blocked", _events(journal))

                note_blocked_cycle(cfg, journal, transient, tracker, T0 + timedelta(seconds=60))
                self.assertEqual(_events(journal).count("cycle_blocked"), 1)
                note_cycle_recovered(cfg, journal, tracker)
                self.assertEqual(_events(journal).count("cycle_recovered"), 1)
                journal.close()

                other = OrderJournal(os.path.join(tmp, "other.db"))
                immediate = CycleBlockTracker()
                note_blocked_cycle(
                    cfg,
                    other,
                    IntegrationError("bad request", status_code=400),
                    immediate,
                    T0,
                )
                self.assertEqual(_events(other), ["cycle_blocked"])
                other.close()
        finally:
            HEALTH.clear()
            HEALTH.update(saved)


class HttpLogTests(unittest.TestCase):
    def test_info_2xx_is_dropped_and_a_502_telegram_url_is_masked(self):
        token = "fake-bot"
        logger = logging.getLogger("warp3c.test.http")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        grab = _Grab()
        grab.addFilter(_REDACT)
        grab.addFilter(_QUIET)
        logger.addHandler(grab)
        try:
            logger.info('HTTP Request: GET https://example.test/api "HTTP/1.1 200 OK"')
            logger.info(
                'HTTP Request: POST %s "HTTP/1.1 502 Bad Gateway"',
                f"https://api.telegram.org/bot{token}/sendMessage",
            )
            self.assertEqual(len(grab.lines), 1)
            self.assertIn("502", grab.lines[0])
            self.assertIn("/bot<redacted>/", grab.lines[0])
            self.assertNotIn(token, grab.lines[0])
            warning = logging.LogRecord(
                "httpx",
                logging.WARNING,
                __file__,
                1,
                'HTTP Request: GET https://example.test "HTTP/1.1 200 OK"',
                (),
                None,
            )
            self.assertTrue(_QUIET.filter(warning))
        finally:
            logger.removeHandler(grab)

        root = logging.getLogger()
        handler = logging.StreamHandler()
        root.addHandler(handler)
        try:
            install_http_redaction()
            self.assertTrue(any(isinstance(item, _QuietHttpSuccess) for item in handler.filters))
        finally:
            root.removeHandler(handler)


class CardTextTests(unittest.TestCase):
    def test_card_and_evidence_round_rsi_and_momentum_only_in_text(self):
        item = evidence()
        item = type(item)(
            item.symbol,
            item.as_of,
            item.last_price,
            item.quote_volume,
            item.trend,
            Decimal("0.123456"),
            Decimal("54.78873737549933944528817208"),
            item.volatility,
            item.regime,
            item.min_notional,
            item.quote_precision,
            item.tradable,
            item.passed,
            item.reason,
            item.min_qty,
            item.step_size,
        )
        _updated, card = qualify(sig(), item, None, Settings())
        self.assertIn("RSI 54.8", card["why"])
        self.assertIn("momentum 0.12", card["why"])
        self.assertIn("RSI 54.8", card["why_id"])
        self.assertIn("momentum 0.12", card["why_id"])
        self.assertNotIn("54.7887", card["why"])
        self.assertNotIn("54.7887", card["why_id"])
        self.assertIn("54.78873737549933944528817208", card["rsi"])
        self.assertEqual(item.rsi, Decimal("54.78873737549933944528817208"))
        line = _evidence_lines(["BTCUSDT"], {"BTCUSDT": item})
        self.assertIn("rsi=54.8", line)
        self.assertIn("momentum=0.12", line)
        self.assertNotIn("54.7887", line)


class FaviconTests(unittest.TestCase):
    def test_favicon_is_empty_without_auth(self):
        status, _headers, body = dispatch("GET", "/favicon.ico", {}, b"")
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertEqual(dispatch("GET", "/favicon.ico?v=1", {}, b"")[0], 204)


def _raw_klines(count: int, now: datetime) -> list:
    rows = []
    price = 100.0
    for i in range(count):
        close_at = now - timedelta(minutes=15 * (count - i))
        open_at = close_at - timedelta(minutes=15)
        price += 0.4
        rows.append(
            [
                int(open_at.timestamp() * 1000),
                f"{price:.4f}",
                f"{price + 1:.4f}",
                f"{price - 1:.4f}",
                f"{price:.4f}",
                "1",
                int(close_at.timestamp() * 1000),
                "1000",
                0,
                "0",
                "0",
                "0",
            ]
        )
    return rows


def _spot_book(klines):
    def handler(req):
        if req.url.path.endswith("exchangeInfo"):
            return httpx.Response(
                200,
                json={
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "status": "TRADING",
                            "quoteAsset": "USDT",
                            "isSpotTradingAllowed": True,
                            "quoteAssetPrecision": 2,
                            "filters": [{"filterType": "NOTIONAL", "minNotional": "5"}],
                        }
                    ]
                },
            )
        if req.url.path.endswith("ticker/24hr"):
            return httpx.Response(
                200,
                json={"symbol": "BTCUSDT", "quoteVolume": "5000000", "lastPrice": "130"},
            )
        if req.url.path.endswith("klines"):
            return klines(req)
        raise AssertionError(req.url.path)

    return handler


class CandleFetchTests(unittest.TestCase):
    def test_five_hundred_configured_candles_reach_the_backtest(self):
        now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
        series = _raw_klines(501, now)
        seen = []

        def klines(req):
            limit = int(req.url.params["limit"])
            seen.append(limit)
            self.assertNotIn("endTime", req.url.params)
            return httpx.Response(200, json=series[-limit:])

        cfg = Settings(backtest_candles=500, rsi_max=Decimal("100"), research_seconds=3600)
        data = MarketData(
            "https://data-api.binance.vision",
            cfg,
            transport=httpx.MockTransport(_spot_book(klines)),
        )
        try:
            item = data.scan(("BTCUSDT",), now=now)["BTCUSDT"]
        finally:
            data.close()
        candles = data.last_candles["BTCUSDT"]
        self.assertEqual(seen, [501])
        self.assertEqual(len(candles), 500)
        self.assertEqual(len(candles_for_backtest(candles, 500)), 500)
        result = run_backtest(candles, Decimal("0.20"), Decimal("0.025"), min_trades=3)
        self.assertNotEqual(result.reason, "not_enough_candles")
        _gated, card = qualify(sig(), evidence(), candles, cfg)
        self.assertNotEqual(card["backtest_reason"], "not_enough_candles")
        recent = analyze(
            "BTCUSDT",
            closed_candles(series[-120:], now=now),
            quote_volume_24h=Decimal("5000000"),
            min_notional=Decimal("5"),
            quote_precision=2,
            tradable=True,
            cfg=cfg,
            now=now,
        )
        self.assertEqual(item.rsi, recent.rsi)
        self.assertEqual(item.momentum, recent.momentum)

    def test_default_scan_still_requests_120(self):
        now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
        series = _raw_klines(120, now)
        seen = []

        def klines(req):
            seen.append((int(req.url.params["limit"]), req.url.params.get("endTime")))
            return httpx.Response(200, json=series)

        cfg = Settings(research_seconds=3600)
        data = MarketData(
            "https://data-api.binance.vision",
            cfg,
            transport=httpx.MockTransport(_spot_book(klines)),
        )
        try:
            data.scan(("BTCUSDT",), now=now)
        finally:
            data.close()
        self.assertEqual(seen, [(120, None)])
        self.assertEqual(len(data.last_candles["BTCUSDT"]), 120)

    def test_a_window_past_the_exchange_page_asks_for_the_older_bars(self):
        now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
        series = _raw_klines(1001, now)
        seen = []

        def klines(req):
            limit = int(req.url.params["limit"])
            end = req.url.params.get("endTime")
            rows = series
            if end is not None:
                end_ms = int(end)
                rows = [row for row in rows if row[0] <= end_ms]
            seen.append((limit, end))
            return httpx.Response(200, json=rows[-limit:])

        cfg = Settings(backtest_candles=1000, research_seconds=3600)
        data = MarketData(
            "https://data-api.binance.vision",
            cfg,
            transport=httpx.MockTransport(_spot_book(klines)),
        )
        try:
            data.scan(("BTCUSDT",), now=now)
        finally:
            data.close()
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], (1000, None))
        self.assertEqual(seen[1][0], 1)
        self.assertIsNotNone(seen[1][1])
        candles = data.last_candles["BTCUSDT"]
        self.assertEqual(len(candles), 1000)
        result = run_backtest(candles, Decimal("0.20"), Decimal("0.025"), min_trades=3)
        self.assertNotEqual(result.reason, "not_enough_candles")

    def test_a_short_fetch_fails_the_backtest_closed(self):
        now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
        series = _raw_klines(120, now)

        def klines(_req):
            return httpx.Response(200, json=series)

        cfg = Settings(backtest_candles=500, research_seconds=3600)
        data = MarketData(
            "https://data-api.binance.vision",
            cfg,
            transport=httpx.MockTransport(_spot_book(klines)),
        )
        try:
            data.scan(("BTCUSDT",), now=now)
        finally:
            data.close()
        candles = data.last_candles["BTCUSDT"]
        self.assertLess(len(candles), 500)
        _gated, card = qualify(sig(), evidence(), candles, cfg)
        self.assertEqual(card["backtest_reason"], "not_enough_candles")


class BacktestWindowTests(unittest.TestCase):
    def test_default_matches_today_and_opt_in_needs_the_longer_series(self):
        candles = _candles(120, "0.004")
        self.assertEqual(Settings().backtest_candles, 120)
        self.assertIs(candles_for_backtest(candles, 120), candles)
        direct = run_backtest(candles, Decimal("0.05"), Decimal("0.01"), min_trades=3)
        selected = run_backtest(
            candles_for_backtest(candles, Settings().backtest_candles),
            Decimal("0.05"),
            Decimal("0.01"),
            min_trades=3,
        )
        self.assertEqual(direct.to_dict(), selected.to_dict())
        _updated, card = qualify(sig(), evidence(), candles, Settings())
        self.assertEqual(card["backtest_reason"], direct.reason)

        longer = _candles(400, "0.004")
        opted = candles_for_backtest(longer, 400)
        self.assertEqual(len(opted), 400)
        self.assertGreater(len(opted), 120)
        wide = run_backtest(opted, Decimal("0.05"), Decimal("0.01"), min_trades=3)
        self.assertNotEqual(wide.reason, "not_enough_candles")
        missing = run_backtest(
            candles_for_backtest(candles, 1000),
            Decimal("0.05"),
            Decimal("0.01"),
            min_trades=3,
        )
        self.assertEqual(missing.reason, "not_enough_candles")
        self.assertIsNone(candles_for_backtest(None, 1000))
        _gated, failed = qualify(sig(), evidence(), candles, Settings(backtest_candles=1000))
        self.assertEqual(failed["backtest_reason"], "not_enough_candles")


class DeployShapeTests(unittest.TestCase):
    """Two supervisor boots, ten minutes apart. One research run, no warm-up alert, scored rows, no 502 page."""

    def tearDown(self):
        HUB.publish({"enabled": False, "state": "disabled"})

    def test_two_boots_ten_minutes_apart(self):
        saved = dict(HEALTH)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = Settings(db_path=os.path.join(tmp, "warp.db"), research_seconds=3600, scan_seconds=30)
                journal = OrderJournal(cfg.db_path)
                journal.record_verdict(
                    "BTCUSDT", "BUY", Decimal("0.8"), Decimal("100"), T0 - timedelta(hours=80)
                )
                market = Markets()
                market.price_at = lambda _symbol, _moment: Decimal("110")
                engine = Engine(cfg, ExplodingBroker(), Research(), journal, market)
                calls = {"n": 0}

                def collect():
                    calls["n"] += 1
                    return []

                engine.collect_research = collect
                engine.cycle = lambda: (_ for _ in ()).throw(AssertionError("cycle"))

                first = ResearchGate()
                once(engine, first, clocks_from_journal(journal), now=T0)
                first._thread.join(2)
                second = ResearchGate()
                once(engine, second, clocks_from_journal(journal), now=T0 + timedelta(minutes=10))
                self.assertEqual(calls["n"], 1)
                self.assertIsNone(second._thread)
                self.assertIsNotNone(journal.verdict_rows()[0]["move_4h"])

                runner, _clock, feeds, alerts = _runner(tmp, T0)
                try:
                    _arm(feeds, T0, "1")
                    runner.cycle()
                    self.assertEqual(_basis_alerts(alerts), [])
                    self.assertEqual(runner.state, "warming")
                finally:
                    runner.store.close()

                transient = IntegrationError("upstream HTTP 502", status_code=502)
                tracker = CycleBlockTracker()
                engine.status.dependencies_healthy = True
                engine.status.execution_permitted = True
                engine.status.risk_permitted = True
                engine._mark_blocked(transient)
                note_blocked_cycle(cfg, journal, transient, tracker, T0)
                self.assertFalse(engine.status.dependencies_healthy)
                self.assertFalse(engine.status.execution_permitted)
                self.assertFalse(engine.status.risk_permitted)
                self.assertNotIn("cycle_blocked", _events(journal))
                journal.close()
        finally:
            HEALTH.clear()
            HEALTH.update(saved)


if __name__ == "__main__":
    unittest.main()
