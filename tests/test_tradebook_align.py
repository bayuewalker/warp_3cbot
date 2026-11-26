"""Tradebook alignment on the PR #33 branch. Fixtures only. No network."""

import json
import tempfile
import unittest
import unittest.mock
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from test_core import sig, strategy
from test_failures import ExplodingBroker, Markets, Research, paper
from test_shared_account import (
    RecordingBroker,
    live_account,
    live_cfg,
    warp_row,
)
from test_tradebook import fresh, policy

from warp3c.capital import (
    Exits,
    apply_ladder,
    build_ladder,
    capital_caps,
    gap_through_stop,
    order_minimum,
    readback_ok,
    scenario_stress,
    search_ladder,
    sleeve_nav,
    stress_loss,
)
from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.models import Account
from warp3c.operator import SNAPSHOT, publish
from warp3c.risk import GlobalRisk, assert_balances_explained
from warp3c.state import OrderJournal
from warp3c.threecommas import IntegrationError


def _nav_policy(equity: str):
    return policy(sleeve_usdt=Decimal(equity), sleeve_pct=Decimal("0"))


class TradebookAlignTests(unittest.TestCase):
    def test_t01_reserve_per_deal_and_portfolio_caps_match_the_table(self):
        expected = {
            "300": ("75", "75", "225"),
            "600": ("150", "150", "450"),
            "1200": ("300", "300", "900"),
            "5000": ("1250", "1250", "3750"),
            "10000": ("2500", "2500", "7500"),
        }
        for equity, (reserve, per_deal, portfolio) in expected.items():
            amount = Decimal(equity)
            chosen = _nav_policy(equity)
            book = sleeve_nav(
                account_usdt=amount, free_usdt=amount, warp_owned=Decimal("0"),
                non_warp_spent=Decimal("0"), non_warp_future_so=Decimal("0"), policy=chosen,
            )
            caps = capital_caps(
                nav=book.eligible, free_usdt=book.free_for_warp,
                warp_committed=Decimal("0"), warp_pending_so=Decimal("0"), policy=chosen,
            )
            self.assertEqual(book.eligible, amount)
            self.assertEqual(caps.reserve_floor, Decimal(reserve))
            self.assertEqual(caps.per_deal_ceiling, Decimal(per_deal))
            self.assertEqual(caps.portfolio_ceiling, Decimal(portfolio))

    def test_t02_a_deposit_does_not_resize_a_stored_ladder(self):
        before = fresh()
        stored = before.ladder.commitment
        after = fresh(
            account_usdt=Decimal("5000"), free_usdt=Decimal("5000"), warp_committed=stored,
        )
        self.assertEqual(before.ladder.commitment, stored)
        self.assertGreater(after.cap, before.cap)
        self.assertLessEqual(after.ladder.commitment, after.cap)

    def test_t03_unfunded_future_safety_orders_veto(self):
        decision = fresh(free_usdt=Decimal("500"), warp_pending_so=Decimal("400"))
        self.assertEqual(decision.decision, "VETO")
        self.assertIn("no_capital", decision.reasons)

    def test_t04_floating_loss_shrinks_caps_once(self):
        full = fresh()
        shrunk = fresh(account_usdt=Decimal("800"), free_usdt=Decimal("800"))
        self.assertLess(shrunk.cap, full.cap)
        marked = sleeve_nav(
            account_usdt=Decimal("1000"), free_usdt=Decimal("600"), warp_owned=Decimal("200"),
            non_warp_spent=Decimal("0"), non_warp_future_so=Decimal("0"), policy=policy(),
        )
        self.assertEqual(marked.eligible, Decimal("800"))

    def test_t07_order_floor_and_lot_size_reject_or_shrink(self):
        chosen = policy()
        self.assertEqual(
            order_minimum(Decimal("10"), chosen),
            Decimal("10") * (Decimal("1") + chosen.order_cushion) + Decimal("10") * chosen.fee_rate,
        )
        wide = search_ladder(
            price=Decimal("100"), cap=Decimal("300"), nav=Decimal("10000"),
            open_stop_risk=Decimal("0"), open_commitment=Decimal("0"),
            minimum=Decimal("5"), atr_1h_pct=Decimal("0.02"), policy=chosen,
        )
        tight = search_ladder(
            price=Decimal("100"), cap=Decimal("40"), nav=Decimal("10000"),
            open_stop_risk=Decimal("0"), open_commitment=Decimal("0"),
            minimum=Decimal("500"), atr_1h_pct=Decimal("0.02"), policy=chosen,
        )
        self.assertIsNotNone(wide)
        self.assertIsNone(tight)
        blocked = fresh(account_usdt=Decimal("80"), free_usdt=Decimal("80"), minimum=Decimal("500"))
        self.assertEqual(blocked.reasons, ["no_feasible_ladder"])
        self.assertIsNone(build_ladder(
            price=Decimal("100"), commitment=Decimal("300"), safety_orders=5,
            scale=Decimal("1"), step=Decimal("0.02"), policy=chosen,
            min_qty=Decimal("1000"), step_size=Decimal("0.001"),
        ))

    def test_t08_a_fourth_deal_is_vetoed(self):
        self.assertEqual(fresh(open_deals=3).reasons, ["max_deals"])

    def test_t09_flat_stress_rejects_three_deals_the_stop_model_prices(self):
        chosen = policy()
        nav = Decimal("1200")
        one = search_ladder(
            price=Decimal("100"), cap=Decimal("300"), nav=nav,
            open_stop_risk=Decimal("0"), open_commitment=Decimal("0"),
            minimum=Decimal("5"), atr_1h_pct=Decimal("0.035"), policy=chosen,
        )
        self.assertIsNotNone(one)
        deals = [(one.commitment, one.stop_loss)] * 3
        cap = chosen.stress_cap * nav
        flat = stress_loss([one.commitment] * 3, chosen)
        priced = scenario_stress(deals, chosen)
        self.assertEqual(priced, scenario_stress(list(deals), chosen))
        # Flat 30%+1% on 3x300 is 276.3, above the 96 cap, so three deals were unreachable.
        # The stop-bounded grid on this ladder is under the cap. The 6.5% aggregate still binds.
        self.assertGreater(flat, cap)
        self.assertLess(priced, cap)
        self.assertGreater(one.stop_loss * 3, Decimal("0.065") * nav)
        # Two deals already sit on the 6.5% aggregate, so a third cannot be resized in.
        third = fresh(
            account_usdt=nav, free_usdt=nav, open_deals=2, warp_committed=one.commitment * 2,
            open_stop_risk=Decimal("0.065") * nav,
            open_book=((one.commitment, one.stop_loss), (one.commitment, one.stop_loss)),
        )
        self.assertEqual(third.decision, "VETO")
        self.assertIn("no_feasible_ladder", third.reasons)

    def test_aggregate_stop_boundary(self):
        base = fresh(account_usdt=Decimal("10000"), free_usdt=Decimal("10000"))
        self.assertEqual(base.decision, "APPROVED")
        nav = base.nav
        stop = base.ladder.stop_loss
        under_open = Decimal("0.0649") * nav - stop
        self.assertGreater(under_open, 0)
        under = fresh(
            account_usdt=Decimal("10000"), free_usdt=Decimal("10000"), open_stop_risk=under_open,
        )
        over = fresh(
            account_usdt=Decimal("10000"), free_usdt=Decimal("10000"),
            open_stop_risk=Decimal("0.0651") * nav,
        )
        self.assertEqual(under.decision, "APPROVED")
        self.assertEqual(over.decision, "VETO")
        self.assertIn("no_feasible_ladder", over.reasons)

    def test_legacy_budget_can_only_tighten_the_aegis_cap(self):
        wide = fresh(max_commitment=Decimal("10000"))
        tight = fresh(max_commitment=Decimal("40"))
        self.assertEqual(wide.decision, "APPROVED")
        self.assertEqual(tight.decision, "APPROVED")
        self.assertLessEqual(tight.ladder.commitment, Decimal("40"))
        self.assertLess(tight.ladder.commitment, wide.ladder.commitment)

    def test_min_base_does_not_drop_a_feasible_budget(self):
        cfg = Settings(min_base=Decimal("15"), max_base=Decimal("15"))
        account = Account("p123", True, Decimal("200"), Decimal("0"), Decimal("200"))
        plans = GlobalRisk(cfg).plan(account, [], [sig("SOLUSDT")], equity=Decimal("200"))
        self.assertEqual(len(plans), 1)
        self.assertLess(plans[0].base_amount, cfg.min_base)
        self.assertGreater(plans[0].budget, 0)
        self.assertGreaterEqual(plans[0].budget, plans[0].full_commitment)

    def test_t24_slot_count_is_not_the_binding_limit(self):
        cfg = Settings(max_positions=10)
        account = Account("p123", True, Decimal("300"), Decimal("0"), Decimal("300"))
        names = ("SOLUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "ATOMUSDT")
        plans = GlobalRisk(cfg).plan(account, [], [sig(name) for name in names], equity=Decimal("300"))
        self.assertGreaterEqual(len(plans), 1)
        self.assertLess(len(plans), cfg.max_positions)

    def test_payload_round_trip_and_readback_mismatch(self):
        ladder = fresh().ladder
        payload = apply_ladder({"clientId": "warp3c-demo"}, ladder, Exits())
        self.assertEqual(payload["extraOrderCount"], ladder.safety_orders + 1)
        self.assertEqual(payload["amountPerTrade"], ladder.base)
        self.assertEqual(payload["extraOrderVolumeMultiplier"], ladder.scale)
        self.assertEqual(payload["extraOrderDeviation"], ladder.step)
        self.assertTrue(readback_ok(payload, Exits(), ladder))
        bad = dict(payload)
        bad["extraOrderCount"] = ladder.safety_orders
        self.assertFalse(readback_ok(bad, Exits(), ladder))
        self.assertTrue(readback_ok({"strategyId": "sid"}, Exits(), ladder))

    def test_t15_gap_through_stop_is_a_record_not_a_new_order(self):
        self.assertEqual(gap_through_stop(Decimal("70.25"), Decimal("68")), Decimal("2.25"))
        self.assertEqual(gap_through_stop(Decimal("70"), Decimal("71")), Decimal("0"))
        with self.assertRaises(ValueError):
            gap_through_stop(Decimal("0"), Decimal("1"))

    def test_t22_wrong_account_vetoes_with_no_create(self):
        self.assertEqual(fresh(mismatched_account=True).reasons, ["wrong_account"])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)

            class Bybit(ExplodingBroker):
                def get_account(self, code, exchange_code):
                    return Account(
                        code, True, Decimal("1000"), Decimal("0"), Decimal("1000"),
                        exchange_code="BYBIT", market="spot",
                    )

                def create_strategy(self, payload):
                    raise AssertionError("create_strategy")

            engine = Engine(cfg, Bybit(), Research(), journal, Markets())
            self.assertEqual(engine.cycle(), 0)
            self.assertEqual(engine.last_aegis[-1]["reasons"], ["wrong_account"])

    def test_t17_missing_lot_size_and_stale_signal_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)

            class NoLot(Markets):
                def scan(self, symbols, now=None):
                    found = super().scan(symbols, now=now)
                    return {symbol: replace(item, min_qty=None, step_size=None) for symbol, item in found.items()}

            broker = ExplodingBroker()
            engine = Engine(cfg, broker, Research(), journal, NoLot())
            self.assertEqual(engine.cycle(), 0)
            self.assertEqual(engine.last_aegis[-1]["status"], "WAIT_DATA_INCOMPLETE")
            self.assertEqual(engine.last_aegis[-1]["reasons"], ["lot_size"])
            self.assertEqual(broker.creates, 0)

        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)

            class Old(Markets):
                def scan(self, symbols, now=None):
                    moment = now or datetime.now(timezone.utc)
                    found = super().scan(symbols, now=now)
                    return {
                        symbol: replace(item, as_of=moment - timedelta(minutes=6))
                        for symbol, item in found.items()
                    }

            broker = ExplodingBroker()
            engine = Engine(cfg, broker, Research(), journal, Old())
            self.assertEqual(engine.cycle(), 0)
            self.assertEqual(engine.last_aegis[-1]["reasons"], ["stale"])
            self.assertEqual(broker.creates, 0)

    def test_t10_duplicate_alert_is_one_intent(self):
        class Twice:
            def research(self, symbols, age):
                return [sig("BTCUSDT"), sig("BTCUSDT")]

        class Once(ExplodingBroker):
            def __init__(self):
                super().__init__()
                self.payloads = []

            def create_strategy(self, payload):
                self.creates += 1
                self.payloads.append(payload)
                return {"strategyId": "sid-1", "clientId": payload["clientId"]}

        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            broker = Once()
            engine = Engine(cfg, broker, Twice(), journal, Markets())
            self.assertEqual(engine.cycle(), 1)
            self.assertEqual(broker.creates, 1)
            self.assertEqual(len({item["signal_id"].split("|")[0] for item in journal.recent_risk_decisions()}), 1)
            self.assertEqual(len(journal.recent_submissions(10)), 1)

    def test_t12_timeout_after_submit_is_not_resent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            broker = ExplodingBroker()
            engine = Engine(cfg, broker, Research(), journal, Markets())
            with self.assertRaises(IntegrationError):
                engine.cycle()
            pending = journal.unresolved()
            self.assertEqual(len(pending), 1)
            restarted = Engine(cfg, broker, Research(), journal, Markets())
            with self.assertRaises(IntegrationError):
                restarted.cycle()
            self.assertEqual(broker.creates, 1)
            self.assertEqual(journal.unresolved(), pending)

    def test_t13_partial_fill_recomputes_stop_and_a_missing_ladder_waits(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = Engine(cfg, ExplodingBroker(), Research(), journal, Markets())
            row = strategy("SOLUSDT")
            row.update(totalEntryAmount="0.2", totalEntryCost="20", totalExitAmount="0", totalExitCost="0")
            stored = {
                "commitment": "40", "stop_loss": "1", "stop_price": "50",
                "step": "0.02", "safety_orders": 5, "scale": "1",
            }
            bot = {"client_id": row["clientId"], "symbol": "SOLUSDT", "ladder": json.dumps(stored)}
            engine.last_positions = [row]
            exposure = engine._warp_exposure([bot], {"SOLUSDT": Decimal("100")})
            self.assertIsNotNone(exposure)
            self.assertGreater(exposure["stop"], Decimal("1"))
            self.assertGreater(exposure["pending"], 0)
            self.assertEqual(exposure["owned"], Decimal("20"))
            self.assertIsNone(engine._warp_exposure([{**bot, "ladder": None}], {"SOLUSDT": Decimal("100")}))

    def test_t16_strategy_quantity_above_the_wallet_freezes(self):
        account = live_account(external=False, warp_sol="0.01")
        row = warp_row(qty="0.2", cost="20")
        with self.assertRaises(IntegrationError):
            assert_balances_explained(account, [row], {"SOLUSDT": Decimal("100")}, shared=True)

    def test_t18_drawdown_from_realized_losses_enqueues_an_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = live_cfg(
                tmp, sleeve_usdt=Decimal("50"), max_drawdown=Decimal("0.9"), max_daily_loss=Decimal("0.9"),
            )
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            now = datetime.now(timezone.utc)
            journal.set_setting("shared_account_since", (now - timedelta(days=1)).isoformat())
            journal.set_setting("shared_equity_peak", (now - timedelta(hours=2)).isoformat() + "|50")
            journal.record_realized(
                "warp3c-" + "cd" * 16, "SOLUSDT", now - timedelta(hours=1), Decimal("-5"), Decimal("0"),
            )
            broker = RecordingBroker(live_account())
            engine = Engine(cfg, broker, Research(), journal, Markets())
            self.assertEqual(engine.cycle(), 0)
            self.assertEqual(engine.status.reason, "drawdown")
            self.assertEqual(broker.created, [])
            due = journal.due_alerts(now + timedelta(seconds=1))
            self.assertTrue(any(item[1] == "breaker:drawdown" for item in due))

    def test_t19_a_rank_change_does_not_modify_the_open_deal(self):
        class Other:
            def research(self, symbols, age):
                return [sig("ETHUSDT")]

        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            cfg = Settings(
                mode=cfg.mode, db_path=cfg.db_path, api_key=cfg.api_key, api_secret=cfg.api_secret,
                profile_code=cfg.profile_code, sleeve_pct=Decimal("1"), max_positions=1,
            )
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            stamp = datetime.now(timezone.utc).isoformat()
            client_id = "warp3c-" + "ef" * 16
            journal.insert_warp_bot(
                {
                    "warp_id": "rank1", "name": "WARP-rank1", "client_id": client_id, "external_code": "ext",
                    "kind": "dca", "product": "strategy", "account_code": "p123", "market": "spot",
                    "symbol": "SOLUSDT", "status": "confirmed", "commitment": "40",
                    "created_at": stamp, "updated_at": stamp,
                }
            )
            journal.update_warp_bot("rank1", ladder='{"commitment":"40","stop_loss":"1","stop_price":"90","step":"0.02","safety_orders":5,"scale":"1"}', updated_at=stamp)
            row = strategy("SOLUSDT", client_id=client_id)
            broker = RecordingBroker(Account("p123", True, Decimal("1000"), Decimal("0"), Decimal("1000")), [row])
            engine = Engine(cfg, broker, Other(), journal, Markets())
            self.assertEqual(engine.cycle(), 0)
            self.assertEqual(broker.created, [])
            self.assertEqual(journal.warp_bot("rank1")["ladder"].startswith('{"commitment":"40"'), True)
            self.assertNotIn("edit", {call[0] for call in broker.calls})

    def test_risk_decision_is_on_status_without_a_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = paper(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = Engine(cfg, ExplodingBroker(), Research(), journal, Markets())
            with self.assertRaises(IntegrationError):
                engine.cycle()
            rows = journal.recent_risk_decisions()
            self.assertEqual(len(rows), 1)
            self.assertNotIn("ladder", rows[0])
            with unittest.mock.patch.dict(SNAPSHOT, {}, clear=True):
                publish(cfg, journal, engine)
                body = json.dumps(SNAPSHOT["/api/status"])
                self.assertIn("risk_decisions", body)
                self.assertNotIn("api_secret", body)
                self.assertNotIn("api_key", body)
                self.assertNotIn("holdings", body)
                self.assertEqual(len(SNAPSHOT["/api/status"]["risk_decisions"]), 1)
