"""Account selection, spot-only refusal, ownership, and the forward journal."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from test_failures import ExplodingBroker, Markets, Research

from warp3c.accounts import (
    LIVE_ACK,
    BrokerAccount,
    UnavailableAccount,
    account_allowed,
    budget_decision,
    budget_for,
)
from warp3c.bots import BotTemplate
from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.forward import evaluate_due, is_hit, summary
from warp3c.httpapi import dispatch
from warp3c.operator import RUNTIME, _sleeve_view
from warp3c.ownership import NotOwned, apply_action
from warp3c.papercheck import run_roundtrip
from warp3c.state import OrderJournal
from warp3c.threecommas import IntegrationError, ThreeCommas


def envelope(items, *, page=1, total=None, pages=1, limit=100):
    count = len(items) if total is None else total
    return {"items": items, "pagination": {"page": page, "limit": limit, "total": count, "pages": pages}}


def profile(code, *, name, demo, exchange_code, exchange_name, asset="USDT", margin=None):
    row = {
        "code": code,
        "name": name,
        "status": "active",
        "demo": demo,
        "exchange": {"code": exchange_code, "name": exchange_name},
        "balance": {"assets": {asset: {"free": "100", "used": "0", "total": "100"}}},
    }
    if margin is not None:
        row["marginMode"] = margin
    return row


def account(**kwargs):
    base = dict(
        code="demo1",
        name="CUANRAKYATV3",
        demo=True,
        status="active",
        exchange_code="BINANCE_FUTURES",
        exchange_name="BINANCE-FUTURES (USDT-M)",
        market="futures",
        quote="USDT",
        free=Decimal("10000"),
        used=Decimal("0"),
        total=Decimal("10000"),
    )
    base.update(kwargs)
    return BrokerAccount(**base)


class AccountTests(unittest.TestCase):
    def test_profiles_map_demo_market_and_quote(self):
        rows = [
            profile("spot1", name="CuanRakyat", demo=False, exchange_code="BINANCE", exchange_name="BINANCE-SPOT", asset="USDC"),
            profile("fut1", name="CUANRAKYATV3", demo=True, exchange_code="BINANCE_FUTURES", exchange_name="BINANCE-FUTURES (USDT-M)"),
            profile("real1", name="CUAN.RAKYAT16K", demo=False, exchange_code="BINANCE", exchange_name="BINANCE-SPOT"),
        ]

        def handler(req):
            if req.url.path.endswith("api_profiles"):
                return httpx.Response(200, json=envelope(rows))
            raise AssertionError(req.url.path)

        api = ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))
        found = {row.code: row for row in api.list_accounts()}
        self.assertFalse(found["spot1"].demo)
        self.assertEqual(found["spot1"].quote, "USDC")
        self.assertEqual(found["spot1"].market, "spot")
        self.assertTrue(found["fut1"].demo)
        self.assertEqual(found["fut1"].market, "futures")
        self.assertEqual(found["fut1"].quote, "USDT")
        self.assertEqual(found["fut1"].badge if False else found["fut1"].to_public(selectable=True, reason="ok")["badge"], "DEMO")
        self.assertEqual(found["real1"].to_public(selectable=False, reason="paper_requires_demo")["badge"], "REAL")
        with self.assertRaises(IntegrationError) as refused_profile:
            api.get_account("fut1", "BINANCE_FUTURES")
        self.assertIn("spot only", str(refused_profile.exception).lower())
        with self.assertRaises(IntegrationError) as refused_market:
            api.get_account("fut1", "BINANCE_FUTURES", market="futures")
        self.assertIn("spot only", str(refused_market.exception).lower())
        self.assertEqual(account_allowed(found["fut1"], mode="paper", live_ack="")[1], "spot_only")
        api.close()

    def test_selection_keeps_the_live_ack_gate(self):
        demo = account()
        real = account(code="real1", name="CUAN.RAKYAT16K", demo=False, market="spot", exchange_code="BINANCE", exchange_name="BINANCE-SPOT")
        self.assertEqual(account_allowed(demo, mode="paper", live_ack=""), (False, "spot_only"))
        self.assertEqual(account_allowed(real, mode="paper", live_ack=LIVE_ACK), (False, "paper_requires_demo"))
        self.assertEqual(account_allowed(real, mode="observe", live_ack=LIVE_ACK), (False, "observe_refuses_real"))
        self.assertEqual(account_allowed(real, mode="live", live_ack="nope"), (False, "live_ack_required"))
        self.assertEqual(account_allowed(real, mode="live", live_ack=LIVE_ACK)[0], True)
        spot_demo = account(market="spot", exchange_code="BINANCE", exchange_name="BINANCE-SPOT")
        self.assertEqual(account_allowed(spot_demo, mode="live", live_ack=LIVE_ACK), (False, "live_refuses_demo"))
        spot = account(market="spot", exchange_code="BINANCE", exchange_name="BINANCE-SPOT")
        self.assertEqual(account_allowed(spot, mode="paper", live_ack="")[0], True)
        usdc = account(quote="USDC", market="spot", exchange_code="BINANCE")
        self.assertEqual(account_allowed(usdc, mode="paper", live_ack="")[1], "quote_not_usdt")
        with self.assertRaises(ValueError):
            BotTemplate.from_mapping({"market": "futures"})
        with self.assertRaises(ValueError):
            BotTemplate.from_mapping({"leverage": 3})

    def test_select_refuses_a_real_account_over_http(self):
        os.environ["WARP_OPERATOR_TOKEN"] = "operator-token-16"
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(
                mode="paper", db_path=os.path.join(tmp, "warp.db"),
                api_key="k", api_secret="s", profile_code="p123",
            )
            journal = OrderJournal(cfg.db_path)
            engine = Engine(cfg, ExplodingBroker(), Research(), journal, Markets())
            engine.last_accounts = [account(code="real1", demo=False, market="spot", exchange_code="BINANCE")]
            RUNTIME["journal"] = journal
            RUNTIME["cfg"] = cfg
            RUNTIME["engine"] = engine
            try:
                denied = dispatch("POST", "/api/accounts/select", {}, b"{}")
                self.assertEqual(denied[0], 401)
                headers = {"Authorization": "Bearer operator-token-16"}
                body = json.dumps({"code": "real1"}).encode()
                refused = dispatch("POST", "/api/accounts/select", headers, body)
                self.assertEqual(refused[0], 403)
                self.assertIn("paper_requires_demo", refused[2].decode())
                self.assertIsNone(journal.get_setting("target_account"))
                engine.last_accounts = [account(market="spot", exchange_code="BINANCE", exchange_name="BINANCE-SPOT")]
                saved = dispatch("POST", "/api/accounts/select", headers, json.dumps({"code": "demo1"}).encode())
                self.assertEqual(saved[0], 200)
                self.assertIn(b'"places_orders":false', saved[2])
                self.assertIn("demo1", journal.get_setting("target_account"))
            finally:
                RUNTIME["journal"] = None
                RUNTIME["cfg"] = None
                RUNTIME["engine"] = None
                journal.close()

    def test_mixed_profiles_keep_valid_balances_and_flag_unavailable(self):
        broken = {
            "code": "cuannone",
            "name": "CuanRakyat",
            "status": "error",
            "demo": False,
            "exchange": {"code": "BINANCE", "name": "BINANCE-SPOT"},
            "balance": {"assets": None},
        }
        skipped = {
            "code": "nodemo1",
            "name": "NoDemoFlag",
            "status": "active",
            "exchange": {"code": "BINANCE", "name": "BINANCE-SPOT"},
            "balance": {"assets": {"USDT": {"free": "50", "used": "0", "total": "50"}}},
        }
        rows = [
            profile("demo1xxx", name="DemoSpot", demo=True, exchange_code="BINANCE", exchange_name="BINANCE-SPOT"),
            broken,
            profile("real1xxx", name="RealSpot", demo=False, exchange_code="BINANCE", exchange_name="BINANCE-SPOT"),
            skipped,
        ]

        def handler(req):
            if req.url.path.endswith("api_profiles"):
                return httpx.Response(200, json=envelope(rows))
            raise AssertionError(req.url.path)

        api = ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))
        listed = api.list_accounts()
        found = {row.code: row for row in listed}
        self.assertIsNone(listed.error)
        self.assertEqual(set(found), {"demo1xxx", "cuannone", "real1xxx"})
        self.assertNotIn("nodemo1", found)
        self.assertEqual(found["demo1xxx"].total, Decimal("100"))
        self.assertEqual(found["demo1xxx"].free, Decimal("100"))
        self.assertEqual(found["real1xxx"].total, Decimal("100"))
        self.assertIsInstance(found["cuannone"], UnavailableAccount)
        self.assertEqual(found["cuannone"].error, "balance unavailable")
        self.assertEqual(found["cuannone"].name, "CuanRakyat")
        self.assertFalse(found["cuannone"].demo)
        self.assertEqual(found["cuannone"].status, "error")
        self.assertEqual(found["cuannone"].exchange_code, "BINANCE")
        self.assertEqual(found["cuannone"].market, "spot")
        public = found["cuannone"].to_public(selectable=True, reason="ok")
        self.assertIsNone(public["total"])
        self.assertIsNone(public["free"])
        self.assertIsNone(public["used"])
        self.assertIsNone(public["quote"])
        self.assertEqual(public["assets"], [])
        self.assertFalse(public["selectable"])
        self.assertEqual(public["error"], "balance unavailable")
        self.assertEqual(account_allowed(found["cuannone"], mode="paper", live_ack="")[0], False)
        self.assertEqual(account_allowed(found["cuannone"], mode="live", live_ack=LIVE_ACK)[0], False)
        public_rows = [
            row.to_public(
                selectable=False,
                reason=getattr(row, "error", None) or "ok",
            )
            for row in listed
        ]
        sleeve = _sleeve_view(
            Settings(sleeve_pct=Decimal("1")),
            public_rows,
            {"code": "cuannone"},
        )
        self.assertIsNone(sleeve["eligible"])
        self.assertIsNone(sleeve["sleeve"])
        self.assertEqual(sleeve["reason"], "balance unavailable")
        api.close()

    def test_select_refuses_an_unavailable_account(self):
        os.environ["WARP_OPERATOR_TOKEN"] = "operator-token-16"
        unavailable = UnavailableAccount(
            "cuannone",
            "CuanRakyat",
            False,
            "error",
            "BINANCE",
            "BINANCE-SPOT",
            "spot",
            "balance unavailable",
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(
                mode="paper", db_path=os.path.join(tmp, "warp.db"),
                api_key="k", api_secret="s", profile_code="p123",
            )
            journal = OrderJournal(cfg.db_path)
            engine = Engine(cfg, ExplodingBroker(), Research(), journal, Markets())
            engine.last_accounts = [
                unavailable,
                account(code="demo1xxx", market="spot", exchange_code="BINANCE", exchange_name="BINANCE-SPOT"),
            ]
            RUNTIME["journal"] = journal
            RUNTIME["cfg"] = cfg
            RUNTIME["engine"] = engine
            try:
                headers = {"Authorization": "Bearer operator-token-16"}
                refused = dispatch(
                    "POST", "/api/accounts/select", headers, json.dumps({"code": "cuannone"}).encode()
                )
                self.assertEqual(refused[0], 403)
                self.assertIn("balance unavailable", refused[2].decode())
                self.assertIsNone(journal.get_setting("target_account"))
            finally:
                RUNTIME["journal"] = None
                RUNTIME["cfg"] = None
                RUNTIME["engine"] = None
                journal.close()

    def test_all_unparseable_profiles_set_list_error(self):
        rows = [
            {
                "code": "cuannone",
                "name": "CuanRakyat",
                "status": "error",
                "demo": False,
                "exchange": {"code": "BINANCE", "name": "BINANCE-SPOT"},
                "balance": {"assets": None},
            },
            {
                "code": "nodemo1x",
                "name": "NoDemoFlag",
                "status": "active",
                "exchange": {"code": "BINANCE", "name": "BINANCE-SPOT"},
                "balance": {"assets": {"USDT": {"free": "50", "used": "0", "total": "50"}}},
            },
        ]

        def handler(req):
            if req.url.path.endswith("api_profiles"):
                return httpx.Response(200, json=envelope(rows))
            raise AssertionError(req.url.path)

        api = ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))
        listed = api.list_accounts()
        self.assertEqual(listed.error, "Account balance unavailable")
        self.assertEqual(len(listed), 1)
        self.assertIsInstance(listed[0], UnavailableAccount)
        self.assertEqual(listed[0].error, "balance unavailable")
        api.close()


class OwnershipTests(unittest.TestCase):
    def _bot(self, journal, *, name="WARP-abcd1234", code="owned", product="dca_bot"):
        stamp = datetime.now(timezone.utc).isoformat()
        journal.insert_warp_bot(
            {
                "warp_id": "abcd1234",
                "name": name,
                "client_id": None,
                "external_code": code,
                "kind": "dca",
                "product": product,
                "account_code": "demo1",
                "market": "spot",
                "symbol": "BTCUSDT",
                "status": "confirmed",
                "commitment": "15",
                "created_at": stamp,
                "updated_at": stamp,
            }
        )

    def test_foreign_bots_are_refused_before_a_write(self):
        class Broker:
            def __init__(self):
                self.writes = []

            def get_dca_bot(self, code):
                return {"code": code, "name": "VORTEQ-USDC-STOCKS"}

            def pause_dca_bot(self, code):
                self.writes.append(code)

            def delete_dca_bot(self, code):
                self.writes.append(("delete", code))

        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            broker = Broker()
            with self.assertRaises(NotOwned):
                apply_action(broker, journal, "foreign1", "delete")
            self.assertEqual(broker.writes, [])
            self._bot(journal)
            with self.assertRaises(NotOwned) as caught:
                apply_action(broker, journal, "abcd1234", "pause")
            self.assertEqual(caught.exception.reason, "foreign_bot")
            self.assertEqual(broker.writes, [])

            class Active(Broker):
                def get_dca_bot(self, code):
                    return {"code": code, "name": "WARP-abcd1234", "active": True, "status": "active"}

            active = Active()
            apply_action(active, journal, "abcd1234", "delete")
            self.assertEqual(active.writes, ["owned", ("delete", "owned")])
            journal.close()

    def test_owned_pause_uses_the_cabinet_route(self):
        seen = []

        def handler(req):
            seen.append((req.method, req.url.path, req.content.decode()))
            if req.method == "GET":
                return httpx.Response(200, json={"code": "owned", "name": "WARP-abcd1234", "status": "active"})
            return httpx.Response(200, json={})

        api = ThreeCommas("https://trade.3commas.io", "key", "secret", transport=httpx.MockTransport(handler))
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            self._bot(journal)
            result = apply_action(api, journal, "abcd1234", "pause")
            self.assertEqual(result["status"], "paused")
            self.assertEqual(seen[0][0], "GET")
            self.assertTrue(seen[1][0] == "POST" and seen[1][1].endswith("/switch-pause"))
            self.assertIn("stop_only", seen[1][2])
            self.assertNotIn("secret", seen[1][2])
            journal.close()
        api.close()


class BudgetTests(unittest.TestCase):
    def test_remaining_budget_and_bot_cap_block_a_new_bot(self):
        bots = [
            {"status": "confirmed", "commitment": "40"},
            {"status": "paused", "commitment": "40"},
            {"status": "deleted", "commitment": "1000"},
        ]
        room = budget_for(
            balance=Decimal("1000"),
            absolute=Decimal("100"),
            fraction=Decimal("0.10"),
            max_bots=2,
            bots=bots,
        )
        self.assertEqual(room.cap, Decimal("100"))
        self.assertEqual(room.used, Decimal("80"))
        self.assertEqual(budget_decision(room, Decimal("15")), (False, "max_bots"))
        wider = budget_for(
            balance=Decimal("1000"),
            absolute=Decimal("100"),
            fraction=Decimal("0.10"),
            max_bots=4,
            bots=bots[:1],
        )
        self.assertEqual(budget_decision(wider, Decimal("70")), (False, "budget"))
        self.assertEqual(budget_decision(wider, Decimal("15"))[0], True)

    def test_default_budget_stops_the_worker_before_a_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(
                mode="paper", db_path=os.path.join(tmp, "warp.db"),
                api_key="k", api_secret="s", profile_code="p123",
                sleeve_pct=Decimal("0.10"),
            )
            journal = OrderJournal(cfg.db_path)
            broker = ExplodingBroker()
            Engine(cfg, broker, Research(), journal, Markets()).cycle()
            self.assertEqual(broker.creates, 0)
            reasons = [row["reason"] for row in journal.recent_decisions()]
            self.assertTrue(any(reason in {"no_feasible_ladder", "no_capital", "score"} for reason in reasons))
            journal.close()


class ForwardTests(unittest.TestCase):
    def test_horizons_score_hit_rate_without_using_the_model_price(self):
        self.assertTrue(is_hit("BUY", Decimal("0.01")))
        self.assertTrue(is_hit("SKIP", Decimal("-0.02")))
        self.assertTrue(is_hit("WAIT", Decimal("0.004")))
        self.assertFalse(is_hit("WAIT", Decimal("0.02")))
        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            start = datetime.now(timezone.utc) - timedelta(hours=80)
            journal.record_verdict("BTCUSDT", "BUY", Decimal("0.8"), Decimal("100"), start)
            journal.record_verdict("ETHUSDT", "SKIP", Decimal("0.4"), Decimal("50"), start)

            def price_at(symbol, moment):
                return Decimal("110") if symbol == "BTCUSDT" else Decimal("40")

            filled = evaluate_due(journal, price_at, datetime.now(timezone.utc))
            self.assertEqual(filled, 6)
            report = summary(journal)
            buy = report["by_verdict"]["BUY"]["4h"]
            skip = report["by_verdict"]["SKIP"]["24h"]
            self.assertEqual(buy["hits"], 1)
            self.assertEqual(buy["n"], 1)
            self.assertEqual(skip["hits"], 1)
            self.assertTrue(Decimal(buy["avg_move"]) > 0)
            self.assertTrue(Decimal(skip["avg_move"]) < 0)
            journal.close()


class PaperCheckTests(unittest.TestCase):
    def test_round_trip_stays_on_a_demo_account(self):
        class Broker:
            def __init__(self, rows):
                self.rows = rows
                self.writes = []
                self.name = None

            def list_accounts(self):
                return self.rows

            def post_dca_bot(self, payload):
                self.writes.append("create")
                self.name = payload["name"]
                self.assert_safe(payload)
                return {}

            def assert_safe(self, payload):
                if payload["dcaTradingType"] != "long" or not payload.get("stopLoss"):
                    raise AssertionError(payload)
                if not str(payload["name"]).startswith("WARP-"):
                    raise AssertionError(payload)

            def list_dca_bots(self):
                return [{"code": "bot1", "name": self.name}]

            def get_dca_bot(self, code):
                return {"code": code, "name": self.name}

            def pause_dca_bot(self, code):
                self.writes.append("pause")

            def delete_dca_bot(self, code):
                self.writes.append("delete")

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(
                mode="paper", db_path=os.path.join(tmp, "warp.db"),
                api_key="super-secret-key", api_secret="s", profile_code="demo1",
                sleeve_pct=Decimal("1"),
            )
            journal = OrderJournal(cfg.db_path)
            real = Broker([account(demo=False, market="spot", exchange_code="BINANCE", exchange_name="BINANCE-SPOT")])
            refused = run_roundtrip(cfg, real, journal)
            self.assertFalse(refused["ok"])
            self.assertFalse(refused["places_orders"])
            self.assertEqual(real.writes, [])
            self.assertNotIn("super-secret-key", json.dumps(refused))
            demo = Broker([account(market="spot", exchange_code="BINANCE", exchange_name="BINANCE-SPOT")])
            report = run_roundtrip(cfg, demo, journal)
            self.assertTrue(report["ok"], report)
            self.assertEqual(demo.writes, ["create", "pause", "delete"])
            halted = Settings(
                mode="paper", db_path=cfg.db_path, api_key="k", api_secret="s", profile_code="demo1",
            )
            open(os.path.join(tmp, "HALT"), "w").close()
            blocked = run_roundtrip(halted, demo, journal)
            self.assertEqual(blocked["steps"][-1]["detail"], "halt")
            self.assertFalse(blocked["places_orders"])
            journal.close()

    def test_observe_mode_does_not_call_the_broker(self):
        class Broker:
            def list_accounts(self):
                raise AssertionError("observe must not list for a write")

        cfg = Settings(db_path="/tmp/does-not-matter.db")
        report = run_roundtrip(cfg, Broker(), None)
        self.assertFalse(report["ok"])
        self.assertEqual(report["steps"][0]["detail"], "paper_mode_required")


if __name__ == "__main__":
    unittest.main()
