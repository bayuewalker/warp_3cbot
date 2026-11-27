"""XAU primary lane. The spot lane stays in the tree and stays quiet unless the flag is exactly 1."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import httpx

from warp3c.config import Settings
from warp3c.httpapi import dispatch
from warp3c.notify import Delivery, flush_alerts
from warp3c.operator import HEALTH, SNAPSHOT, publish_postponed
from warp3c.state import OrderJournal
from warp3c.threecommas import ThreeCommas
from warp3c.xau.basis import MIN_SAMPLES, BasisBook
from warp3c.xau.candles import Bar
from warp3c.xau.clock import window_at
from warp3c.xau.config import XauSettings
from warp3c.xau.engines.smc import build_setup
from warp3c.xau.profiles import load_profile
from warp3c.xau.runner import XauRunner

UTC = timezone.utc
T0 = datetime(2026, 11, 26, 4, tzinfo=UTC)
LONDON = datetime(2026, 11, 26, 7, 30, tzinfo=UTC)
D = Decimal


class _Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


class _Feeds:
    def __init__(self):
        self.spot_price = D("2300")
        self.perp_price = D("2301")
        self.bars = {"1m": []}

    def book(self):
        return "binance", self.bars

    def spot(self):
        return self.spot_price

    def perp_now(self):
        return self.perp_price

    def dxy(self):
        return "DXY 100"

    def ff_calendar(self):
        return None


class _Transport(httpx.BaseTransport):
    def __init__(self):
        self.calls = 0

    def handle_request(self, request):
        self.calls += 1
        return httpx.Response(200, json={"ok": True})


def _runner(tmp, moment):
    alerts = []
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
    runner.store.migrate()
    return runner, clock, feeds, alerts


def _bar(moment, price):
    opened = moment - timedelta(minutes=1)
    return Bar(opened, price, price, price, price, D("1"), "1m")


def _aplus():
    return build_setup(
        model="S1", direction="long", entry_type="limit", entry=D("3565.40"), extreme=D("3560"),
        atr_h1=D("12"), spread=D("0.30"), l1=D("3600"), dol_price=D("3620"),
        ote=False, clean_sweep=True, vacuum=False, fresh_reversal=False,
        bias="bullish", window="London KZ", valid_until=None, reason="S1",
        poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=D("0.40"),
    )


class FlagTests(unittest.TestCase):
    def test_only_exact_one_enables_the_spot_lane(self):
        import warp3c.config as config

        with tempfile.TemporaryDirectory() as tmp:
            base = {"WARP_DB_PATH": os.path.join(tmp, "warp.db"), "TRADING_MODE": "observe"}
            for value in ("", "0", "true", "yes"):
                config._SPOT_LANE_WARNED = False
                with patch.dict(os.environ, {**base, "WARP_SPOT_LANE_ENABLED": value}, clear=False):
                    if value == "":
                        os.environ.pop("WARP_SPOT_LANE_ENABLED", None)
                    if value in {"true", "yes"}:
                        with self.assertLogs("warp3c", level="INFO") as logs:
                            cfg = Settings.from_env()
                        self.assertTrue(any("spot_lane_config_warning" in line for line in logs.output))
                        with patch.dict(os.environ, {**base, "WARP_SPOT_LANE_ENABLED": value}, clear=False):
                            Settings.from_env()
                        self.assertEqual(sum("spot_lane_config_warning" in line for line in logs.output), 1)
                    else:
                        cfg = Settings.from_env()
                self.assertFalse(cfg.spot_lane_enabled)
            config._SPOT_LANE_WARNED = False
            with patch.dict(os.environ, {**base, "WARP_SPOT_LANE_ENABLED": "1"}, clear=False):
                self.assertTrue(Settings.from_env().spot_lane_enabled)


class HostLoopTests(unittest.TestCase):
    def tearDown(self):
        HEALTH.pop("lane", None)
        HEALTH.pop("host_ok", None)
        HEALTH.pop("xau_state", None)

    def test_postponed_pass_makes_zero_broker_requests(self):
        transport = _Transport()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"))
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            broker = ThreeCommas("https://trade.3commas.io", "k" * 8, "s" * 8, transport=transport)
            self.addCleanup(broker.close)
            from warp3c.__main__ import postponed_host_pass

            postponed_host_pass(cfg, journal, None, {"price": 0.0, "screen": 0.0, "accounts_at": ""}, broker)
            self.assertEqual(transport.calls, 0)
            self.assertEqual(SNAPSHOT["/api/status"]["reason"], "spot_postponed")
            self.assertEqual(SNAPSHOT["/api/status"]["lanes"]["spot"]["state"], "postponed")
            self.assertTrue(SNAPSHOT["/api/status"]["dependencies_healthy"])
            self.assertFalse(SNAPSHOT["/api/status"]["execution_permitted"])
            self.assertEqual(SNAPSHOT["/api/status"]["pipeline"]["screening"]["state"], "postponed")
            self.assertEqual(SNAPSHOT["/api/accounts"], {"state": "postponed"})

    def test_postponed_endpoints_are_http_200(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"))
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            publish_postponed(cfg, journal)
            token = "lane-test-token-1"
            with patch.dict(os.environ, {"WARP_OPERATOR_TOKEN": token}):
                for path in ("/api/accounts", "/api/positions", "/api/screener", "/api/journal"):
                    status, _headers, body = dispatch(
                        "GET", path, {"Authorization": "Bearer " + token}, b""
                    )
                    self.assertEqual(status, 200, path)
                    self.assertEqual(json.loads(body), {"state": "postponed"})

    def test_enabled_flag_calls_once_and_postponed_does_not(self):
        from warp3c.__main__ import postponed_host_pass, run_spot_iteration

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"), spot_lane_enabled=True)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = type("Engine", (), {"status": type("Status", (), {
                "dependencies_healthy": True,
                "market_data_fresh": True,
                "execution_permitted": False,
                "risk_permitted": True,
                "reason": "observe_without_account",
            })()})()
            with patch("warp3c.__main__.once", return_value="clocks") as once_mock, \
                    patch("warp3c.__main__.publish"), \
                    patch("warp3c.__main__.note_cycle_recovered"), \
                    patch("warp3c.__main__._flush"), \
                    patch("warp3c.__main__._refresh_live"):
                run_spot_iteration(cfg, engine, None, "clocks", journal, None, None, {})
            once_mock.assert_called_once()
            with patch("warp3c.__main__.once") as quiet:
                postponed_host_pass(
                    Settings(db_path=cfg.db_path), journal, None,
                    {"price": 0.0, "screen": 0.0, "accounts_at": ""}, None,
                )
            quiet.assert_not_called()


class ReadyTests(unittest.TestCase):
    def test_ready_follows_xau_when_postponed_and_the_worker_when_active(self):
        from warp3c.httpapi import _ready_code

        previous = dict(HEALTH)
        try:
            HEALTH.clear()
            HEALTH.update({
                "alive": True, "host_ok": True, "last_cycle_ok": True, "lane": "postponed",
                "xau_state": "running", "dependencies_healthy": False,
            })
            self.assertEqual(_ready_code(), 200)
            for state in ("warming", "degraded"):
                HEALTH["xau_state"] = state
                self.assertEqual(_ready_code(), 200, state)
            for state in ("stalled", "config_error", "feed_unavailable", "stopped", "disabled"):
                HEALTH["xau_state"] = state
                self.assertEqual(_ready_code(), 503, state)
            HEALTH["xau_state"] = "running"
            HEALTH["host_ok"] = False
            self.assertEqual(_ready_code(), 503)
            HEALTH["lane"] = "active"
            HEALTH["dependencies_healthy"] = False
            HEALTH["last_cycle_ok"] = False
            self.assertEqual(_ready_code(), 503)
            HEALTH["dependencies_healthy"] = True
            HEALTH["last_cycle_ok"] = True
            self.assertEqual(_ready_code(), 200)
        finally:
            HEALTH.clear()
            HEALTH.update(previous)


class BasisPairingTests(unittest.TestCase):
    def test_same_moment_pairing_stays_healthy_when_the_closed_bar_flaps(self):
        self.assertEqual(MIN_SAMPLES, 10)
        start = datetime(2026, 11, 26, 7, tzinfo=UTC)
        spot = [D("2300") + D(index % 2) * D("4") for index in range(15)]
        closed = BasisBook(D("15"))
        paired = BasisBook(D("15"))
        previous = spot[0]
        for index, price in enumerate(spot):
            moment = start + timedelta(minutes=index)
            closed.observe(previous, price, moment)
            paired.observe(price + D("0.40"), price, moment)
            previous = price
        self.assertEqual(closed.observe(None, None, start).reason, "basis_noisy")
        live = paired.observe(None, None, start)
        self.assertTrue(live.healthy)
        self.assertEqual(live.reason, "ok")
        self.assertLessEqual(live.std, D("1.50"))


class FunnelTests(unittest.TestCase):
    def test_each_silence_reason_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, clock, feeds, _alerts = _runner(tmp, LONDON)
            self.addCleanup(runner.store.close)
            window = window_at(LONDON)
            self.assertEqual(window.kind, "kz")

            class Clean:
                gaps = False
                stale_m5 = False

            class View:
                blocked = False

            class Basis:
                def __init__(self, healthy):
                    self.healthy = healthy

            clean = Clean()
            view = View()
            healthy = Basis(True)
            cases = [
                ("halt", dict(halt_global=True)),
                ("dirty_basis", dict(basis=Basis(False))),
                ("dirty_gaps", dict(cleaned=type("C", (), {"gaps": True, "stale_m5": False})())),
                ("stale_m5", dict(cleaned=type("C", (), {"gaps": False, "stale_m5": True})())),
                ("news_block", dict(view=type("V", (), {"blocked": True})())),
            ]
            for reason, overrides in cases:
                kwargs = dict(
                    halt_global=False, halt_xau=False, basis=healthy, cleaned=clean, view=view, dirty=False,
                )
                kwargs.update(overrides)
                found = runner._silence(LONDON, window, **kwargs)
                self.assertEqual(found, reason)
                runner.store.note_funnel(LONDON.isoformat(), window.key, found)
            weekend = datetime(2026, 11, 28, 7, 30, tzinfo=UTC)
            closed = window_at(weekend)
            self.assertEqual(
                runner._silence(weekend, closed, False, False, healthy, clean, view, False),
                "market_closed",
            )
            runner.store.note_funnel(weekend.isoformat(), closed.key, "market_closed")
            counts = runner.store.funnel_counts(LONDON.isoformat(), window.key)
            for reason, _overrides in cases:
                self.assertGreaterEqual(counts["today"].get(reason, 0), 1)
            self.assertIn("market_closed", counts["today"])
            runner._warming = True
            self.assertEqual(
                runner._silence(LONDON, window, False, False, Basis(False), clean, view, False),
                "warming_up",
            )
            self.assertEqual(
                runner._silence(weekend, closed, False, False, Basis(False), clean, view, False),
                "market_closed",
            )

    def test_funnel_collapses_runs_and_drops_rows_older_than_30_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _clock, _feeds, _alerts = _runner(tmp, LONDON)
            self.addCleanup(runner.store.close)
            window = window_at(LONDON)
            old = (LONDON - timedelta(days=31)).isoformat()
            runner.store.db.execute(
                "INSERT INTO xau_funnel(at_utc, window_key, reason) VALUES(?, ?, ?)",
                (old, window.key, "dirty_basis"),
            )
            for _ in range(3):
                runner.store.note_funnel(LONDON.isoformat(), window.key, "dirty_basis")
            runner.store.note_funnel((LONDON + timedelta(minutes=1)).isoformat(), window.key, "halt")
            counts = runner.store.funnel_counts(LONDON.isoformat(), window.key)
            self.assertEqual(counts["today"]["dirty_basis"], 3)
            self.assertEqual(counts["recent"][0]["reason"], "halt")
            self.assertEqual(counts["recent"][0]["count"], 1)
            self.assertEqual(counts["recent"][1]["reason"], "dirty_basis")
            self.assertEqual(counts["recent"][1]["count"], 3)
            left = [str(row["reason"]) for row in runner.store.db.execute("SELECT reason FROM xau_funnel")]
            self.assertNotIn("stale", left)
            self.assertEqual(left.count("dirty_basis"), 3)

    def test_last_signal_at_comes_from_the_sent_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _clock, _feeds, _alerts = _runner(tmp, LONDON)
            self.addCleanup(runner.store.close)
            runner.store.insert_signal(_open_row(LONDON))
            runner._last_signal_at = None
            block = runner._snapshot_body()
            self.assertEqual(block["last_signal_at"], LONDON.isoformat())

    def test_engine_reasons_and_one_aplus_telegram(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, clock, _feeds, alerts = _runner(tmp, LONDON)
            self.addCleanup(runner.store.close)
            runner.news.note_fetch(LONDON, [])
            window = window_at(LONDON)
            bars = {}
            self.assertEqual(
                runner._maybe_signal("NO_TRADE", window, bars, LONDON, "binance", D("0.4"), "DXY", True, False),
                "engine_none",
            )
            self.assertEqual(
                runner._maybe_signal("SMC", window, bars, LONDON, "binance", D("0.4"), "DXY", True, False),
                "no_direction",
            )
            with patch("warp3c.xau.runner._direction", return_value="long"):
                self.assertEqual(
                    runner._maybe_signal("EMA", window, bars, LONDON, "binance", D("0.4"), "DXY", True, False),
                    "entry_closed",
                )
            setup = _aplus()
            self.assertIsNotNone(setup)
            with patch("warp3c.xau.runner._direction", return_value="long"), \
                    patch("warp3c.xau.runner.evaluate_smc", return_value=None):
                self.assertEqual(
                    runner._maybe_signal("SMC", window, bars, LONDON, "binance", D("0.4"), "DXY", True, False),
                    "no_setup",
                )
            from dataclasses import replace
            weak = replace(setup, poi_ok=False)
            with patch("warp3c.xau.runner._direction", return_value="long"), \
                    patch("warp3c.xau.runner.evaluate_smc", return_value=weak):
                failed = runner._maybe_signal("SMC", window, bars, LONDON, "binance", D("0.4"), "DXY", True, False)
            self.assertTrue(failed.startswith("gate_fail:"))
            self.assertIn("C5", failed)
            runner.store.insert_signal(_open_row(LONDON))
            before = [event for event in alerts if event.startswith("XAU BUY") or event.startswith("XAU SELL")]
            with patch("warp3c.xau.runner._direction", return_value="long"), \
                    patch("warp3c.xau.runner.evaluate_smc", return_value=setup):
                blocked = runner._maybe_signal("SMC", window, bars, LONDON, "binance", D("0.4"), "DXY", True, False)
            self.assertEqual(blocked, "risk_block:E4")
            self.assertEqual(before, [])
            runner.store.db.execute("DELETE FROM xau_signals")
            alerts.clear()
            with patch("warp3c.xau.runner._direction", return_value="long"), \
                    patch("warp3c.xau.runner.evaluate_smc", return_value=setup):
                sent = runner._maybe_signal("SMC", window, bars, LONDON, "binance", D("0.4"), "DXY", True, False)
            self.assertIsNone(sent)
            buys = [event for event in alerts if event.startswith("XAU BUY") or event.startswith("XAU SELL")]
            self.assertEqual(len(buys), 1)

    def test_cycle_records_dirty_basis_before_any_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, clock, feeds, _alerts = _runner(tmp, LONDON)
            self.addCleanup(runner.store.close)
            feeds.bars = {"1m": [_bar(LONDON, D("2300"))]}
            feeds.perp_price = None
            runner.cycle()
            block = runner._snapshot_body()
            self.assertEqual(block["feed"], "binance")
            self.assertIsNone(block["basis"])
            self.assertIn("warming_up", block["funnel"]["today"])
            self.assertNotIn("dirty_basis", block["funnel"]["today"])
            self.assertEqual(block["spot_state"], "live")
            self.assertIn("ready", block)
            self.assertIn("basis_std", block)
            self.assertIn("spot_last", block)


def _open_row(moment):
    return {
        "id": "01OPEN00000000000000000000",
        "created_at_utc": moment.isoformat(),
        "rules_version": "p0",
        "feed": "binance",
        "basis": "0.4",
        "engine": "SMC",
        "model": "S1",
        "window": "London KZ",
        "direction": "long",
        "grade": "A+",
        "confidence": "80",
        "entry_type": "limit",
        "entry": "3565.40",
        "sl": "3558.05",
        "tp1": "3580.10",
        "tp2": None,
        "r_usd": "7.35",
        "rr_tp1": "2",
        "rr_tp2": None,
        "risk_pct": "0.40",
        "example_lot": "0.05",
        "valid_until": None,
        "checklist_json": "{}",
        "reason": "open",
        "bias": "bullish",
        "dol": None,
        "dxy_note": "",
        "tags_json": "{}",
        "sent": "1",
        "tg_event": "",
        "status": "PENDING",
        "filled_at": None,
        "fill_price": None,
        "exit_at": None,
        "exit_reason": None,
        "realized_r": None,
        "mae_r": None,
        "mfe_r": None,
        "spread_assumed": "0.30",
    }


class TelegramOrderTests(unittest.TestCase):
    def test_an_xau_signal_is_delivered_before_older_ops_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            self.addCleanup(journal.close)
            now = datetime(2026, 11, 26, 8, tzinfo=UTC)
            for index in range(10):
                self.assertTrue(journal.enqueue_alert(f"ops-{index}", "unchanged", now=now))
            self.assertTrue(journal.enqueue_alert("XAU BUY LIMIT A+ #ABC123", "signal only", now=now))
            due = journal.due_alerts(now)
            self.assertTrue(due[0][1].startswith("XAU BUY"))
            seen = []

            def deliver(event, _detail):
                seen.append(event)
                return Delivery(ok=True)

            flush_alerts(journal, deliver)
            self.assertEqual(seen[0], "XAU BUY LIMIT A+ #ABC123")
            self.assertLessEqual(len(seen), 3)


class BasisDebounceTests(unittest.TestCase):
    def test_unhealthy_waits_ten_minutes_and_recovery_follows_an_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, clock, feeds, alerts = _runner(tmp, T0 + timedelta(hours=1))
            self.addCleanup(runner.store.close)
            moment = clock.moment
            runner._started_at = moment - timedelta(minutes=20)
            runner.basis.seed([(moment - timedelta(minutes=20 - i), D("30")) for i in range(10)])
            feeds.bars = {"1m": [_bar(moment, D("2330"))]}
            feeds.perp_price = D("2330")
            feeds.spot_price = D("2300")
            runner.cycle()
            self.assertEqual([event for event in alerts if "basis_unhealthy" in event], [])
            clock.moment = moment + timedelta(minutes=10)
            runner.basis.seed([(clock.moment - timedelta(minutes=20 - i), D("30")) for i in range(10)])
            feeds.bars = {"1m": [_bar(clock.moment, D("2330"))]}
            runner.cycle()
            self.assertEqual(
                [event for event in alerts if event.startswith("XAU basis_unhealthy")],
                ["XAU basis_unhealthy #1"],
            )
            clock.moment = moment + timedelta(minutes=11)
            runner.basis.seed([(clock.moment - timedelta(minutes=20 - i), D("0.4")) for i in range(12)])
            feeds.perp_price = D("2300.40")
            feeds.bars = {"1m": [_bar(clock.moment, D("2300.40"))]}
            runner.cycle()
            self.assertEqual(
                [event for event in alerts if event.startswith("XAU basis_recovered")],
                ["XAU basis_recovered #1"],
            )


class PlaybookTests(unittest.TestCase):
    def test_playbook_repeats_the_loaded_settings_and_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _clock, _feeds, _alerts = _runner(tmp, LONDON)
            self.addCleanup(runner.store.close)
            book = runner._snapshot_body()["playbook"]
            settings = runner.settings
            profile = runner.profile
            self.assertEqual(book["rules_version"], settings.rules_version)
            self.assertEqual(book["cycle_seconds"], settings.cycle_seconds)
            self.assertEqual(book["basis_max"], format(Decimal(str(settings.basis_max)), "f"))
            self.assertEqual(book["spread_assumed"], format(settings.spread_assumed, "f"))
            self.assertEqual(book["profile_name"], profile.name)
            self.assertEqual(book["profile_type"], profile.account_type)
            self.assertEqual(book["profile_type"], "example")
            self.assertEqual(book["max_risk_per_trade_pct"], format(profile.max_risk_per_trade_pct, "f"))
            self.assertEqual(book["daily_dd_pct"], format(profile.daily_dd_pct, "f"))
            self.assertEqual(book["max_dd_pct"], format(profile.max_dd_pct, "f"))
            self.assertEqual(book["news_rule"], profile.news_rule)
            self.assertEqual(book["news_window_min"], profile.news_window_min)
            self.assertEqual(book["gate_checks"], 14)
            self.assertEqual(book["engines"], ["SMC", "EMA", "SCALP"])
            self.assertEqual(
                [(row["name"], row["engine"]) for row in book["windows"]],
                [("London KZ", "SMC"), ("London EMA", "EMA"), ("NY KZ", "SMC"), ("NY EMA", "EMA")],
            )
            blob = json.dumps(book).lower()
            for banned in ("token", "password", "telegram", "profile_path", "secret"):
                self.assertNotIn(banned, blob)
            self.assertNotIn("playbook", runner.settings.__dict__)


if __name__ == "__main__":
    unittest.main()
