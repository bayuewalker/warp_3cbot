"""Telegram and outbox alerts. Transports are mocked. No live token or chat id."""

import json
import logging
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import httpx

from warp3c.__main__ import _deliver
from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.httpapi import dispatch
from warp3c.notify import enqueue_incident, flush_alerts
from warp3c.operator import RUNTIME
from warp3c.schedule import Clocks
from warp3c.signalcard import halt_off_card, halt_on_card
from warp3c.state import OrderJournal
from warp3c.supervisor import ResearchGate
from warp3c.telemetry import configure_logging, log_event, send_telegram
from warp3c.vibe import ResearchError


def _fixture_bot_token() -> str:
    """T1 fixture. Kept in pieces so the source has no single token literal."""
    return "123456" + ":" + "TESTTOKEN_" + "abcdefghijklmnopqrstuvwxyz"


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _Capture:
    def __init__(self):
        self.grab = _Grab()
        self.grab.setFormatter(logging.Formatter("%(message)s"))
        self.roots = []

    def __enter__(self):
        names = ("", "httpx", "httpcore", "httpcore.http11", "warp3c")
        for name in names:
            logger = logging.getLogger(name)
            self.roots.append((logger, logger.level))
            logger.setLevel(logging.DEBUG)
            logger.addHandler(self.grab)
        return self

    def __exit__(self, *_args):
        for logger, level in self.roots:
            logger.removeHandler(self.grab)
            logger.setLevel(level)

    def lines(self) -> list[str]:
        rendered = []
        for record in self.grab.records:
            rendered.append(self.grab.format(record))
            rendered.append(str(record.msg))
            rendered.append(str(record.args))
            if record.exc_text:
                rendered.append(record.exc_text)
            try:
                rendered.append(record.getMessage())
            except Exception as exc:
                rendered.append(str(exc))
        return rendered


def _forbid_token(test: unittest.TestCase, lines: list[str]) -> None:
    blob = "\n".join(lines)
    test.assertNotIn(_fixture_bot_token(), blob)
    test.assertNotIn("TESTTOKEN", blob)


class TokenRedactionTests(unittest.TestCase):
    def _send(self, status: int | None, *, transport_error: bool = False) -> list[str]:
        token = _fixture_bot_token()

        def handler(request: httpx.Request) -> httpx.Response:
            if "binance" in str(request.url):
                return httpx.Response(200, json={})
            if transport_error:
                raise httpx.ConnectError("unavailable")
            body = {"ok": status == 200}
            if status == 429:
                body = {"ok": False, "parameters": {"retry_after": 30}}
            return httpx.Response(status or 500, json=body)

        transport = httpx.MockTransport(handler)
        with _Capture() as captured:
            send_telegram(token, "99", "halt_on", "HALT file is present", transport=transport)
            with httpx.Client(transport=transport, timeout=5) as client:
                client.get("https://data-api.binance.vision/api/v3/ping")
        return captured.lines()

    def test_send_telegram_logs_omit_the_bot_token(self):
        for status, failed in ((200, False), (403, False), (None, True)):
            with self.subTest(status=status, transport_error=failed):
                lines = self._send(status, transport_error=failed)
                _forbid_token(self, lines)
                blob = "\n".join(lines)
                if status == 200:
                    self.assertIn(
                        'HTTP Request: POST https://api.telegram.org/bot<redacted>/sendMessage "HTTP/1.1 200 OK"',
                        blob,
                    )
                    binance = [line for line in lines if "data-api.binance.vision" in line]
                    self.assertTrue(binance)
                    self.assertTrue(all("<redacted>" not in line for line in binance))
                    self.assertTrue(any("/api/v3/ping" in line for line in binance))
                if failed or (status is not None and status >= 400):
                    self.assertIn("alert_failed", blob)
                    self.assertNotIn("api.telegram.org/bot" + _fixture_bot_token(), blob)

    def test_main_logging_configuration_redacts_the_bot_token(self):
        root = logging.getLogger()
        previous_handlers = list(root.handlers)
        previous_level = root.level
        try:
            configure_logging()
            lines = self._send(200)
            _forbid_token(self, lines)
            self.assertIn("/bot<redacted>/", "\n".join(lines))
            lines = self._send(418)
            _forbid_token(self, lines)
            self.assertIn('"http_status":418', "\n".join(lines))
            lines = self._send(None, transport_error=True)
            _forbid_token(self, lines)
            self.assertIn("alert_failed", "\n".join(lines))
        finally:
            for handler in list(root.handlers):
                if handler not in previous_handlers:
                    root.removeHandler(handler)
            root.setLevel(previous_level)


class ChannelTests(unittest.TestCase):
    def test_no_channel_is_skipped_and_a_later_channel_delivers_the_same_event(self):
        now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            quiet = Settings()
            self.assertTrue(journal.enqueue_alert("cycle_blocked", "boom", now=now))
            with self.assertLogs("warp3c", level="INFO") as logs:
                sent = flush_alerts(journal, lambda event, detail: _deliver(quiet, event, detail), now=now)
            self.assertEqual(sent, 0)
            self.assertEqual(journal.recent_alerts(1)[0]["state"], "skipped_no_channel")
            skipped = [line for line in logs.output if "no_channel" in line]
            self.assertEqual(len(skipped), 1)
            self.assertIn("alert_skipped", skipped[0])
            self.assertEqual(
                flush_alerts(journal, lambda event, detail: _deliver(quiet, event, detail), now=now),
                0,
            )
            self.assertFalse(
                enqueue_incident(journal, "cycle_blocked", "boom", now=now, channel=False)
            )
            with self.assertRaises(AssertionError):
                with self.assertLogs("warp3c", level="INFO"):
                    flush_alerts(journal, lambda event, detail: _deliver(quiet, event, detail), now=now)

            def handler(_request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"ok": True})

            live = Settings(telegram_bot_token="1:abc", telegram_chat_id="99")
            transport = httpx.MockTransport(handler)
            self.assertTrue(enqueue_incident(journal, "cycle_blocked", "boom", now=now, channel=True))
            sent = flush_alerts(
                journal,
                lambda event, detail: _deliver(live, event, detail, transport=transport),
                now=now,
            )
            self.assertEqual(sent, 1)
            states = {row["state"] for row in journal.recent_alerts(5)}
            self.assertIn("delivered", states)
            self.assertIn("skipped_no_channel", states)
            delivered = [row for row in journal.recent_alerts(5) if row["state"] == "delivered"]
            self.assertEqual(len(delivered), 1)
            journal.close()


class TriggerTests(unittest.TestCase):
    def _engine(self, tmp, **overrides):
        cfg = Settings(mode="observe", db_path=os.path.join(tmp, "warp.db"), **overrides)
        journal = OrderJournal(cfg.db_path)
        self.addCleanup(journal.close)
        return cfg, journal, Engine(cfg, None, None, journal, None)

    def test_research_failed_on_the_first_failure_of_a_streak(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            cfg, journal, engine = self._engine(tmp)
            gate = ResearchGate()

            def drive(generation: int, error):
                gate._generation = generation
                gate._error = error
                gate._finished_at = now
                gate.settle(engine, Clocks(), now, cfg)

            drive(1, ResearchError("LLM providers failed"))
            due = journal.due_alerts(now)
            self.assertEqual([row[1] for row in due], ["research_failed:providers_failed"])
            self.assertEqual(due[0][2], "providers_failed ResearchError")
            self.assertLessEqual(len(due[0][2]), 400)
            drive(2, ResearchError("LLM providers failed"))
            self.assertEqual(len(journal.due_alerts(now)), 1)
            journal.mark_delivered(due[0][0], now)
            drive(3, None)
            journal.clear_alert()
            drive(4, ResearchError("LLM providers failed"))
            rows = journal.db.execute(
                "SELECT state FROM alerts WHERE event=?",
                ("research_failed:providers_failed",),
            ).fetchall()
            self.assertEqual(sorted(row[0] for row in rows), ["pending", "resolved"])

    def test_llm_cap_when_the_cap_is_reached(self):
        now = datetime(2026, 11, 26, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            cfg, journal, engine = self._engine(tmp)
            gate = ResearchGate()

            def drive(generation: int):
                gate._generation = generation
                gate._error = ResearchError("LLM daily cap reached")
                gate._finished_at = now
                gate.settle(engine, Clocks(), now, cfg)

            drive(1)
            names = sorted(row[1] for row in journal.due_alerts(now))
            self.assertEqual(names, ["llm_cap", "research_failed:llm_cap"])
            details = {row[1]: row[2] for row in journal.due_alerts(now)}
            self.assertEqual(details["llm_cap"], "llm daily cap reached")
            self.assertNotIn("key", details["llm_cap"].lower())
            drive(2)
            self.assertEqual(sorted(row[1] for row in journal.due_alerts(now)), names)

    def test_deal_closed_reports_realized_pnl_and_fee(self):
        client_id = "warp3c-closed1"
        account_code = "ACCT-9f3a"
        with tempfile.TemporaryDirectory() as tmp:
            cfg, journal, engine = self._engine(tmp)
            stamp = "2026-11-26T00:00:00+00:00"
            journal.insert_warp_bot({
                "warp_id": "closed1",
                "name": "WARP-closed1",
                "client_id": client_id,
                "external_code": "strat-1",
                "kind": "dca",
                "product": "strategy",
                "account_code": account_code,
                "market": "spot",
                "symbol": "SOLUSDT",
                "status": "confirmed",
                "commitment": "10",
                "created_at": stamp,
                "updated_at": stamp,
            })
            row = {
                "status": "completed",
                "clientId": client_id,
                "strategyId": "strat-1",
                "pair": "SOLUSDT",
                "totalEntryCost": "10",
                "totalExitCost": "12",
                "totalEntryAmount": "1",
                "totalExitAmount": "1",
                "totalEntryCommissionCost": "0.2",
                "totalExitCommissionCost": "0.3",
            }
            engine._retire_finished([row])
            engine._retire_finished([row])
            due = journal.due_alerts(datetime.now(timezone.utc) + timedelta(seconds=1))
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0][1], f"deal_closed:SOLUSDT:{client_id}")
            self.assertEqual(due[0][2], "realized 1.5 fee 0.5 status completed")
            self.assertNotIn(account_code, due[0][1] + due[0][2])
            self.assertLessEqual(len(due[0][2]), 400)
            self.assertEqual(journal.warp_bot("closed1")["status"], "closed")

    def test_halt_file_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            _cfg, journal, engine = self._engine(tmp)
            later = datetime.now(timezone.utc) + timedelta(seconds=1)
            engine.monitor()
            self.assertEqual(journal.due_alerts(later), [])
            halt = os.path.join(tmp, "HALT")
            open(halt, "w").close()
            engine.monitor()
            engine.monitor()
            due = journal.due_alerts(datetime.now(timezone.utc) + timedelta(seconds=1))
            self.assertEqual([row[1] for row in due], ["halt_on"])
            self.assertEqual(due[0][2], halt_on_card())
            restarted = Engine(_cfg, None, None, journal, None)
            restarted.monitor()
            due = journal.due_alerts(datetime.now(timezone.utc) + timedelta(seconds=1))
            self.assertEqual([row[1] for row in due], ["halt_on"])
            os.remove(halt)
            flush_alerts(
                journal,
                lambda _event, _detail: True,
                now=datetime(2000, 1, 1, tzinfo=timezone.utc),
            )
            names = [row[1] for row in journal.due_alerts(datetime.now(timezone.utc) + timedelta(seconds=1))]
            self.assertEqual(names, ["halt_on", "halt_off"])
            off = [row for row in names if row == "halt_off"]
            self.assertEqual(off, ["halt_off"])
            detail = [
                row[2]
                for row in journal.due_alerts(datetime.now(timezone.utc) + timedelta(seconds=1))
                if row[1] == "halt_off"
            ]
            self.assertEqual(detail, [halt_off_card()])

    def test_worker_started_reports_mode_and_shared_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(
                mode="paper",
                db_path=os.path.join(tmp, "warp.db"),
                api_key="k",
                api_secret="s",
                profile_code="p123",
                shared_account=True,
                sleeve_usdt=Decimal("50"),
            )
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            with patch.dict(RUNTIME, {"journal": journal, "cfg": cfg, "engine": None}):
                log_event("worker_started", mode="paper", symbols="BTCUSDT", discovery="static")
                log_event("worker_started", mode="paper", symbols="ETHUSDT")
            due = journal.due_alerts(datetime.now(timezone.utc) + timedelta(seconds=1))
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0][1], "worker_started")
            self.assertEqual(due[0][2], "mode=paper shared_account=true")
            self.assertNotIn("BTCUSDT", due[0][2])
            self.assertNotIn("ETHUSDT", due[0][2])

    def test_duplicate_alert_is_suppressed(self):
        now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            self.addCleanup(journal.close)
            self.assertTrue(journal.enqueue_alert("halt_on", "HALT file is present", now=now))
            self.assertFalse(journal.enqueue_alert("halt_on", "HALT file is present", now=now))
            self.assertEqual(len(journal.due_alerts(now)), 1)


class DeliveryTests(unittest.TestCase):
    def test_telegram_429_retry_after_sets_next_attempt(self):
        now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 30}})

        cfg = Settings(telegram_bot_token="1:abc", telegram_chat_id="99")
        transport = httpx.MockTransport(handler)
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            journal.enqueue_alert("halt_on", "HALT file is present", now=now)
            with self.assertLogs("warp3c", level="INFO") as logs:
                sent = flush_alerts(
                    journal,
                    lambda event, detail: _deliver(cfg, event, detail, transport=transport),
                    now=now,
                )
            self.assertEqual(sent, 0)
            self.assertEqual(journal.recent_alerts(1)[0]["state"], "pending")
            self.assertEqual(journal.due_alerts(now + timedelta(seconds=29)), [])
            due = journal.due_alerts(now + timedelta(seconds=30))
            self.assertEqual(len(due), 1)
            stored = journal.db.execute("SELECT next_attempt_at FROM alerts").fetchone()
            self.assertEqual(datetime.fromisoformat(stored[0]), now + timedelta(seconds=30))
            self.assertTrue(any('"http_status":429' in line for line in logs.output))
            blob = "\n".join(logs.output)
            self.assertNotIn("1:abc", blob)
            journal.close()

    def test_flush_caps_at_three_alerts_and_ten_seconds(self):
        now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            for name in ("one", "two", "three", "four"):
                journal.enqueue_alert(name, "detail", now=now)
            sent = flush_alerts(journal, lambda _event, _detail: True, now=now)
            self.assertEqual(sent, 3)
            pending = [row for row in journal.recent_alerts(10) if row["state"] == "pending"]
            self.assertEqual([row["event"] for row in pending], ["four"])

            journal.enqueue_alert("later", "detail", now=now)
            ticks = iter((0, 0, 10))
            sent = flush_alerts(
                journal,
                lambda _event, _detail: True,
                now=now,
                monotonic=lambda: next(ticks),
            )
            self.assertEqual(sent, 1)
            still = [row["event"] for row in journal.recent_alerts(10) if row["state"] == "pending"]
            self.assertIn("later", still)
            journal.close()


class TestAlertRouteTests(unittest.TestCase):
    def test_operator_test_alert_is_authenticated_and_fixed(self):
        operator = "operator-token-16"
        body = json.dumps({"text": "pwned", "mode": "live"}).encode()
        self.assertEqual(dispatch("POST", "/api/alerts/test", {}, body)[0], 401)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(
                mode="paper",
                db_path=os.path.join(tmp, "warp.db"),
                api_key="k",
                api_secret="s",
                profile_code="p123",
            )
            journal = OrderJournal(cfg.db_path)
            headers = {"Authorization": "Bearer " + operator}
            with patch.dict(os.environ, {"WARP_OPERATOR_TOKEN": operator}), patch.dict(
                RUNTIME, {"journal": journal, "cfg": cfg, "engine": None}
            ):
                status, _headers, payload = dispatch("POST", "/api/alerts/test", headers, body)
                again_status, _again_headers, again_payload = dispatch(
                    "POST", "/api/alerts/test", headers, b'{"text":"second"}'
                )
            self.assertEqual(status, 200)
            self.assertEqual(again_status, 200)
            self.assertTrue(json.loads(payload)["enqueued"])
            self.assertFalse(json.loads(again_payload)["enqueued"])
            due = journal.due_alerts(datetime.now(timezone.utc) + timedelta(seconds=1))
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0][1], "alert_test")
            self.assertEqual(due[0][2], "WARP alerts connected (paper mode) - test")
            self.assertNotIn("pwned", due[0][2])
            self.assertNotIn("live", due[0][2])
            self.assertNotIn("second", due[0][2])
            journal.close()


if __name__ == "__main__":
    unittest.main()
