"""Read-only open positions per 3Commas account, and their stream events."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from warp3c.book import MAX_POSITION_READS, collect, parse_position, positions_view
from warp3c.threecommas import IntegrationError


def _bot(name="ALPHA", code="b1", cnt=1, pnl=None, row_id="r1", kind_key="dcaTradingType", status="active"):
    return {
        "name": name,
        "code": code,
        "status": status,
        kind_key: "long",
        "profiles": [{"profile": {"code": "acct-1", "name_of_account": "Main"}}],
        "unrealizedPnl": {
            "cnt": cnt,
            "pnlFiat": pnl or 0,
            "summary": {row_id: {"pnlFiat": pnl}} if pnl is not None else None,
        },
    }


# Shape copied from a 3Commas /open_api/dca_bots/:code/positions row (values altered).
ROW = {
    "_id": "r1",
    "status": "entered",
    "paperTrading": False,
    "enteredAt": "2026-11-21T07:15:16.509Z",
    "createdAt": "2026-11-21T06:18:26.777Z",
    "lastPrice": 0.2117,
    "price": 0.2257,
    "stopPrice": None,
    "totalEntryAmount": 3101.1,
    "totalExitAmount": 0,
    "totalEntryCost": 699.94,
    "totalExitCost": 0,
    "currentPosition": -6.21,
    "extraOrderCount": 7,
    "extraOrderCompletedCount": 5,
    "takeProfits": [{"price": 0.2290855, "status": "not triggered"}],
    "pair": "MMT/USDT",
    "pairCode": "MMTUSDT",
    "pairSymbols": {"ref": "MMT", "base": "USDT"},
    "profileStrategies": [{"profile": {"name_of_account": "Main", "code": "acct-1"}}],
}


class Broker:
    """Read-only test double. It has no write methods, so any write attempt raises AttributeError."""

    def __init__(self, dca, grid=(), rows=None, fail=()):
        self.dca = list(dca)
        self.grid = list(grid)
        self.rows = rows or {}
        self.fail = set(fail)
        self.calls: list[tuple] = []

    def list_dca_bots(self):
        self.calls.append(("GET", "dca_bots"))
        if "dca" in self.fail:
            raise IntegrationError("down")
        return self.dca

    def list_grid_bots(self):
        self.calls.append(("GET", "grid_bots"))
        return self.grid

    def get_bot_positions(self, kind, code):
        self.calls.append(("GET", f"{kind}/{code}/positions"))
        if code in self.fail:
            raise IntegrationError("HTTP 500")
        return {"rows": self.rows.get(code, [])}


class ParseTests(unittest.TestCase):
    def test_full_row_reports_every_field_with_sources(self):
        row = parse_position(ROW, bot=_bot(pnl=-43.99), kind="dca")
        self.assertEqual(row["account_code"], "acct-1")
        self.assertEqual(row["pair"], "MMT/USDT")
        self.assertEqual(row["symbol"], "MMTUSDT")
        self.assertEqual(row["side"], "long")
        self.assertEqual(row["owner"], "external")
        self.assertEqual(row["average_price"], "0.2257")
        self.assertEqual(row["size"], "3101.1")
        self.assertEqual(row["current_price"], "0.2117")
        self.assertEqual(row["current_price_source"], "3commas")
        self.assertEqual(row["pnl_usd"], "-43.99")
        self.assertEqual(row["pnl_pct"], "-6.21")
        self.assertEqual((row["safety_used"], row["safety_max"]), (5, 7))
        self.assertEqual(row["take_profits"], ["0.2290855"])
        self.assertIsNone(row["stop_price"])
        self.assertTrue(row["opened_at"].startswith("2026-11-21T07:15:16"))

    def test_missing_fields_stay_unavailable_not_zero(self):
        row = parse_position({"_id": "x", "pair": "BTC/USDT", "price": "", "lastPrice": 0}, bot=None, kind="grid")
        for key in (
            "average_price",
            "size",
            "cost",
            "current_price",
            "pnl_usd",
            "pnl_pct",
            "safety_used",
            "safety_max",
            "stop_price",
            "opened_at",
            "side",
            "account_code",
        ):
            self.assertIsNone(row[key], key)
        self.assertEqual(row["take_profits"], [])
        self.assertIsNone(row["current_price_source"])

    def test_malformed_values_are_rejected(self):
        row = parse_position(
            {"price": "abc", "extraOrderCount": True, "enteredAt": "yesterday", "currentPosition": "NaN"},
            bot=None,
            kind="dca",
        )
        self.assertIsNone(row["average_price"])
        self.assertIsNone(row["safety_max"])
        self.assertIsNone(row["opened_at"])
        self.assertIsNone(row["pnl_pct"])

    def test_warp_prefix_marks_ownership(self):
        self.assertEqual(parse_position(ROW, bot=_bot(name="WARP-0a1b2c3d"), kind="dca")["owner"], "warp")
        self.assertEqual(parse_position(ROW, bot=_bot(name="OTHER"), kind="dca")["owner"], "external")


class CollectTests(unittest.TestCase):
    def test_reads_only_bots_with_open_positions(self):
        broker = Broker([_bot(code="b1", cnt=1), _bot(code="b2", cnt=0)], rows={"b1": [ROW]})
        book = collect(broker)
        self.assertEqual(len(book["rows"]), 1)
        self.assertEqual(book["bots_checked"], 2)
        self.assertNotIn(("GET", "dca/b2/positions"), broker.calls)
        self.assertTrue(all(method == "GET" for method, _ in broker.calls))

    def test_failed_reads_are_reported_and_others_kept(self):
        broker = Broker([_bot(code="b1"), _bot(code="b3", name="BROKEN")], rows={"b1": [ROW]}, fail={"b3"})
        book = collect(broker)
        self.assertEqual(len(book["rows"]), 1)
        self.assertTrue(any("BROKEN" in error for error in book["errors"]))
        broker = Broker([], fail={"dca"})
        self.assertTrue(collect(broker)["errors"][0].startswith("dca_bots"))

    def test_position_reads_are_capped(self):
        bots = [_bot(code=f"b{i}") for i in range(MAX_POSITION_READS + 5)]
        book = collect(Broker(bots))
        self.assertEqual(book["position_reads"], MAX_POSITION_READS)
        self.assertIn("position_read_cap", book["errors"])

    def test_strategies_use_the_journal_for_ownership(self):
        book = collect(
            Broker([]),
            strategies=[{**ROW, "clientId": "mine"}, {**ROW, "clientId": "theirs"}],
            warp_client=lambda client: client == "mine",
        )
        self.assertEqual([row["owner"] for row in book["rows"]], ["warp", "external"])


class ViewTests(unittest.TestCase):
    def test_binance_price_fills_only_missing_values_and_is_labelled(self):
        bare = {**ROW, "lastPrice": None, "currentPosition": None}
        book = {"rows": [parse_position(bare, bot=_bot(), kind="dca")], "as_of": "t", "errors": []}
        view = positions_view(book, prices={"MMTUSDT": {"last": "0.25"}}, prices_fresh=True)
        row = view["positions"][0]
        self.assertEqual(row["current_price"], "0.25")
        self.assertEqual(row["current_price_source"], "binance")
        self.assertEqual(row["pnl_pct_source"], "computed")
        self.assertEqual(row["pnl_usd_source"], "computed")
        self.assertGreater(float(row["pnl_usd"]), 0)
        self.assertEqual(view["by_account"], {"acct-1": 1})

    def test_stale_prices_are_not_used(self):
        bare = {**ROW, "lastPrice": None, "currentPosition": None}
        book = {"rows": [parse_position(bare, bot=_bot(), kind="dca")], "as_of": "t", "errors": []}
        row = positions_view(book, prices={"MMTUSDT": {"last": "0.25"}}, prices_fresh=False)["positions"][0]
        self.assertIsNone(row["current_price"])
        self.assertIsNone(row["pnl_usd"])
        self.assertIsNone(row["pnl_pct"])

    def test_reported_values_win_over_the_ticker(self):
        book = {"rows": [parse_position(ROW, bot=_bot(pnl=-43.99), kind="dca")], "as_of": "t", "errors": []}
        row = positions_view(book, prices={"MMTUSDT": {"last": "9"}}, prices_fresh=True)["positions"][0]
        self.assertEqual(row["current_price"], "0.2117")
        self.assertEqual(row["pnl_usd"], "-43.99")

    def test_not_loaded_is_explicit(self):
        view = positions_view(None, prices={}, prices_fresh=False)
        self.assertEqual(view["positions"], [])
        self.assertEqual(view["error"], "not_loaded")
        self.assertIsNone(view["as_of"])
        self.assertTrue(view["readonly"])


class MarkTests(unittest.TestCase):
    def test_mark_symbols_cover_holdings_and_positions_outside_the_tape(self):
        from warp3c.accounts import BrokerAccount
        from warp3c.live import mark_symbols
        from decimal import Decimal

        account = BrokerAccount(
            "a", "Main", False, "active", "BINANCE", "BINANCE-SPOT", "spot", "USDT",
            Decimal("1"), Decimal("0"), Decimal("1"), (("BNB", "0.3"), ("MMT", "10"), ("USDT", "5")),
        )
        rows = [{"symbol": "MMTUSDT", "quote_asset": "USDT"}, {"symbol": "ETHBTC", "quote_asset": "BTC"}]
        self.assertEqual(mark_symbols([account], rows, {"BNBUSDT"}), ["MMTUSDT"])

    def test_marks_expire_and_tape_wins(self):
        from warp3c.live import LiveBus

        bus = LiveBus()
        bus.note_marks({"MMTUSDT": {"last": "0.2"}})
        self.assertEqual(bus.fresh_marks()["MMTUSDT"]["last"], "0.2")
        bus.marks_at = "2000-01-01T00:00:00+00:00"
        self.assertEqual(bus.fresh_marks(), {})

    def test_poll_marks_skips_unlisted_symbols(self):
        import httpx

        from warp3c.live import poll_marks

        def handler(request):
            symbol = request.url.params.get("symbol")
            if symbol == "NOPEUSDT":
                return httpx.Response(400, json={"code": -1121})
            return httpx.Response(200, json={"symbol": symbol, "price": "1.5"})

        client = httpx.Client(base_url="https://binance.test", transport=httpx.MockTransport(handler))
        self.assertEqual(poll_marks(client, ["NOPEUSDT", "MMTUSDT"]), {"MMTUSDT": {"last": "1.5", "change_pct": None}})


class PublishTests(unittest.TestCase):
    def test_publish_reads_pushes_accounts_and_positions_events(self):
        from warp3c.config import Settings
        from warp3c.engine import Engine
        from warp3c.live import BUS
        from warp3c.operator import ROUTES, SNAPSHOT, publish_reads
        from warp3c.state import OrderJournal

        self.assertIn("/api/account-positions", ROUTES)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"), api_key="k", api_secret="s")
            journal = OrderJournal(cfg.db_path)
            inbox = BUS.subscribe()
            try:
                from test_failures import Markets, Research

                broker = Broker([_bot(pnl=-43.99)], rows={"b1": [ROW]})
                engine = Engine(cfg, broker, Research(), journal, Markets())
                engine.refresh_position_book(force=True)
                publish_reads(cfg, journal, engine)
                events = {}
                while not inbox.empty():
                    text = inbox.get_nowait().decode()
                    name = text.split("\n", 1)[0].removeprefix("event: ")
                    events[name] = json.loads(text.split("data: ", 1)[1])
                self.assertIn("accounts", events)
                self.assertIn("positions", events)
                self.assertEqual(events["positions"]["positions"][0]["pnl_usd"], "-43.99")
                self.assertEqual(SNAPSHOT["/api/account-positions"]["by_account"], {"acct-1": 1})
                self.assertTrue(events["accounts"]["readonly"])
                self.assertTrue(all(method == "GET" for method, _ in broker.calls))
                # A second refresh inside the cadence does not call 3Commas again.
                before = len(broker.calls)
                engine.refresh_position_book()
                self.assertEqual(len(broker.calls), before)
            finally:
                BUS.unsubscribe(inbox)
                journal.close()


if __name__ == "__main__":
    unittest.main()
