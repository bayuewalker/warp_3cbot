"""Boss isolation proofs. An XAU failure must not touch the DCA loop or HEALTH."""

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_failures import ExplodingBroker, Markets, Research

from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.operator import HEALTH, publish
from warp3c.schedule import Clocks
from warp3c.state import OrderJournal
from warp3c.supervisor import ResearchGate, once
from warp3c.xau.runner import start_xau
from warp3c.xau.status import snapshot
from warp3c.xau.store import XauStore


def _drive_dca(tmp: str, loops: int = 5):
    cfg = Settings(db_path=os.path.join(tmp, "warp.db"))
    journal = OrderJournal(cfg.db_path)
    engine = Engine(cfg, ExplodingBroker(), Research(), journal, Markets())
    gate = ResearchGate()
    moment = datetime.now(timezone.utc)
    clocks = Clocks(scan=moment, research=moment, reconcile=moment, monitor=moment - timedelta(seconds=120))
    for _ in range(loops):
        moment += timedelta(seconds=120)
        clocks = once(engine, gate, clocks, now=moment)
    return cfg, journal, engine


class XauRaisesTests(unittest.TestCase):
    def test_xau_runtime_error_does_not_stop_dca_or_health(self):
        def boom():
            raise RuntimeError("xau down")

        with tempfile.TemporaryDirectory() as tmp:
            health_before = dict(HEALTH)
            runner = start_xau(
                os.path.join(tmp, "warp.db"),
                lambda _event, _detail: True,
                cycle=boom,
            )
            self.addCleanup(lambda: runner.stop(timeout=1))
            deadline = time.time() + 2
            while time.time() < deadline and snapshot().get("state") != "degraded":
                time.sleep(0.05)
            cfg, journal, engine = _drive_dca(tmp)
            self.addCleanup(journal.close)
            started = time.perf_counter()
            publish(cfg, journal, engine)
            self.assertLess(time.perf_counter() - started, 0.5)
            self.assertEqual(snapshot()["state"], "degraded")
            self.assertEqual(HEALTH, health_before)
            self.assertEqual(engine.status.reason != "xau", True)


class XauHangTests(unittest.TestCase):
    def test_blocked_cycle_does_not_block_dca_live_or_stop(self):
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer

        from warp3c.__main__ import HealthHandler

        blocked = threading.Event()

        class Clock:
            def __init__(self):
                self.moment = datetime(2026, 11, 26, 15, tzinfo=timezone.utc)

            def __call__(self):
                return self.moment

        clock = Clock()
        with tempfile.TemporaryDirectory() as tmp:
            runner = start_xau(
                os.path.join(tmp, "warp.db"),
                lambda _event, _detail: True,
                cycle=lambda: blocked.wait(),
                clock=clock,
            )
            self.addCleanup(lambda: runner.stop(timeout=0.2))
            time.sleep(0.2)
            started = time.perf_counter()
            cfg, journal, engine = _drive_dca(tmp)
            self.addCleanup(journal.close)
            self.assertLess(time.perf_counter() - started, 2)
            previous = dict(HEALTH)
            HEALTH.update({"alive": True, "dependencies_healthy": False, "last_cycle_ok": False})
            server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                port = server.server_address[1]
                conn = HTTPConnection("127.0.0.1", port, timeout=3)
                conn.request("GET", "/live")
                live = conn.getresponse()
                live.read()
                self.assertEqual(live.status, 200)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                HEALTH.clear()
                HEALTH.update(previous)
            clock.moment = clock.moment + timedelta(minutes=6)
            self.assertEqual(snapshot()["state"], "stalled")
            stopped = time.perf_counter()
            runner.stop(timeout=0.5)
            self.assertLess(time.perf_counter() - stopped, 1)


class StartFailureTests(unittest.TestCase):
    def test_start_xau_failure_is_logged_and_main_continues(self):
        from warp3c.__main__ import STOP, main

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "WARP_DB_PATH": os.path.join(tmp, "warp.db"),
                "WARP_XAU_ENABLED": "1",
                "WARP_HTTP": "stdlib",
                "WARP_MANAGE_VIBE": "0",
                "PORT": "0",
                "TRADING_MODE": "observe",
            }
            STOP.set()
            with patch.dict(os.environ, env, clear=False), \
                    patch("warp3c.__main__.start_xau", side_effect=RuntimeError("boom")), \
                    patch("warp3c.live.DashboardReads.start", lambda self: None), \
                    self.assertLogs("warp3c", level="INFO") as logs:
                main()
            self.assertTrue(any("xau_start_failed" in line for line in logs.output))
            STOP.clear()


class DisabledTests(unittest.TestCase):
    def test_only_exact_one_enables_and_from_env_does_not_raise(self):
        import warp3c.config as config

        with tempfile.TemporaryDirectory() as tmp:
            base = {"WARP_DB_PATH": os.path.join(tmp, "warp.db"), "TRADING_MODE": "observe"}
            for value in ("", "0", "true", "yes"):
                config._XAU_FLAG_WARNED = False
                before = {thread.name for thread in threading.enumerate()}
                with patch.dict(os.environ, {**base, "WARP_XAU_ENABLED": value}, clear=False):
                    if value == "":
                        os.environ.pop("WARP_XAU_ENABLED", None)
                    cfg = Settings.from_env()
                self.assertFalse(cfg.xau_enabled)
                after = {thread.name for thread in threading.enumerate()}
                self.assertFalse(after - before)
                self.assertNotIn("xau", after - before)


class ImportFenceTests(unittest.TestCase):
    def test_ast_import_fence(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "warp3c"
        for path in (root / "xau").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = _imported(node)
                for name in modules:
                    if name == "warp3c" or name.startswith("warp3c."):
                        self.assertEqual(name, "warp3c.telemetry", path.name)
        allowed = {"__main__.py", "operator.py"}
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                for name in _imported(node):
                    if name.startswith("warp3c.xau") or name == "warp3c.xau":
                        self.assertIn(path.name, allowed)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_does_not_lock_behind_a_slow_cycle(self):
        entered = threading.Event()

        def slow():
            entered.set()
            time.sleep(1)

        with tempfile.TemporaryDirectory() as tmp:
            runner = start_xau(os.path.join(tmp, "warp.db"), lambda _e, _d: True, cycle=slow)
            try:
                self.assertTrue(entered.wait(1))
                started = time.perf_counter()
                snapshot()
                self.assertLess(time.perf_counter() - started, 0.01)
            finally:
                # stop before the next test. A live heartbeat uses the wall clock, and the
                # stall fixture is only a few minutes away from it.
                runner.stop(timeout=2)


class ContentionTests(unittest.TestCase):
    def test_xau_writes_do_not_stall_the_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "warp.db")
            journal = OrderJournal(path)
            self.addCleanup(journal.close)
            store = XauStore(path)
            self.addCleanup(store.close)
            store.migrate()
            done = threading.Event()

            def burst():
                for index in range(500):
                    store.add_decision(
                        "2026-11-26T00:00:00+00:00", None, ["C1"], False, "B", str(index)
                    )
                done.set()

            threading.Thread(target=burst, daemon=True).start()
            now = datetime.now(timezone.utc)
            for index in range(20):
                started = time.perf_counter()
                journal.enqueue_alert(f"dca-{index}", "unchanged", now=now)
                self.assertLess(time.perf_counter() - started, 1)
            self.assertTrue(done.wait(5))


def _imported(node):
    import ast

    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        if node.level:
            if node.level == 1 and base.startswith("xau"):
                return ["warp3c." + base]
            if node.level == 1 and base == "telemetry":
                return ["warp3c.telemetry"]
            if node.level == 2 and base == "telemetry":
                return ["warp3c.telemetry"]
            return [base] if base else []
        return [base] if base else []
    return []


if __name__ == "__main__":
    unittest.main()
