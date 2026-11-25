"""P5 release-gate contract tests. Test-only: nothing here changes, patches in, or relaxes product code.

Part A is the contract matrix: each data-contract row from the P5 brief has at least one assertion here or in
an earlier test file (see docs/dashboard/TRADINGDESK_2_QA.md for the row to test mapping).
Part B guards the read-only boundary: the set of state-changing routes, broker write calls and browser write
targets is pinned, so a new order path cannot appear without this file failing.
Part C records known gaps as expected failures. They assert the safe behaviour that the product does not yet have.
"""

from __future__ import annotations

import ast
import json
import os
import re
import secrets
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from test_account_positions import ROW, Broker, _bot

from warp3c import operator
from warp3c.book import collect, parse_position, positions_view
from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.httpapi import dispatch
from warp3c.operator import RUNTIME, SNAPSHOT, publish
from warp3c.state import OrderJournal
from warp3c.threecommas import IntegrationError, ThreeCommas

ROOT = Path(__file__).resolve().parents[1]
PAGE = 100


def envelope(items, *, page=1, total=None, pages=1, limit=PAGE):
    count = len(items) if total is None else total
    return {"items": items, "pagination": {"page": page, "limit": limit, "total": count, "pages": pages}}


def live_client(handler):
    return ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))


class AuthedCase(unittest.TestCase):
    """Dispatches requests as the operator, with a token that exists only for this test."""

    def setUp(self):
        self.token = secrets.token_urlsafe(24)
        patcher = patch.dict(os.environ, {"WARP_OPERATOR_TOKEN": self.token})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.headers = {"Authorization": "Bearer " + self.token}

    def get(self, path):
        status, _headers, body = dispatch("GET", path, self.headers, b"")
        return status, json.loads(body)

    def post(self, path, payload):
        status, _headers, body = dispatch("POST", path, self.headers, json.dumps(payload).encode())
        return status, json.loads(body)


# ───────────────────────────── Part A: contract matrix ─────────────────────────────


class ColdStartTests(AuthedCase):
    """Row: cold start, no first snapshot. A worker that has published nothing must not look confirmed."""

    def test_every_route_without_a_snapshot_has_no_source_time(self):
        with patch.dict(SNAPSHOT, {}, clear=True), patch.dict(RUNTIME, {"journal": None, "cfg": None, "engine": None}):
            for path in operator.ROUTES:
                status, payload = self.get(path)
                self.assertEqual(status, 200, path)
                self.assertIsInstance(payload, dict, path)
                for key in ("as_of", "published_at", "fetched_at"):
                    self.assertIsNone(payload.get(key), (path, key))
                self.assertFalse(payload.get("positions"), path)

    def test_account_book_without_a_snapshot_is_not_loaded_and_never_an_empty_list(self):
        view = positions_view(None, prices={}, prices_fresh=False)
        self.assertEqual(view["error"], "not_loaded")
        self.assertIsNone(view["as_of"])
        self.assertEqual((view["positions"], view["by_account"], view["errors"]), ([], {}, []))
        self.assertFalse(view["stale"])

    def test_unauthenticated_cold_reads_are_refused(self):
        for path in operator.ROUTES:
            self.assertEqual(dispatch("GET", path, {}, b"")[0], 401, path)


class ConfirmedZeroTests(unittest.TestCase):
    """Row: confirmed zero. An empty successful read is a zero with a time, and a zero field stays zero."""

    def test_a_successful_empty_book_has_a_source_time_and_no_error(self):
        book = collect(Broker([]), strategies=[])
        self.assertEqual((book["rows"], book["errors"], book["stale"]), ([], [], False))
        self.assertIsNotNone(datetime.fromisoformat(book["as_of"]).tzinfo)
        view = positions_view(book, prices={}, prices_fresh=False)
        self.assertEqual((view["positions"], view["by_account"], view["error"], view["errors"]), ([], {}, None, []))
        self.assertIsNotNone(view["as_of"])
        self.assertNotEqual(view["error"], positions_view(None, prices={}, prices_fresh=False)["error"])

    def test_zero_counts_and_zero_pnl_survive_parsing(self):
        row = {**ROW, "currentPosition": 0, "extraOrderCompletedCount": 0, "takeProfits": []}
        parsed = parse_position(row, bot=_bot(), kind="dca")
        self.assertEqual(parsed["pnl_pct"], "0")
        self.assertEqual(parsed["safety_used"], 0)
        self.assertEqual(parsed["take_profits"], [])
        self.assertEqual(parsed["pnl_pct_source"], "3commas")

    def test_a_zero_price_is_not_a_price(self):
        parsed = parse_position({**ROW, "lastPrice": 0, "price": 0}, bot=_bot(), kind="dca")
        self.assertIsNone(parsed["current_price"])
        self.assertIsNone(parsed["average_price"])
        self.assertIsNone(parsed["current_price_source"])


class MissingSoldQuantityAndCostTests(unittest.TestCase):
    """Row: missing sold quantity or cost. Size and cost are independent and never default to the entry value."""

    def test_unusable_exit_inputs_leave_the_matching_value_unreported(self):
        cases = {
            "missing exit": ({"totalExitAmount": None}, "size"),
            "blank exit": ({"totalExitAmount": ""}, "size"),
            "negative exit": ({"totalExitAmount": -1}, "size"),
            "boolean exit": ({"totalExitAmount": True}, "size"),
            "text exit": ({"totalExitAmount": "n/a"}, "size"),
            "NaN exit": ({"totalExitAmount": "NaN"}, "size"),
            "Infinity exit": ({"totalExitAmount": "Infinity"}, "size"),
            "exit beyond entry": ({"totalExitAmount": 9999999}, "size"),
            "fully sold": ({"totalExitAmount": ROW["totalEntryAmount"]}, "size"),
            "missing cost exit": ({"totalExitCost": None}, "cost"),
            "negative cost exit": ({"totalExitCost": -5}, "cost"),
            "cost exit beyond entry": ({"totalExitCost": 99999999}, "cost"),
        }
        for label, (change, field) in cases.items():
            with self.subTest(label):
                parsed = parse_position({**ROW, **change}, bot=_bot(), kind="dca")
                other = "cost" if field == "size" else "size"
                self.assertIsNone(parsed[field], label)
                self.assertIsNotNone(parsed[other], f"{label}: the independent field was dropped too")
                self.assertNotEqual(parsed[field], "0")

    def test_missing_entry_never_becomes_a_remaining_size(self):
        parsed = parse_position({**ROW, "totalEntryAmount": None, "totalEntryCost": None}, bot=_bot(), kind="dca")
        self.assertIsNone(parsed["size"])
        self.assertIsNone(parsed["cost"])

    def test_pnl_in_usd_needs_the_broker_summary_for_the_same_row(self):
        parsed = parse_position(ROW, bot=_bot(pnl=12.5, row_id="another-row"), kind="dca")
        self.assertIsNone(parsed["pnl_usd"])
        self.assertIsNone(parsed["pnl_usd_source"])


class PartialPositionTests(unittest.TestCase):
    """Row: partial positions. A count mismatch, a bad row or a failed bot marks the book incomplete and keeps the rest."""

    def test_the_flag_is_raised_by_count_mismatch_even_when_every_returned_row_is_complete(self):
        # Mechanism only. Why production grid bots report "incomplete position rows" is unverified: it needs a
        # read-only capture of a live grid bot's positions response, which this lane may not make.
        for reported, rows in ((3, [ROW]), (1, [ROW, {**ROW, "_id": "r2"}]), (1, [])):
            with self.subTest(reported=reported, rows=len(rows)):
                book = collect(Broker([], grid=[_bot(code="g1", cnt=reported, kind_key="gridTradingType")],
                                      rows={"g1": rows}))
                self.assertEqual(book["errors"], ["grid g1: incomplete position rows"])
                self.assertEqual(len(book["rows"]), len(rows))
                self.assertFalse(book["stale"])

    def test_a_failed_bot_read_keeps_the_other_bots(self):
        book = collect(Broker([_bot(code="a"), _bot(code="b", row_id="r9")], rows={"a": [ROW], "b": []}, fail={"b"}))
        self.assertEqual(len(book["rows"]), 1)
        self.assertEqual(len(book["errors"]), 1)
        self.assertIn("HTTP 500", book["errors"][0])
        view = positions_view(book, prices={}, prices_fresh=False)
        self.assertEqual(view["errors"], book["errors"])
        self.assertEqual(view["by_account"], {"acct-1": 1})

    def test_a_row_without_a_broker_id_is_kept_and_flagged(self):
        nameless = {key: value for key, value in ROW.items() if key != "_id"}
        book = collect(Broker([_bot()], rows={"b1": [nameless]}))
        self.assertEqual(len(book["rows"]), 1)
        self.assertIsNone(book["rows"][0]["id"])
        self.assertTrue(book["errors"])


class StaleStrategyBookTests(unittest.TestCase):
    """Row: stale strategy book. Retained rows are published as stale, with their last good time or none."""

    def test_the_public_view_carries_stale_error_and_the_last_good_time(self):
        good = "2026-11-24T00:00:00+00:00"
        strategy = {"strategyId": "s1", "clientId": "c", "pair": "BTC/USDT",
                    "pairSymbols": {"base": "BTC", "quote": "USDT"}}
        book = collect(Broker([]), strategies=[strategy], strategies_as_of=good, strategy_error="HTTP 503")
        view = positions_view(book, prices={}, prices_fresh=False)
        self.assertTrue(view["stale"])
        self.assertEqual(view["as_of"], good)
        self.assertIn("strategies: HTTP 503", view["errors"])
        self.assertEqual(len(view["positions"]), 1)
        self.assertTrue(view["positions"][0]["stale"])

    def test_a_first_failure_has_no_time_at_all(self):
        view = positions_view(collect(Broker([]), strategy_error="HTTP 429"), prices={}, prices_fresh=False)
        self.assertTrue(view["stale"])
        self.assertIsNone(view["as_of"])


class UnassignedRowTests(unittest.TestCase):
    """Row: accounts fail and Unassigned stays visible. Rows without a resolvable account are still published."""

    def test_rows_with_no_or_ambiguous_account_are_counted_as_unknown_and_not_dropped(self):
        two = {**_bot(code="x"), "profiles": [{"profile": {"code": "a", "name_of_account": "A"}},
                                                {"profile": {"code": "b", "name_of_account": "B"}}]}
        none = {**_bot(code="y", row_id="r2"), "profiles": []}
        rows = {"x": [{**ROW, "profileStrategies": []}], "y": [{**ROW, "_id": "r2", "profileStrategies": None}]}
        view = positions_view(collect(Broker([two, none], rows=rows)), prices={}, prices_fresh=False)
        self.assertEqual(len(view["positions"]), 2)
        self.assertEqual(view["by_account"], {"unknown": 2})
        for row in view["positions"]:
            self.assertIsNone(row["account_code"])
            self.assertEqual(row["owner"], "external")

    def test_by_account_always_adds_up_to_the_rows(self):
        mixed = [{**_bot(code="a"), "profiles": [{"profile": {"code": "p1", "name_of_account": "One"}}]},
                 {**_bot(code="b", row_id="r2"), "profiles": []}]
        rows = {"a": [{**ROW, "profileStrategies": []}], "b": [{**ROW, "_id": "r2", "profileStrategies": []}]}
        view = positions_view(collect(Broker(mixed, rows=rows)), prices={}, prices_fresh=False)
        self.assertEqual(sum(view["by_account"].values()), len(view["positions"]))
        self.assertEqual(view["by_account"], {"p1": 1, "unknown": 1})


class BadPaginationTests(unittest.TestCase):
    """Row: bad pagination. Every malformed page sequence fails closed instead of returning a partial list."""

    def stubs(self, count, prefix="s"):
        return [{"strategyId": f"{prefix}{i:03d}"} for i in range(count)]

    def run_pages(self, pages):
        """pages maps the requested page number to a JSON body, or to a callable that builds one."""
        def handler(request):
            number = int(request.url.params["page"])
            body = pages.get(number)
            if body is None:
                return httpx.Response(200, json=envelope([], page=number, total=0, pages=0))
            return httpx.Response(200, json=body)

        client = live_client(handler)
        try:
            return client.get_live()
        finally:
            client.close()

    def test_malformed_sequences_fail_closed(self):
        full = self.stubs(PAGE)
        cases = {
            "duplicate id across pages": {
                1: envelope(full, page=1, total=101, pages=2),
                2: envelope([full[0]], page=2, total=101, pages=2)},
            "missing id": {1: envelope([{"name": "x"}], page=1, total=1, pages=1)},
            "empty id": {1: envelope([{"strategyId": ""}], page=1, total=1, pages=1)},
            "non-string id": {1: envelope([{"strategyId": 7}], page=1, total=1, pages=1)},
            "truncated middle page": {
                1: envelope(full[:99], page=1, total=201, pages=3)},
            "total changes between pages": {
                1: envelope(full, page=1, total=101, pages=2),
                2: envelope(self.stubs(1, "t"), page=2, total=102, pages=2)},
            "page count changes between pages": {
                1: envelope(full, page=1, total=101, pages=2),
                2: envelope(self.stubs(1, "t"), page=2, total=101, pages=3)},
            "limit mismatch": {1: envelope(self.stubs(1), page=1, total=1, pages=1, limit=50)},
            "wrong page number": {1: envelope(self.stubs(1), page=2, total=1, pages=1)},
            "last page too long": {1: envelope(self.stubs(PAGE + 1), page=1, total=PAGE + 1, pages=1)},
            "items on a zero-page list": {1: envelope(self.stubs(1), page=1, total=1, pages=0)},
            "empty list claiming many pages": {1: envelope([], page=1, total=0, pages=5)},
            "page count does not match total": {1: envelope(self.stubs(1), page=1, total=1, pages=4)},
            "negative total": {1: envelope([], page=1, total=-1, pages=0)},
            "string metadata": {1: {"items": self.stubs(1), "pagination": {
                "page": "1", "limit": PAGE, "total": 1, "pages": 1}}},
            "boolean metadata": {1: {"items": self.stubs(1), "pagination": {
                "page": True, "limit": PAGE, "total": 1, "pages": 1}}},
            "non-object item": {1: envelope(["x"], page=1, total=1, pages=1)},
            "missing pagination": {1: {"items": self.stubs(1)}},
            "missing items": {1: {"pagination": {"page": 1, "limit": PAGE, "total": 1, "pages": 1}}},
            "list instead of envelope": {1: [{"strategyId": "s"}]},
        }
        for label, pages in cases.items():
            with self.subTest(label), self.assertRaises(IntegrationError):
                self.run_pages(pages)

    def test_a_well_formed_two_page_list_still_loads(self):
        full = self.stubs(PAGE)
        rows = self.run_pages({1: envelope(full, page=1, total=101, pages=2),
                               2: envelope(self.stubs(1, "t"), page=2, total=101, pages=2)})
        self.assertEqual(len(rows), 101)

    def test_server_errors_and_rate_limits_do_not_return_partial_lists(self):
        for code in (400, 401, 403, 404, 429, 500, 502, 503):
            with self.subTest(code):
                client = live_client(lambda _request, code=code: httpx.Response(code, json={"error": "fixture"}))
                try:
                    with self.assertRaises(IntegrationError):
                        client.get_live()
                finally:
                    client.close()


class DuplicateIdTests(unittest.TestCase):
    """Row: duplicate ids. Duplicates inside one cabinet read are counted once and the book is flagged partial."""

    def test_the_same_position_twice_in_one_bot_is_one_row_and_flags_the_book(self):
        book = collect(Broker([_bot(cnt=1)], rows={"b1": [ROW, dict(ROW)]}))
        self.assertEqual([row["id"] for row in book["rows"]], ["r1"])
        self.assertEqual(book["errors"], ["dca b1: incomplete position rows"])

    def test_rows_without_ids_are_never_merged(self):
        nameless = {key: value for key, value in ROW.items() if key != "_id"}
        book = collect(Broker([_bot(cnt=2)], rows={"b1": [nameless, dict(nameless)]}))
        self.assertEqual(len(book["rows"]), 2)
        self.assertTrue(book["errors"])

    def test_the_same_id_under_two_bots_is_two_rows_one_per_bot(self):
        # The dedupe window is one bot's response. Two bots are two sources, so nothing is silently dropped.
        book = collect(Broker([_bot(code="a"), _bot(code="b")], rows={"a": [ROW], "b": [ROW]}))
        self.assertEqual([row["bot_code"] for row in book["rows"]], ["a", "b"])
        self.assertEqual(book["errors"], [])

    def test_duplicate_strategy_ids_are_refused_by_the_client_before_they_reach_the_book(self):
        page = envelope([{"strategyId": "dup"}, {"strategyId": "dup"}], total=2)
        client = live_client(lambda _request: httpx.Response(200, json=page))
        try:
            with self.assertRaises(IntegrationError) as caught:
                client.get_live()
            self.assertIn("Duplicate", str(caught.exception))
        finally:
            client.close()


class MissingMarketMarkTests(unittest.TestCase):
    """Row: missing market mark. Without a fresh mark nothing is computed, and nothing is labelled as computed."""

    def row(self, **extra):
        base = {"symbol": "ZZZUSDT", "average_price": "10", "size": "2", "side": "long", "quote_asset": "USDT",
                "current_price": None, "pnl_pct": None, "pnl_usd": None, "pnl_pct_source": None,
                "pnl_usd_source": None, "current_price_source": None}
        return {**base, **extra}

    def view(self, row, prices, fresh):
        return positions_view({"rows": [row], "errors": [], "as_of": datetime.now(timezone.utc).isoformat(),
                               "stale": False}, prices=prices, prices_fresh=fresh)["positions"][0]

    def test_no_symbol_no_mark_and_no_computed_values(self):
        shown = self.view(self.row(), {"OTHERUSDT": {"last": "5"}}, True)
        self.assertEqual((shown["current_price"], shown["pnl_pct"], shown["pnl_usd"]), (None, None, None))
        self.assertEqual((shown["pnl_pct_source"], shown["pnl_usd_source"], shown["current_price_source"]),
                         (None, None, None))

    def test_a_retained_tape_is_not_a_mark(self):
        shown = self.view(self.row(), {"ZZZUSDT": {"last": "11"}}, False)
        self.assertIsNone(shown["current_price"])
        self.assertIsNone(shown["pnl_pct"])

    def test_a_present_mark_labels_its_source_and_a_side_is_required(self):
        shown = self.view(self.row(), {"ZZZUSDT": {"last": "11"}}, True)
        self.assertEqual((shown["current_price"], shown["current_price_source"]), ("11", "binance"))
        self.assertEqual((shown["pnl_pct"], shown["pnl_pct_source"]), ("10", "computed"))
        self.assertEqual((shown["pnl_usd"], shown["pnl_usd_source"]), ("2", "computed"))
        sideless = self.view(self.row(side=None), {"ZZZUSDT": {"last": "11"}}, True)
        self.assertEqual(sideless["current_price"], "11")
        self.assertIsNone(sideless["pnl_pct"])
        self.assertIsNone(sideless["pnl_usd"])

    def test_usd_pnl_is_never_computed_for_a_non_dollar_quote(self):
        shown = self.view(self.row(quote_asset="BTC"), {"ZZZUSDT": {"last": "11"}}, True)
        self.assertEqual(shown["pnl_pct"], "10")
        self.assertIsNone(shown["pnl_usd"])


class UnknownSubmissionTests(AuthedCase):
    """Row: unknown submission. A submission with no confirmation is published as unknown and keeps blocking."""

    def test_unknown_stays_visible_through_the_journal_route_and_blocks_a_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Settings(mode="observe", db_path=os.path.join(directory, "p5.db"))
            journal = OrderJournal(cfg.db_path)
            client_id = "warp3c-" + "7" * 32
            try:
                journal.reserve(client_id, "BTCUSDT", {"fixture": True})
                rows = journal.recent_submissions(30)
                self.assertEqual([(row["client_id"], row["state"], row["strategy_id"]) for row in rows],
                                 [(client_id, "unknown", None)])
                self.assertTrue(journal.recently_submitted("BTCUSDT"))
                self.assertFalse(journal.recently_submitted("ETHUSDT"))
                with patch.dict(SNAPSHOT, {"/api/journal": {"submissions": rows}}):
                    status, payload = self.get("/api/journal")
                self.assertEqual(status, 200)
                self.assertEqual(payload["submissions"][0]["state"], "unknown")
                self.assertIsNone(payload["submissions"][0]["strategy_id"])
                # A broker listing that does not contain it leaves it unknown. It is never promoted or dropped.
                self.assertEqual(journal.reconcile([]), [client_id])
                self.assertEqual(journal.recent_submissions(30)[0]["state"], "unknown")
            finally:
                journal.close()


class HaltContractTests(AuthedCase):
    """Row: HALT. The HALT file overrides a snapshot taken before it, on the REST route and the stream frame."""

    def test_the_halt_file_wins_over_a_healthy_snapshot_on_rest_and_stream(self):
        from warp3c.httpapi import broadcast_live
        from warp3c.live import BUS

        healthy = {"mode": "observe", "halt": False, "risk_permitted": True, "execution_permitted": True,
                   "published_at": datetime.now(timezone.utc).isoformat()}
        with tempfile.TemporaryDirectory() as directory:
            halt_path = Path(directory) / "HALT"
            halt_path.touch()
            inbox = BUS.subscribe()
            self.assertIsNotNone(inbox)
            try:
                with patch.dict(SNAPSHOT, {"/api/status": healthy, "/api/breakers": {"halt": False}}), \
                        patch.dict(operator.HEALTH, {"halt_path": str(halt_path)}):
                    status, payload = self.get("/api/status")
                    self.assertTrue(payload["halt"])
                    self.assertFalse(payload["execution_permitted"])
                    self.assertEqual(payload["gating"], "halt")
                    self.assertTrue(self.get("/api/breakers")[1]["halt"])
                    broadcast_live()
                    frame = inbox.get_nowait().decode()
            finally:
                BUS.unsubscribe(inbox)
        self.assertIn("event: snapshot", frame)
        routes = json.loads(frame.split("data: ", 1)[1])["routes"]
        self.assertTrue(routes["/api/status"]["halt"])
        self.assertFalse(routes["/api/status"]["execution_permitted"])
        self.assertTrue(routes["/api/breakers"]["halt"])

    def test_removing_the_halt_file_does_not_reopen_a_snapshot_taken_under_halt(self):
        halted = {"mode": "observe", "halt": True, "reason": "halt", "execution_permitted": False,
                  "published_at": datetime.now(timezone.utc).isoformat()}
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "HALT")
            with patch.dict(SNAPSHOT, {"/api/status": halted}), patch.dict(operator.HEALTH, {"halt_path": missing}):
                payload = self.get("/api/status")[1]
        self.assertEqual(payload["gating"], "awaiting_worker")
        self.assertFalse(payload["execution_permitted"])
        self.assertNotEqual(payload["reason"], "halt")

    def test_a_missing_halt_path_cannot_clear_a_halted_snapshot(self):
        halted = {"mode": "observe", "halt": True, "reason": "halt", "risk_permitted": False,
                  "execution_permitted": False, "published_at": datetime.now(timezone.utc).isoformat()}
        with patch.dict(SNAPSHOT, {"/api/status": halted}), patch.dict(operator.HEALTH, {}, clear=True):
            self.assertTrue(self.get("/api/status")[1]["halt"])


class NoSecretInPayloadsTests(AuthedCase):
    """Credentials given to the worker must not appear in anything the dashboard can read."""

    def test_keys_tokens_and_secrets_are_absent_from_every_published_route(self):
        sentinels = {"api_key": "K-" + secrets.token_hex(12), "api_secret": "S-" + secrets.token_hex(12)}
        with tempfile.TemporaryDirectory() as directory:
            cfg = Settings(mode="observe", db_path=os.path.join(directory, "p5.db"), profile_code="p-qa", **sentinels)
            journal = OrderJournal(cfg.db_path)
            try:
                engine = Engine(cfg, None, None, journal, None)
                engine.refresh_readonly()
                with patch.dict(SNAPSHOT, {}, clear=True):
                    publish(cfg, journal, engine)
                    everything = json.dumps(dict(SNAPSHOT), default=str)
                    for path in operator.ROUTES:
                        everything += json.dumps(self.get(path)[1], default=str)
            finally:
                journal.close()
        for name, value in {**sentinels, "operator_token": self.token}.items():
            self.assertNotIn(value, everything, name)
        for marker in ("Bearer ", "WARP_OPERATOR_TOKEN", "WARP_SESSION_SECRET", "api_secret"):
            self.assertNotIn(marker, everything, marker)


# ───────────────────────── Part B: the read-only boundary is pinned ─────────────────────────

POSTS = {
    "/api/login", "/api/logout", "/api/pause", "/api/resume", "/api/rules", "/api/accounts/budget",
    "/api/accounts/select", "/api/screener/filters", "/api/bots/template", "/api/bots/preview",
    "/api/paper/roundtrip", "/api/warp-bots/action",
}

# Every call site in threecommas.py that is not a GET. Pinned from main at 91d3c38 (P4C).
BROKER_WRITES = [
    ("DELETE", "/open_api/dca_bots/{}/delete"),
    ("DELETE", "/open_api/grid_bots/{}/delete"),
    ("POST", "/open_api/dca_bots/{}/close-all"),
    ("POST", "/open_api/dca_bots/create"),
    ("POST", "/open_api/dca_bots/create"),
    ("POST", "/open_api/dca_bots/{}/switch-pause"),
    ("POST", "/open_api/dca_bots/{}/switch-pause"),
    ("POST", "/open_api/grid_bots/create"),
    ("POST", "/open_api/grid_bots/{}/restart"),
    ("POST", "/open_api/grid_bots/{}/stop"),
    ("POST", "/open_api/strategies/trade"),
]

API_LITERAL = re.compile(r'"(/api/[A-Za-z0-9/_-]+)"')
GET_ROUTES = set(operator.ROUTES) | {"/api/session", "/api/stream", "/api/rules"}


def _text(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
    return None


class ReadOnlyBoundaryTests(unittest.TestCase):
    def test_the_worker_exposes_exactly_the_documented_state_changing_routes(self):
        source = (ROOT / "src/warp3c/httpapi.py").read_text()
        found = set(re.findall(r'method == "POST" and clean == "(/[^"]+)"', source))
        self.assertEqual(found, POSTS)
        for verb in ("PUT", "PATCH", "DELETE"):
            self.assertNotIn(f'method == "{verb}"', source)
        handler = (ROOT / "src/warp3c/__main__.py").read_text()
        defined = set(re.findall(r"def (do_[A-Z]+)\(", handler))
        self.assertEqual(defined, {"do_GET", "do_POST"})

    def test_every_post_route_is_behind_authentication_except_login_and_logout(self):
        for path in sorted(POSTS - {"/api/login", "/api/logout"}):
            with self.subTest(path):
                self.assertEqual(dispatch("POST", path, {}, b"{}")[0], 401)

    def test_other_verbs_never_reach_a_handler(self):
        token = secrets.token_urlsafe(16)
        with patch.dict(os.environ, {"WARP_OPERATOR_TOKEN": token}):
            for verb in ("PUT", "PATCH", "DELETE"):
                for path in sorted(POSTS - {"/api/login", "/api/logout"}):
                    with self.subTest(verb=verb, path=path):
                        status = dispatch(verb, path, {"Authorization": "Bearer " + token}, b"{}")[0]
                        self.assertGreaterEqual(status, 400)

    def test_the_broker_client_has_no_write_call_beyond_the_pinned_set(self):
        tree = ast.parse((ROOT / "src/warp3c/threecommas.py").read_text())
        found = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
                continue
            if node.func.attr not in {"request", "_bot_write"}:
                continue
            method = _text(node.args[0])
            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                path = _text(node.args[1])
                self.assertIsNotNone(path, f"line {node.lineno}: a write with a computed path")
                found.append((method, path))
        self.assertEqual(sorted(found), sorted(BROKER_WRITES))

    def test_no_other_module_opens_a_broker_connection_or_writes_over_http(self):
        for path in sorted((ROOT / "src/warp3c").glob("*.py")):
            if path.name in {"threecommas.py", "config.py"}:
                continue
            text = path.read_text()
            self.assertNotRegex(text, r"\.(post|put|patch|delete)\(\s*[\"']https?://", path.name)
            self.assertNotIn("trade.3commas.io", text, path.name)

    def test_the_browser_only_writes_to_the_documented_routes(self):
        files = [path for path in (ROOT / "web/src").rglob("*") if path.suffix in {".ts", ".tsx"}]
        literals, methods, direct = set(), [], []
        for path in files:
            text = path.read_text()
            literals.update(API_LITERAL.findall(text))
            methods += re.findall(r'method:\s*"([A-Z]+)"', text)
            if path.name != "api.ts" and re.search(r"\bfetch\(", text):
                direct.append(path.name)
            for banned in ("XMLHttpRequest", "sendBeacon", "WebSocket"):
                self.assertNotIn(banned, text, f"{path.name} uses {banned}")
        self.assertLessEqual(literals, GET_ROUTES | POSTS)
        self.assertEqual(set(methods), {"POST"})
        self.assertEqual(len(methods), 11, "the number of browser write call sites changed")
        self.assertEqual(direct, [], "fetch() outside web/src/api.ts")

    def test_the_browser_links_only_to_the_documented_external_host(self):
        hosts = set()
        for path in (ROOT / "web/src").rglob("*"):
            if path.suffix in {".ts", ".tsx", ".css"}:
                hosts.update(re.findall(r"https?://([A-Za-z0-9.-]+)", path.read_text()))
        self.assertEqual(hosts, {"www.tradingview.com"})

    def test_non_warp_bot_names_cannot_become_owned(self):
        from warp3c.ownership import NotOwned, require_owned

        for name in ("VORTEQ-USDC-STOCKS", "AEGIS", "BOT-NAME-1", "warp-abcd1234", "WARP-", "WARP-zzzz1234"):
            with self.subTest(name), self.assertRaises(NotOwned):
                require_owned({"name": name, "warp_id": "abcd1234"}, None)
        with self.assertRaises(NotOwned):
            require_owned({"name": "WARP-abcd1234", "warp_id": "abcd1234"}, {"name": "VORTEQ-USDC-STOCKS"})


# ───────────────────────────── Part C: known gaps as expected failures ─────────────────────────────


class KnownGapTests(AuthedCase):
    """These assert the safe behaviour. They fail today, so they are expected failures.

    When the product gains the control, unittest reports an unexpected success. Remove the marker then.
    """

    def _owned_bot(self, journal):
        stamp = datetime.now(timezone.utc).isoformat()
        journal.insert_warp_bot({
            "warp_id": "abcd1234", "name": "WARP-abcd1234", "client_id": None, "external_code": "owned", "kind": "dca",
            "product": "dca_bot", "account_code": "demo1", "market": "spot", "symbol": "BTCUSDT", "status": "confirmed",
            "commitment": "15", "created_at": stamp, "updated_at": stamp,
        })

    def test_P0_warp_bot_action_is_refused_by_the_server_outside_paper_mode(self):
        """P0. POST /api/warp-bots/action has no server-side mode or account gate (httpapi.py _bot_action).

        Only the UI limits it to paper mode on a demo spot account. In OBSERVE a signed-in caller can still reach
        the broker for a journaled WARP bot. Release blocker for any PAPER or LIVE enablement.
        """
        class Recording:
            def __init__(self):
                self.writes = []

            def get_dca_bot(self, code):
                return {"code": code, "name": "WARP-abcd1234", "status": "active", "active": True}

            def pause_dca_bot(self, code):
                self.writes.append(("pause", code))
                return {}

        with tempfile.TemporaryDirectory() as directory:
            cfg = Settings(mode="observe", db_path=os.path.join(directory, "p5.db"))
            journal = OrderJournal(cfg.db_path)
            broker = Recording()
            engine = Engine(cfg, broker, None, journal, None)
            self._owned_bot(journal)
            try:
                with patch.dict(RUNTIME, {"journal": journal, "cfg": cfg, "engine": engine}), \
                        patch("warp3c.httpapi.publish", lambda *_a, **_k: None):
                    status, _body = self.post("/api/warp-bots/action",
                                              {"warp_id": "abcd1234", "action": "pause", "confirm": "PAUSE"})
            finally:
                journal.close()
        self.assertEqual(broker.writes, [], "an OBSERVE-mode request reached a broker write")
        self.assertEqual(status, 403)
        self.assertEqual(_body["error"], "paper_mode_required")

    def test_logout_revokes_the_session_token_server_side(self):
        """The session cookie is a signed token valid for 24 hours. POST /api/logout now revokes the presented
        token server-side, so a copied token stops working at once instead of living until it expires."""
        from warp3c.auth import issue_session

        with tempfile.TemporaryDirectory() as data_dir, patch.dict(os.environ, {
                "WARP_SESSION_SECRET": secrets.token_urlsafe(32),
                "WARP_DB_PATH": os.path.join(data_dir, "warp.db")}):
            token = issue_session()
            headers = {"Cookie": f"warp_session={token}"}
            self.assertEqual(dispatch("GET", "/api/session", headers, b"")[0], 200)
            self.assertTrue(json.loads(dispatch("GET", "/api/session", headers, b"")[2])["authenticated"])
            dispatch("POST", "/api/logout", headers, b"")
            after = json.loads(dispatch("GET", "/api/session", headers, b"")[2])
            self.assertFalse(after["authenticated"])


# ───────────────────────────── Part D: release documents ─────────────────────────────

DOCS = ROOT / "docs/dashboard"
P5_DOCS = ["ROLLBACK.md", "RELEASE_CANDIDATE_REPORT.md", "TRADINGDESK_P5.md", "TRADINGDESK_2_QA.md", "DESIGN_SYSTEM.md"]
BASE_SHA = "91d3c38f7581d6bf3954d01c0f9f895b5d0e8c75"


class ReleaseDocumentTests(unittest.TestCase):
    def read(self, name):
        return (DOCS / name).read_text()

    def test_rollback_has_no_command_that_removes_a_volume_machine_or_app(self):
        text = self.read("ROLLBACK.md")
        self.assertNotRegex(text, r"(?i)\b(fly|flyctl)\s+(volumes?|vol)\s+(destroy|delete|remove|rm|extend|fork)\b")
        self.assertNotRegex(text, r"(?i)\b(fly|flyctl)\s+(machines?|apps?)\s+(destroy|delete|remove|rm|kill)\b")
        self.assertNotRegex(text, r"(?i)\b(fly|flyctl)\s+scale\s+count\b")
        self.assertIn("vol_42k13q9n9g9jo294", text)
        self.assertIn("vs_40kv0xollvAtz5lVmV5", text)

    def test_documents_keep_the_live_acknowledgement_value_out_and_no_external_chat_links(self):
        for path in sorted(DOCS.rglob("*.md")) + sorted((ROOT / "web/qa").glob("*.py")) + sorted(ROOT.glob("scripts/qa/*")):
            text = path.read_text()
            self.assertNotIn("chatgpt.com", text, path.name)
            self.assertNotRegex(text, r"WARP_LIVE_EXECUTION_ACK\s*[=:]\s*[\"']?\w", path.name)

    def test_the_report_names_the_base_the_blocker_and_the_approval_boundary(self):
        text = self.read("RELEASE_CANDIDATE_REPORT.md")
        self.assertIn(BASE_SHA, text)
        self.assertIn("Owner approval required: release candidate approval is separate from merge/deploy.", text)
        self.assertIn("/api/warp-bots/action", text)
        self.assertRegex(text, r"(?s)P0.*blocker for any PAPER or LIVE")
        for ident in ("AC-01", "AC-14", "NON-GO"):
            self.assertIn(ident, text)
        for gap in ("F24-4", "F24-5", "P4B P2", "F26-3", "F26-4", "+24.78 kB"):
            self.assertIn(gap, text, gap)

    def test_relative_links_and_evidence_files_resolve(self):
        link = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
        for name in P5_DOCS:
            for target in link.findall(self.read(name)):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                self.assertTrue((DOCS / target).resolve().exists(), f"{name} links to missing {target}")

    def test_p5_screenshots_are_webp_only(self):
        files = sorted((DOCS / "screenshots/p5").iterdir())
        self.assertTrue(files)
        for path in files:
            self.assertEqual(path.suffix, ".webp", path.name)
            self.assertEqual(path.read_bytes()[:4], b"RIFF", path.name)
            self.assertEqual(path.read_bytes()[8:12], b"WEBP", path.name)


if __name__ == "__main__":
    unittest.main()
