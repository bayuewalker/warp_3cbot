"""Live stream, TradingView screener failures, and the production fixture ban."""

from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path

from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.live import BUS, encode_event, iter_server_events, parse_tickers
from warp3c.operator import _sleeve_view
from warp3c.screener import ScreenerError
from warp3c.state import OrderJournal


class LiveDeskTests(unittest.TestCase):
    def test_event_encoding_and_ticker_parser(self):
        payload = encode_event("prices", {"symbols": {}})
        self.assertTrue(payload.startswith(b"event: prices\n"))
        self.assertTrue(payload.endswith(b"\n\n"))
        rows = parse_tickers(
            [{"symbol": "BTCUSDT", "lastPrice": "100.5", "priceChangePercent": "-1.2"}],
            {"BTCUSDT"},
        )
        self.assertEqual(rows["BTCUSDT"]["last"], "100.5")
        self.assertEqual(rows["BTCUSDT"]["change_pct"], "-1.2")

    def test_stale_quote_does_not_mark_coin_usd(self):
        from datetime import datetime, timedelta, timezone

        from warp3c.operator import _mark_assets

        saved = (dict(BUS.prices), BUS.prices_at, BUS.prices_error)
        try:
            BUS.note_prices({"SOLUSDT": {"last": "100", "change_pct": "1"}}, None)
            BUS.prices_at = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
            accounts = [{"assets": [{"asset": "SOL", "total": "2"}, {"asset": "USDT", "total": "5"}]}]
            _mark_assets(accounts)
            sol, usdt = accounts[0]["assets"]
            self.assertIsNone(sol["usd"])
            self.assertEqual(sol["usd_reason"], "usd_mark_unavailable")
            self.assertGreaterEqual(sol["quote_age_seconds"], 31)
            self.assertEqual(usdt["usd"], "5")
            BUS.prices_at = datetime.now(timezone.utc).isoformat()
            _mark_assets(accounts)
            self.assertEqual(accounts[0]["assets"][0]["usd"], "200")
            self.assertIsNone(accounts[0]["assets"][0]["usd_reason"])
        finally:
            BUS.prices, BUS.prices_at, BUS.prices_error = saved

    def test_missing_account_sleeve_is_unavailable(self):
        view = _sleeve_view(Settings(), [], None)
        self.assertIsNone(view["eligible"])
        self.assertEqual(view["reason"], "account_unavailable")

    def test_screener_error_keeps_the_last_tradingview_rows(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"))
            journal = OrderJournal(cfg.db_path)
            try:
                from test_failures import Markets, Research

                engine = Engine(cfg, object(), Research(), journal, Markets(), screener=object())

                class Down:
                    def scan(self, _filters):
                        raise ScreenerError("TradingView scanner unavailable")

                engine.screener = Down()
                engine.screener_hits = ["kept"]
                engine.screener_source = "tradingview"
                engine.screener_fetched_at = "2020-01-01T00:00:00+00:00"
                engine._refresh_screener()
                self.assertEqual(engine.screener_hits, ["kept"])
                self.assertEqual(engine.screener_source, "tradingview")
                self.assertIn("TradingView", engine.screener_error)
            finally:
                journal.close()

    def test_stream_requires_auth_then_heartbeats(self):
        from fastapi.testclient import TestClient

        from warp3c.webapi import create_app

        previous = os.environ.get("WARP_OPERATOR_TOKEN")
        os.environ["WARP_OPERATOR_TOKEN"] = "operator-token-16"
        BUS.heartbeat = 0.01
        client = TestClient(create_app())
        inbox = BUS.subscribe()
        stop = threading.Event()
        try:
            denied = client.get("/api/stream")
            self.assertEqual(denied.status_code, 401)
            self.assertNotIn("accounts", denied.text)
            self.assertIn("/api/stream", {getattr(route, "path", None) for route in create_app().routes})
            self.assertIsNotNone(inbox)
            text = ""
            for payload in iter_server_events(inbox, BUS, stop):
                text += payload.decode()
                if "heartbeat" in text:
                    stop.set()
            self.assertIn("event: hello", text)
            self.assertIn("event: heartbeat", text)
        finally:
            if inbox is not None:
                BUS.unsubscribe(inbox)
            BUS.heartbeat = 15
            if previous is None:
                os.environ.pop("WARP_OPERATOR_TOKEN", None)
            else:
                os.environ["WARP_OPERATOR_TOKEN"] = previous

    def test_production_code_has_no_mock_fixtures(self):
        banned = ("MockTransport", "lorem ipsum", "sample_price", "hardcoded_equity")
        root = Path(__file__).resolve().parents[1] / "src" / "warp3c"
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, text, f"{path.name} contains {token}")
