"""Server-side gate on POST /api/warp-bots/action. Every refusal must happen before any broker bot read or write."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from warp3c.accounts import BrokerAccount, ListedAccounts, UnavailableAccount
from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.httpapi import dispatch
from warp3c.operator import RUNTIME
from warp3c.state import OrderJournal
from warp3c.threecommas import IntegrationError

ACTIONS = {"pause": "PAUSE", "resume": "RESUME", "close": "CLOSE", "delete": "DELETE"}


def account(code="demo1", *, demo=True, market="spot", status="active"):
    return BrokerAccount(
        code, "acct", demo, status, "BINANCE", "Binance", market, "USDT",
        Decimal("100"), Decimal("0"), Decimal("100"),
    )


class Recording:
    """Records every call. get_* and writes are separate so a refusal can be shown to have made neither."""

    def __init__(self, accounts=None, list_error=None, remote_name="WARP-abcd1234"):
        self.accounts = [account()] if accounts is None else accounts
        self.list_error = list_error
        self.remote_name = remote_name
        self.account_reads = 0
        self.reads = []
        self.writes = []

    def list_accounts(self):
        self.account_reads += 1
        if self.list_error:
            raise IntegrationError(self.list_error)
        return ListedAccounts(self.accounts)

    def _get(self, kind, code):
        self.reads.append((kind, code))
        return {"code": code, "name": self.remote_name, "status": "active", "active": True}

    def get_dca_bot(self, code):
        return self._get("dca", code)

    def get_grid_bot(self, code):
        return self._get("grid", code)

    def _write(self, name, code, **kw):
        self.writes.append((name, code, kw) if kw else (name, code))
        return {}

    def pause_dca_bot(self, code):
        return self._write("pause_dca", code)

    def resume_dca_bot(self, code):
        return self._write("resume_dca", code)

    def close_dca_positions(self, code):
        return self._write("close_dca", code)

    def delete_dca_bot(self, code):
        return self._write("delete_dca", code)

    def stop_grid_bot(self, code, close=False):
        return self._write("stop_grid", code, close=close)

    def restart_grid_bot(self, code):
        return self._write("restart_grid", code)

    def delete_grid_bot(self, code):
        return self._write("delete_grid", code)


class GateCase(unittest.TestCase):
    def setUp(self):
        token = secrets.token_urlsafe(24)
        patcher = patch.dict(os.environ, {"WARP_OPERATOR_TOKEN": token})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.headers = {"Authorization": "Bearer " + token}

    def setting(self, mode="paper"):
        cfg = Settings(
            mode="paper", db_path=os.path.join(self.tmp, "gate.db"),
            api_key="k", api_secret="s", profile_code="demo1",
        )
        object.__setattr__(cfg, "mode", mode)
        return cfg

    def bot(self, journal, *, product="dca_bot", status="confirmed", account_code="demo1", external="owned",
            name="WARP-abcd1234"):
        stamp = datetime.now(timezone.utc).isoformat()
        journal.insert_warp_bot({
            "warp_id": "abcd1234", "name": name, "client_id": None, "external_code": external,
            "kind": "dca" if product == "dca_bot" else "grid", "product": product, "account_code": account_code,
            "market": "spot", "symbol": "BTCUSDT", "status": status, "commitment": "15",
            "created_at": stamp, "updated_at": stamp,
        })

    def call(self, broker, *, mode="paper", action="pause", confirm=None, warp_id="abcd1234", seed=True,
             halt=False, **bot_kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        cfg = self.setting(mode)
        journal = OrderJournal(cfg.db_path)
        self.addCleanup(journal.close)
        engine = Engine(cfg, broker, None, journal, None)
        if seed:
            self.bot(journal, **bot_kw)
        if halt:
            open(os.path.join(self.tmp, "HALT"), "w").close()
        payload = {"warp_id": warp_id, "action": action, "confirm": ACTIONS[action] if confirm is None else confirm}
        with patch.dict(RUNTIME, {"journal": journal, "cfg": cfg, "engine": engine}), \
                patch("warp3c.httpapi.publish", lambda *_a, **_k: None):
            status, _headers, body = dispatch("POST", "/api/warp-bots/action", self.headers, json.dumps(payload).encode())
        return status, json.loads(body), journal

    def assertRefused(self, broker, status, body, code, error):
        self.assertEqual((status, body.get("error")), (code, error), body)
        self.assertIs(body.get("places_orders"), False)
        self.assertEqual(broker.writes, [])
        self.assertEqual(broker.reads, [])


class ModeGateTests(GateCase):
    def test_non_paper_modes_are_refused_for_every_action_with_no_bot_call(self):
        for mode in ("observe", "live", "bogus", ""):
            for action in ACTIONS:
                with self.subTest(mode=mode, action=action):
                    broker = Recording()
                    status, body, _ = self.call(broker, mode=mode, action=action)
                    self.assertRefused(broker, status, body, 403, "paper_mode_required")
                    self.assertEqual(body["mode"], mode)
                    self.assertEqual(broker.account_reads, 0)

    def test_mode_gate_runs_before_ownership_for_an_unknown_bot(self):
        broker = Recording()
        status, body, _ = self.call(broker, mode="observe", seed=False, warp_id="deadbeef")
        self.assertRefused(broker, status, body, 403, "paper_mode_required")


class AccountGateTests(GateCase):
    def test_refused_accounts(self):
        cases = {
            "real": (Recording([account(demo=False)]), "demo_account_required"),
            "futures": (Recording([account(market="futures")]), "spot_only"),
            "inactive": (Recording([account(status="paused")]), "inactive"),
            "missing": (Recording([account("other")]), "account_not_found"),
            "empty": (Recording([]), "account_not_found"),
            "duplicate": (Recording([account(), account()]), "account_not_found"),
            "list_error": (Recording(list_error="3Commas GET /accounts failed"), "account_unavailable"),
            "incomplete": (Recording([UnavailableAccount(
                "demo1", "acct", True, "active", "BINANCE", "Binance", "spot", "Account balance unavailable")]),
                "account_unavailable"),
        }
        for name, (broker, error) in cases.items():
            for action in ACTIONS:
                with self.subTest(case=name, action=action):
                    broker.reads.clear()
                    status, body, _ = self.call(broker, action=action)
                    self.assertRefused(broker, status, body, 403, error)

    def test_every_list_row_failing_is_unavailable(self):
        broker = Recording()
        broker.list_accounts = lambda: ListedAccounts([account()], error="Account balance unavailable")
        status, body, _ = self.call(broker)
        self.assertRefused(broker, status, body, 403, "account_unavailable")

    def test_a_broker_without_account_listing_is_refused(self):
        class NoListing:
            reads: list = []
            writes: list = []

        broker = NoListing()
        status, body, _ = self.call(broker)
        self.assertRefused(broker, status, body, 403, "account_unavailable")


class BotGateTests(GateCase):
    def test_unknown_warp_id_is_not_owned(self):
        broker = Recording()
        status, body, _ = self.call(broker, seed=False, warp_id="deadbeef")
        self.assertRefused(broker, status, body, 403, "not_owned")

    def test_non_warp_journal_row_is_not_owned(self):
        for name in ("VORTEQ-USDC-STOCKS", "AEGIS", "BOT-NAME-1"):
            with self.subTest(name):
                broker = Recording()
                status, body, _ = self.call(broker, name=name)
                self.assertRefused(broker, status, body, 403, "not_owned")

    def test_unsupported_product_and_missing_external_code(self):
        broker = Recording()
        status, body, _ = self.call(broker, product="signal_bot")
        self.assertRefused(broker, status, body, 403, "unsupported_product")
        broker = Recording()
        status, body, _ = self.call(broker, external=None)
        self.assertRefused(broker, status, body, 403, "not_owned")

    def test_deleted_bot_is_refused_with_no_calls(self):
        for action in ACTIONS:
            with self.subTest(action):
                broker = Recording()
                status, body, _ = self.call(broker, action=action, status="deleted")
                self.assertRefused(broker, status, body, 409, "already_deleted")

    def test_foreign_remote_name_is_still_refused_before_any_write(self):
        broker = Recording(remote_name="VORTEQ-USDC-STOCKS")
        status, body, journal = self.call(broker)
        self.assertEqual((status, body["error"]), (403, "foreign_bot"))
        self.assertEqual(broker.writes, [])
        self.assertEqual(journal.warp_bot("abcd1234")["status"], "confirmed")


class PaperPathTests(GateCase):
    def test_wrong_confirm_word_is_still_400(self):
        for action in ACTIONS:
            with self.subTest(action):
                broker = Recording()
                status, body, _ = self.call(broker, action=action, confirm="NOPE")
                self.assertRefused(broker, status, body, 400, "confirmation_required")
        broker = Recording()
        status, body, _ = self.call(broker, mode="observe", confirm="NOPE")
        self.assertRefused(broker, status, body, 400, "confirmation_required")

    def test_halt_refuses_resume_without_a_write(self):
        broker = Recording()
        status, body, _ = self.call(broker, action="resume", halt=True, status="paused")
        self.assertEqual((status, body["error"], body["places_orders"]), (409, "halt", False))
        self.assertEqual(broker.writes, [])
        self.assertEqual(broker.reads, [])

    def test_halt_does_not_block_pause(self):
        broker = Recording()
        status, body, _ = self.call(broker, action="pause", halt=True)
        self.assertEqual(status, 200, body)
        self.assertEqual(broker.writes, [("pause_dca", "owned")])

    def test_positive_control_dca_each_action_writes_once(self):
        expected = {
            "pause": [("pause_dca", "owned")],
            "resume": [("resume_dca", "owned")],
            "close": [("close_dca", "owned")],
            "delete": [("pause_dca", "owned"), ("delete_dca", "owned")],
        }
        status_of = {"pause": "paused", "resume": "active", "close": "closed", "delete": "deleted"}
        for action, writes in expected.items():
            with self.subTest(action):
                broker = Recording()
                status, body, journal = self.call(broker, action=action)
                self.assertEqual(status, 200, body)
                self.assertEqual(body["status"], status_of[action])
                self.assertIs(body["places_orders"], False)
                self.assertEqual(broker.writes, writes)
                self.assertEqual(broker.reads, [("dca", "owned")])
                self.assertEqual(journal.warp_bot("abcd1234")["status"], status_of[action])

    def test_positive_control_grid_each_action_writes_once(self):
        expected = {
            "pause": [("stop_grid", "owned", {"close": False})],
            "resume": [("restart_grid", "owned")],
            "close": [("stop_grid", "owned", {"close": True})],
            "delete": [("stop_grid", "owned", {"close": False}), ("delete_grid", "owned")],
        }
        for action, writes in expected.items():
            with self.subTest(action):
                broker = Recording()
                status, body, _ = self.call(broker, action=action, product="grid_bot")
                self.assertEqual(status, 200, body)
                self.assertEqual(broker.writes, writes)
                self.assertEqual(broker.reads, [("grid", "owned")])


if __name__ == "__main__":
    unittest.main()
