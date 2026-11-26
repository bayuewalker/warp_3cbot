"""WARP_SHARED_ACCOUNT: WARP trades a sleeve inside an account that other bots and manual buys also use.

Default (strict) behaviour must not change. Shared behaviour must stay fail-closed for WARP's own budget.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from test_core import evidence, sig, strategy

from warp3c.config import Settings, strict_flag
from warp3c.engine import Engine
from warp3c.httpapi import dispatch
from warp3c.models import Account, AssetBalance, EquityMark
from warp3c.operator import SNAPSHOT, publish
from warp3c.ownership import NotOwned, apply_action
from warp3c.risk import GlobalRisk, assert_balances_explained, mark_to_market, realized_pnl
from warp3c.state import OrderJournal
from warp3c.threecommas import IntegrationError, StrategyNotFound

ACK = "I_UNDERSTAND_LIVE_ORDERS"
WARP_CID = "warp3c-" + "ab" * 16
EXTERNAL_COINS = {
    "BTC": "0.177",
    "ETH": "1.0",
    "ZEC": "1.72",
    "XAUT": "0.9",
    "NIGHT": "400",
    "USDC": "3",
}
EXTERNAL_NAMES = ("BTC", "ETH", "ZEC", "XAUT", "NIGHT", "USDC")


def shared_cfg(**overrides) -> Settings:
    values = dict(
        shared_account=True,
        sleeve_usdt=Decimal("50"),
        dca_orders=2,
        max_base=Decimal("15"),
        max_committed=Decimal("0.6"),
        max_pair=Decimal("0.5"),
        max_group=Decimal("0.5"),
        max_positions=1,
        max_drawdown=Decimal("0.05"),
        max_daily_loss=Decimal("0.05"),
    )
    values.update(overrides)
    return Settings(**values)


def live_account(free="1609", *, external=True, warp_sol=None, now=None, quantities=None) -> Account:
    free_value = Decimal(free)
    assets = {"USDT": AssetBalance(free_value, Decimal("0"), free_value)}
    if external:
        for coin, amount in (quantities or EXTERNAL_COINS).items():
            assets[coin] = AssetBalance(Decimal(amount), Decimal("0"), Decimal(amount))
    if warp_sol is not None:
        assets["SOL"] = AssetBalance(Decimal(warp_sol), Decimal("0"), Decimal(warp_sol))
    return Account(
        "p123", False, free_value, Decimal("0"), free_value, assets,
        fetched_at=now or datetime.now(timezone.utc), exchange_code="BINANCE",
    )


def warp_row(*, price="100", qty="0.2", cost="20", client_id=WARP_CID) -> dict:
    row = strategy("SOLUSDT", client_id=client_id)
    row.update(
        paperTrading=False, totalEntryAmount=qty, totalEntryCost=cost, lastPrice=price,
        initFixedAmount=10, extraOrderCount=2, extraOrderVolumeMultiplier=1,
    )
    return row


def external_row(symbol="BTCUSDT") -> dict:
    row = strategy(symbol, client_id="someone-elses-bot")
    row.update(createdBy="web", paperTrading=False, totalEntryAmount="0.177", totalEntryCost="10000")
    return row


class ConfigTests(unittest.TestCase):
    def test_default_is_strict(self):
        self.assertFalse(Settings().shared_account)
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(Settings.from_env().shared_account)

    def test_env_flag_parses_only_explicit_values(self):
        for raw, expected in (("", False), ("0", False), ("false", False), ("1", True), ("TRUE", True)):
            with patch.dict(os.environ, {"WARP_SHARED_ACCOUNT": raw}):
                self.assertIs(strict_flag("WARP_SHARED_ACCOUNT"), expected)
        for raw in ("yes", "on", "2", "enabled"):
            with patch.dict(os.environ, {"WARP_SHARED_ACCOUNT": raw}), self.assertRaises(ValueError):
                strict_flag("WARP_SHARED_ACCOUNT")

    def test_from_env_enables_shared_mode_with_an_explicit_sleeve(self):
        env = {"WARP_SHARED_ACCOUNT": "1", "WARP_SLEEVE_USDT": "50"}
        with patch.dict(os.environ, env, clear=True):
            cfg = Settings.from_env()
        self.assertTrue(cfg.shared_account)
        self.assertEqual(cfg.sleeve_usdt, Decimal("50"))

    def test_shared_mode_requires_an_explicit_usdt_sleeve(self):
        with self.assertRaises(ValueError):
            Settings(shared_account=True)
        with self.assertRaises(ValueError):
            Settings(shared_account=True, sleeve_pct=Decimal("0.5"), sleeve_usdt=Decimal("0"))
        with patch.dict(os.environ, {"WARP_SHARED_ACCOUNT": "1"}, clear=True), self.assertRaises(ValueError):
            Settings.from_env()

    def test_flag_must_be_a_real_boolean(self):
        with self.assertRaises(ValueError):
            Settings(shared_account="true", sleeve_usdt=Decimal("50"))

    def test_live_ack_is_still_required_in_shared_mode(self):
        with self.assertRaises(ValueError):
            shared_cfg(mode="live", api_key="k", api_secret="s", profile_code="p123")
        cfg = shared_cfg(mode="live", api_key="k", api_secret="s", profile_code="p123", live_ack=ACK)
        self.assertTrue(cfg.shared_account)


class StrictModeUnchangedTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.prices = {"BTCUSDT": Decimal("60000"), "ETHUSDT": Decimal("3000"), "ZECUSDT": Decimal("400"),
                       "XAUTUSDT": Decimal("2400"), "NIGHTUSDT": Decimal("1"), "USDCUSDT": Decimal("1")}

    def test_external_coin_balances_still_halt_entries(self):
        with self.assertRaisesRegex(IntegrationError, "Unexplained spot balance"):
            GlobalRisk(Settings()).assess(
                live_account(), [], [sig("SOLUSDT")], prices=self.prices, marks=[], now=self.now
            )

    def test_external_strategy_still_blocks(self):
        account = live_account(external=False)
        with self.assertRaisesRegex(IntegrationError, "External strategy"):
            GlobalRisk(Settings()).assess(
                account, [external_row()], [sig("SOLUSDT")], prices=self.prices, marks=[], now=self.now
            )

    def test_strict_equity_is_still_whole_account(self):
        equity, reliable = mark_to_market(live_account(), [], self.prices)
        self.assertTrue(reliable)
        self.assertGreater(equity, Decimal("15000"))

    def test_strict_does_not_need_a_journal_or_baseline(self):
        account = live_account(external=False)
        decision = GlobalRisk(Settings()).assess(
            account, [], [sig("SOLUSDT")], prices={}, marks=[], now=self.now
        )
        self.assertTrue(decision.execution_permitted)

    def test_strict_assert_still_flags_a_strategy_quantity_above_the_wallet(self):
        account = live_account(external=False, warp_sol="0.05")
        with self.assertRaisesRegex(IntegrationError, "exceeds wallet"):
            assert_balances_explained(account, [warp_row()], {"SOLUSDT": Decimal("100")})


class SharedBalanceScopeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.since = self.now - timedelta(days=1)
        self.journaled = frozenset({WARP_CID})

    def assess(self, account, positions=(), *, cfg=None, marks=(), prices=None, journaled=None,
               since="default", signals=None):
        return GlobalRisk(cfg or shared_cfg()).assess(
            account,
            list(positions),
            [sig("SOLUSDT")] if signals is None else signals,
            prices={} if prices is None else prices,
            marks=list(marks),
            now=self.now,
            journaled=self.journaled if journaled is None else journaled,
            shared_since=self.since if since == "default" else since,
        )

    def test_external_holdings_do_not_halt_and_need_no_marks(self):
        decision = self.assess(live_account(now=self.now))
        self.assertTrue(decision.execution_permitted)
        self.assertEqual(decision.reason, "ok")
        self.assertEqual([order.symbol for order in decision.orders], ["SOLUSDT"])
        self.assertEqual(decision.equity, Decimal("50"))

    def test_external_strategy_is_tolerated_and_left_untouched(self):
        foreign = external_row()
        before = copy.deepcopy(foreign)
        decision = self.assess(live_account(now=self.now), [foreign])
        self.assertTrue(decision.execution_permitted)
        self.assertEqual(foreign, before)

    def test_malformed_external_strategy_is_not_read(self):
        decision = self.assess(
            live_account(now=self.now),
            [{"clientId": "x", "createdBy": "web", "profileStrategies": [{"profile": {"code": "p123"}}]}],
        )
        self.assertTrue(decision.execution_permitted)

    def test_strategy_without_profile_data_still_fails_closed(self):
        with self.assertRaises(IntegrationError):
            self.assess(live_account(now=self.now), [{"clientId": "x", "createdBy": "web"}])

    def test_external_strategy_does_not_count_against_the_position_cap(self):
        decision = self.assess(live_account(now=self.now), [external_row("BTCUSDT"), external_row("ETHUSDT")])
        self.assertTrue(decision.execution_permitted)
        self.assertEqual(len(decision.orders), 1)

    def test_warp_lookalike_without_a_journal_entry_fails_closed(self):
        account = live_account(now=self.now, warp_sol="0.2")
        prices = {"SOLUSDT": Decimal("100")}
        with self.assertRaisesRegex(IntegrationError, "not in the WARP journal"):
            self.assess(account, [warp_row()], prices=prices, journaled=frozenset())
        with self.assertRaisesRegex(IntegrationError, "not in the WARP journal"):
            GlobalRisk(shared_cfg()).assess(
                account, [warp_row()], [], prices=prices, marks=[], now=self.now, shared_since=self.since
            )

    def test_journaled_warp_strategy_reconciles_with_the_wallet(self):
        account = live_account(now=self.now, warp_sol="0.2")
        decision = self.assess(account, [warp_row()], prices={"SOLUSDT": Decimal("100")}, signals=[])
        self.assertEqual(decision.reason, "ok")
        self.assertEqual(decision.equity, Decimal("50"))

    def test_warp_quantity_above_the_wallet_fails_closed(self):
        account = live_account(now=self.now, warp_sol="0.05")
        with self.assertRaisesRegex(IntegrationError, "exceeds wallet"):
            self.assess(account, [warp_row()], prices={"SOLUSDT": Decimal("100")})

    def test_warp_asset_missing_from_the_wallet_fails_closed(self):
        with self.assertRaisesRegex(IntegrationError, "missing from wallet"):
            self.assess(live_account(now=self.now), [warp_row()], prices={"SOLUSDT": Decimal("100")})

    def test_warp_asset_without_a_mark_fails_closed(self):
        account = live_account(now=self.now, warp_sol="0.2")
        with self.assertRaisesRegex(IntegrationError, "no USDT mark"):
            self.assess(account, [warp_row()], prices={})

    def test_insufficient_free_usdt_fails_closed(self):
        decision = self.assess(live_account("30", now=self.now))
        self.assertFalse(decision.execution_permitted)
        self.assertEqual(decision.reason, "insufficient_free_usdt")
        self.assertEqual(decision.orders, [])

    def test_free_usdt_exactly_covering_the_sleeve_is_enough(self):
        decision = self.assess(live_account("50", now=self.now))
        self.assertTrue(decision.execution_permitted)

    def test_open_warp_deal_reduces_the_cash_the_sleeve_still_needs(self):
        account_ok = live_account("30", now=self.now, warp_sol="0.2")
        account_short = live_account("29.99", now=self.now, warp_sol="0.2")
        prices = {"SOLUSDT": Decimal("100")}
        self.assertTrue(self.assess(account_ok, [warp_row()], prices=prices, signals=[]).execution_permitted)
        short = self.assess(account_short, [warp_row()], prices=prices, signals=[])
        self.assertEqual(short.reason, "insufficient_free_usdt")
        self.assertFalse(short.execution_permitted)

    def test_stale_balance_fails_closed(self):
        old = live_account(now=self.now - timedelta(hours=1))
        with self.assertRaisesRegex(IntegrationError, "stale"):
            self.assess(old)

    def test_snapshot_without_a_timezone_fails_closed(self):
        naive = live_account(now=datetime(2026, 1, 1))
        with self.assertRaises(IntegrationError):
            self.assess(naive)

    def test_missing_usdt_balance_row_fails_closed(self):
        bare = Account("p123", False, Decimal("1609"), Decimal("0"), Decimal("1609"), {},
                       fetched_at=self.now, exchange_code="BINANCE")
        with self.assertRaisesRegex(IntegrationError, "balance missing"):
            self.assess(bare)
        no_usdt = live_account(now=self.now)
        wallet = dict(no_usdt.assets)
        wallet.pop("USDT")
        broken = Account("p123", False, Decimal("1609"), Decimal("0"), Decimal("1609"), wallet,
                         fetched_at=self.now, exchange_code="BINANCE")
        with self.assertRaisesRegex(IntegrationError, "balance missing"):
            self.assess(broken)

    def test_inconsistent_usdt_balance_fails_closed(self):
        account = live_account(now=self.now)
        skewed = Account("p123", False, Decimal("5000"), Decimal("0"), Decimal("5000"), account.assets,
                         fetched_at=self.now, exchange_code="BINANCE")
        with self.assertRaisesRegex(IntegrationError, "inconsistent"):
            self.assess(skewed)

    def test_missing_equity_baseline_fails_closed(self):
        with self.assertRaisesRegex(IntegrationError, "baseline"):
            self.assess(live_account(now=self.now), since=None)

    def test_unreliable_warp_mark_blocks(self):
        account = live_account(now=self.now, warp_sol="0.2")
        row = warp_row(price="150")
        decision = self.assess(account, [row], prices={"SOLUSDT": Decimal("100")}, signals=[])
        self.assertFalse(decision.execution_permitted)
        self.assertEqual(decision.reason, "mtm_unreliable")


class SharedAccountingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.since = self.now - timedelta(days=1)

    def run_assess(self, account, positions, prices, marks, *, cfg=None):
        return GlobalRisk(cfg or shared_cfg()).assess(
            account, positions, [], prices=prices, marks=marks, now=self.now,
            journaled=frozenset({WARP_CID}), shared_since=self.since,
        )

    def test_equity_is_the_sleeve_whatever_the_external_coins_do(self):
        marks = [EquityMark(self.now - timedelta(hours=2), Decimal("50"), True)]
        for factor in ("0.1", "1", "10"):
            quantities = {coin: str(Decimal(amount) * Decimal(factor)) for coin, amount in EXTERNAL_COINS.items()}
            account = live_account(now=self.now, quantities=quantities)
            decision = self.run_assess(account, [], {"BTCUSDT": Decimal("60000") * Decimal(factor)}, marks)
            self.assertEqual(decision.equity, Decimal("50"))
            self.assertTrue(decision.execution_permitted, factor)
            self.assertEqual(decision.reason, "ok")

    def test_external_crash_cannot_trip_the_drawdown_or_daily_loss_halt(self):
        marks = [EquityMark(self.now - timedelta(hours=3), Decimal("50"), True),
                 EquityMark(self.now - timedelta(hours=1), Decimal("50"), True)]
        crashed = live_account(now=self.now, quantities={"BTC": "0.0001", "ETH": "0.001"})
        self.assertTrue(self.run_assess(crashed, [], {}, marks).execution_permitted)

    def test_an_old_whole_account_mark_is_never_compared_with_sleeve_equity(self):
        whole_account = [EquityMark(self.since - timedelta(hours=1), Decimal("17000"), True)]
        decision = self.run_assess(live_account(now=self.now), [], {}, whole_account)
        self.assertTrue(decision.execution_permitted)
        strict = GlobalRisk(Settings()).assess(
            live_account(external=False, free="50", now=self.now), [], [], prices={}, marks=whole_account, now=self.now
        )
        self.assertEqual(strict.reason, "drawdown")

    def test_warp_drawdown_still_halts_and_external_gains_cannot_mask_it(self):
        marks = [EquityMark(self.now - timedelta(hours=3), Decimal("50"), True)]
        losing = warp_row(price="80")
        prices = {"SOLUSDT": Decimal("80"), "BTCUSDT": Decimal("999999")}
        for external in ({"BTC": "0.177"}, {"BTC": "50"}):
            account = live_account(now=self.now, warp_sol="0.2", quantities=external)
            decision = self.run_assess(account, [losing], prices, marks)
            self.assertFalse(decision.execution_permitted)
            self.assertEqual(decision.reason, "drawdown")
            self.assertEqual(decision.equity, Decimal("46"))

    def test_warp_daily_loss_still_halts(self):
        marks = [EquityMark(self.now - timedelta(minutes=30), Decimal("50"), True)]
        cfg = shared_cfg(max_drawdown=Decimal("0.5"))
        account = live_account(now=self.now, warp_sol="0.2")
        decision = self.run_assess(account, [warp_row(price="80")], {"SOLUSDT": Decimal("80")}, marks, cfg=cfg)
        self.assertEqual(decision.reason, "daily_loss")
        self.assertFalse(decision.execution_permitted)

    def test_gains_on_a_warp_deal_raise_equity_by_the_open_profit_only(self):
        account = live_account(now=self.now, warp_sol="0.2")
        decision = self.run_assess(account, [warp_row(price="110")], {"SOLUSDT": Decimal("110")}, [])
        self.assertEqual(decision.equity, Decimal("52"))

    def test_mark_to_market_ignores_the_wallet_in_shared_mode(self):
        rich = live_account(free="100000")
        poor = live_account(free="1", external=False)
        for account in (rich, poor):
            self.assertEqual(
                mark_to_market(account, [], {}, sleeve_usdt=Decimal("50")), (Decimal("50"), True)
            )

    def test_broken_warp_cost_basis_is_unreliable_not_an_exception(self):
        row = warp_row()
        row.pop("totalExitCost")
        account = live_account(now=self.now, warp_sol="0.2")
        self.assertEqual(
            mark_to_market(account, [row], {"SOLUSDT": Decimal("100")}, sleeve_usdt=Decimal("50")),
            (Decimal("0"), False),
        )


class RecordingBroker:
    """Reads plus create_strategy only. Any other write would raise AttributeError."""

    def __init__(self, account, live=(), *, dca_error=False):
        self.account = account
        self.live = list(live)
        self.dca_error = dca_error
        self.calls: list[tuple] = []
        self.created: list[dict] = []

    def get_account(self, code, exchange_code):
        self.calls.append(("get_account", code))
        return self.account

    def get_live(self):
        self.calls.append(("get_live",))
        return self.live

    def get_strategy(self, client_id):
        self.calls.append(("get_strategy", client_id))
        raise StrategyNotFound(client_id)

    def list_dca_bots(self):
        self.calls.append(("list_dca_bots",))
        if self.dca_error:
            raise IntegrationError("3Commas bot list unavailable")
        return [{"name": "VORTEQ-1", "maxRequiredAmount": "120 USDT"}]

    def create_strategy(self, payload):
        self.calls.append(("create_strategy", payload["pairCode"]))
        self.created.append(payload)
        return {"strategyId": "sid-1", "clientId": payload["clientId"]}


class Research:
    def research(self, symbols, age):
        return [sig("SOLUSDT")]


class Markets:
    def scan(self, symbols, now=None):
        return {symbol: evidence(symbol) for symbol in symbols}


def live_cfg(tmp, **overrides) -> Settings:
    values = dict(
        mode="live", live_ack=ACK, api_key="k", api_secret="s", profile_code="p123",
        db_path=os.path.join(tmp, "warp.db"), symbols=("SOLUSDT",),
        sleeve_usdt=Decimal("200"), per_deal_ceiling=Decimal("0.5"),
        deal_stop_risk=Decimal("0.2"), aggregate_stop_risk=Decimal("0.3"), max_base=Decimal("25"),
    )
    values.update(overrides)
    return shared_cfg(**values)


class SharedEngineTests(unittest.TestCase):
    def build(self, tmp, broker, **overrides):
        cfg = live_cfg(tmp, **overrides)
        journal = OrderJournal(cfg.db_path)
        self.addCleanup(journal.close)
        return cfg, journal, Engine(cfg, broker, Research(), journal, Markets())

    def test_shared_cycle_opens_one_warp_order_and_touches_no_external_coin(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = RecordingBroker(live_account(), [external_row("BTCUSDT"), external_row("ETHUSDT")])
            _cfg, _journal, engine = self.build(tmp, broker)
            self.assertEqual(engine.cycle(), 1)
            self.assertEqual(engine.status.reason, "ok")
            self.assertEqual([call for call in broker.calls if call[0] == "create_strategy"],
                             [("create_strategy", "SOLUSDT")])
            payload = broker.created[0]
            self.assertEqual(payload["pairCode"], "SOLUSDT")
            self.assertEqual(payload["side"], "long")
            self.assertTrue(payload["clientId"].startswith("warp3c-"))
            self.assertNotIn(payload["pairCode"][:-4], EXTERNAL_NAMES)
            writes = {call[0] for call in broker.calls} - {"get_account", "get_live", "get_strategy", "list_dca_bots"}
            self.assertEqual(writes, {"create_strategy"})

    def test_strict_engine_cycle_with_the_same_account_places_nothing(self):
        class Quotes(Markets):
            def last_price(self, symbol):
                return "1000"

        with tempfile.TemporaryDirectory() as tmp:
            for market, expected in ((Markets(), "mark_unavailable"), (Quotes(), None)):
                broker = RecordingBroker(live_account())
                cfg = live_cfg(tmp, shared_account=False)
                journal = OrderJournal(cfg.db_path)
                engine = Engine(cfg, broker, Research(), journal, market)
                if expected:
                    self.assertEqual(engine.cycle(), 0)
                    self.assertEqual(engine.status.reason, expected)
                else:
                    with self.assertRaisesRegex(IntegrationError, "Unexplained spot balance"):
                        engine.cycle()
                self.assertEqual(broker.created, [])
                journal.close()

    def test_external_holdings_do_not_need_public_marks(self):
        class NoQuotes(Markets):
            def last_price(self, symbol):
                raise AssertionError(f"external coin was priced: {symbol}")

        with tempfile.TemporaryDirectory() as tmp:
            broker = RecordingBroker(live_account())
            cfg = live_cfg(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = Engine(cfg, broker, Research(), journal, NoQuotes())
            self.assertEqual(engine.cycle(), 1)

    def test_insufficient_free_usdt_places_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = RecordingBroker(live_account("120"))
            _cfg, _journal, engine = self.build(tmp, broker)
            self.assertEqual(engine.cycle(), 0)
            self.assertEqual(engine.status.reason, "insufficient_free_usdt")
            self.assertFalse(engine.status.execution_permitted)
            self.assertEqual(broker.created, [])

    def test_stale_account_places_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = live_account(now=datetime.now(timezone.utc) - timedelta(hours=1))
            broker = RecordingBroker(stale)
            _cfg, _journal, engine = self.build(tmp, broker)
            with self.assertRaisesRegex(IntegrationError, "stale"):
                engine.cycle()
            self.assertEqual(broker.created, [])
            self.assertFalse(engine.status.execution_permitted)

    def test_balance_read_error_places_nothing(self):
        class Down(RecordingBroker):
            def get_account(self, code, exchange_code):
                raise IntegrationError("exchange disconnected")

        with tempfile.TemporaryDirectory() as tmp:
            broker = Down(live_account())
            _cfg, _journal, engine = self.build(tmp, broker)
            with self.assertRaises(IntegrationError):
                engine.cycle()
            self.assertEqual(broker.created, [])

    def test_missing_balance_data_places_nothing(self):
        bare = Account("p123", False, Decimal("1609"), Decimal("0"), Decimal("1609"), {},
                       fetched_at=datetime.now(timezone.utc), exchange_code="BINANCE")
        with tempfile.TemporaryDirectory() as tmp:
            broker = RecordingBroker(bare)
            _cfg, _journal, engine = self.build(tmp, broker)
            with self.assertRaisesRegex(IntegrationError, "balance missing"):
                engine.cycle()
            self.assertEqual(broker.created, [])

    def test_foreign_bot_reserve_read_failure_places_nothing_in_shared_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = RecordingBroker(live_account(), dca_error=True)
            _cfg, _journal, engine = self.build(tmp, broker)
            with self.assertRaises(IntegrationError):
                engine.cycle()
            self.assertEqual(broker.created, [])

    def test_a_warp_lookalike_strategy_missing_from_the_journal_places_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            account = live_account(warp_sol="0.2")
            broker = RecordingBroker(account, [warp_row()])
            _cfg, _journal, engine = self.build(tmp, broker)
            with self.assertRaisesRegex(IntegrationError, "not in the WARP journal"):
                engine.cycle()
            self.assertEqual(broker.created, [])

    def test_a_journaled_warp_strategy_reconciles(self):
        class Held(Markets):
            def scan(self, symbols, now=None):
                return {symbol: evidence(symbol) for symbol in symbols}

        with tempfile.TemporaryDirectory() as tmp:
            account = live_account(warp_sol="0.2")
            broker = RecordingBroker(account, [warp_row()])
            cfg = live_cfg(tmp)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            journal.reserve(WARP_CID, "SOLUSDT", {"pairCode": "SOLUSDT"})
            journal.confirm(WARP_CID, "abc")
            engine = Engine(cfg, broker, Research(), journal, Held())
            engine.cycle()
            self.assertEqual(engine.status.reason, "ok")
            self.assertEqual(broker.created, [])

    def test_whole_account_marks_from_strict_mode_do_not_halt_a_new_shared_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = RecordingBroker(live_account())
            cfg, journal, engine = self.build(tmp, broker)
            journal.record_equity(Decimal("17000"), True, now=datetime.now(timezone.utc) - timedelta(hours=3))
            self.assertEqual(engine.cycle(), 1)
            self.assertTrue(journal.get_setting("shared_account_since"))
            self.assertEqual([mark.equity for mark in journal.equity_marks()][-1], Decimal("200"))

    def test_switching_the_flag_keeps_the_baseline_peak_and_realized_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = RecordingBroker(live_account())
            cfg, journal, engine = self.build(tmp, broker)
            engine.cycle()
            since = journal.get_setting("shared_account_since")
            peak = journal.get_setting("shared_equity_peak")
            closed = datetime.now(timezone.utc)
            journal.record_realized("warp3c-" + "cd" * 16, "SOLUSDT", closed, Decimal("-4"), Decimal("0.1"))
            strict = Engine(live_cfg(tmp, shared_account=False), RecordingBroker(live_account(external=False)),
                            Research(), journal, Markets())
            strict.cycle()
            self.assertEqual(journal.get_setting("shared_account_since"), since)
            self.assertEqual(journal.get_setting("shared_equity_peak"), peak)
            self.assertEqual(journal.realized_pnl()[0]["realized_usdt"], Decimal("-4"))

    def test_live_ack_gate_still_blocks_a_shared_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = RecordingBroker(live_account())
            _cfg, _journal, engine = self.build(tmp, broker)
            with patch("warp3c.engine.execution_allowed", return_value=(False, "live_ack_missing")):
                self.assertEqual(engine.cycle(), 0)
            self.assertEqual(broker.created, [])


class WarpOnlyActsOnItsOwnDealsTests(unittest.TestCase):
    class Broker:
        def __init__(self, remote):
            self.remote = remote
            self.writes: list[tuple] = []

        def get_dca_bot(self, code):
            return self.remote

        def get_grid_bot(self, code):
            return self.remote

        def pause_dca_bot(self, code):
            self.writes.append(("pause", code))

        def resume_dca_bot(self, code):
            self.writes.append(("resume", code))

        def close_dca_positions(self, code):
            self.writes.append(("close", code))

        def delete_dca_bot(self, code):
            self.writes.append(("delete", code))

    def journal_with(self, tmp, **row):
        journal = OrderJournal(os.path.join(tmp, "own.db"))
        self.addCleanup(journal.close)
        stamp = datetime.now(timezone.utc).isoformat()
        base = {
            "warp_id": "0a0b0c0d", "name": "WARP-0a0b0c0d", "client_id": WARP_CID, "external_code": "code-1",
            "kind": "dca", "product": "dca_bot", "account_code": "p123", "market": "spot",
            "symbol": "SOLUSDT", "status": "confirmed", "commitment": "30", "created_at": stamp,
            "updated_at": stamp,
        }
        base.update(row)
        journal.insert_warp_bot(base)
        return journal

    def test_an_unjournaled_bot_id_never_reaches_the_broker(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal_with(tmp)
            broker = self.Broker({"name": "WARP-0a0b0c0d", "code": "code-1"})
            for action in ("pause", "resume", "close", "delete"):
                with self.assertRaises(NotOwned):
                    apply_action(broker, journal, "ffffffff", action)
            self.assertEqual(broker.writes, [])

    def test_a_journal_row_pointing_at_an_external_bot_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal_with(tmp)
            external = self.Broker({"name": "VORTEQ-BTC", "code": "code-1"})
            for action in ("pause", "resume", "close", "delete"):
                with self.assertRaises(NotOwned):
                    apply_action(external, journal, "0a0b0c0d", action)
            swapped = self.Broker({"name": "WARP-0a0b0c0d", "code": "someone-elses-code"})
            with self.assertRaises(NotOwned):
                apply_action(swapped, journal, "0a0b0c0d", "close")
            self.assertEqual(external.writes + swapped.writes, [])

    def test_a_bot_row_that_is_not_named_like_warp_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal_with(tmp, name="AEGIS-0a0b0c0d")
            broker = self.Broker({"name": "AEGIS-0a0b0c0d", "code": "code-1"})
            with self.assertRaises(NotOwned):
                apply_action(broker, journal, "0a0b0c0d", "close")
            self.assertEqual(broker.writes, [])

    def test_an_owned_bot_is_still_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal_with(tmp)
            broker = self.Broker({"name": "WARP-0a0b0c0d", "code": "code-1", "active": True})
            apply_action(broker, journal, "0a0b0c0d", "pause")
            self.assertEqual(broker.writes, [("pause", "code-1")])


class SurfaceTests(unittest.TestCase):
    def publish_status(self, tmp, **overrides):
        cfg = live_cfg(tmp, **overrides)
        journal = OrderJournal(cfg.db_path)
        self.addCleanup(journal.close)
        engine = Engine(cfg, RecordingBroker(live_account()), Research(), journal, Markets())
        engine.cycle()
        with patch.dict(SNAPSHOT, {}, clear=True):
            publish(cfg, journal, engine)
            return json.loads(json.dumps(dict(SNAPSHOT), default=str))

    def test_status_and_config_expose_a_boolean_and_no_external_coins(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = self.publish_status(tmp)
        self.assertIs(shared["/api/status"]["shared_account"], True)
        self.assertIs(shared["/api/config"]["shared_account"], True)
        self.assertEqual(shared["/api/status"]["equity"], "200")
        for route in ("/api/status", "/api/risk", "/api/config", "/api/breakers"):
            text = json.dumps(shared[route])
            for coin in ("0.177", "1.72", *("\"" + name + "\"" for name in EXTERNAL_NAMES)):
                self.assertNotIn(coin, text, f"{route} {coin}")

    def test_strict_status_reports_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "w.db"))
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = Engine(cfg, None, None, journal, None)
            with patch.dict(SNAPSHOT, {}, clear=True):
                publish(cfg, journal, engine)
                self.assertIs(SNAPSHOT["/api/status"]["shared_account"], False)

    def test_public_health_carries_no_balances_or_mode_detail(self):
        status, _headers, body = dispatch("GET", "/health", {}, b"")
        text = body.decode()
        self.assertEqual(status, 200)
        self.assertNotIn("shared_account", text)
        for coin in EXTERNAL_NAMES:
            self.assertNotIn(coin, text)


def _closed(client_id: str, *, entry: str = "10", exit_cost: str = "5") -> dict:
    row = warp_row(client_id=client_id, cost=entry, qty="0.1")
    row["status"] = "completed"
    row["totalEntryCost"] = entry
    row["totalExitCost"] = exit_cost
    row["totalExitAmount"] = "0.1"
    return row


def _own(journal, warp_id: str, client_id: str, status: str = "confirmed") -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    journal.insert_warp_bot(
        {
            "warp_id": warp_id, "name": "WARP-" + warp_id, "client_id": client_id, "external_code": "ext-" + warp_id,
            "kind": "dca", "product": "strategy", "account_code": "p123", "market": "spot", "symbol": "SOLUSDT",
            "status": status, "commitment": "10", "created_at": stamp, "updated_at": stamp,
        }
    )
    journal.reserve(client_id, "SOLUSDT", {"pairCode": "SOLUSDT"})
    journal.confirm(client_id, "sid-" + warp_id)


class RealizedLossTests(unittest.TestCase):
    """Closed WARP losses stay in the sleeve. The wallet's other USDT does not refill them."""

    def test_closed_loss_is_derived_and_a_missing_cost_is_not_zero(self):
        net, fee = realized_pnl(_closed("warp3c-" + "ab" * 16, entry="20", exit_cost="5"))
        self.assertEqual((net, fee), (Decimal("-15"), Decimal("0")))
        charged = _closed("warp3c-" + "ab" * 16, entry="20", exit_cost="5")
        charged["totalFee"] = "1"
        self.assertEqual(realized_pnl(charged)[0], Decimal("-16"))
        for broken in ({}, {"totalEntryCost": "10"}, _closed("warp3c-" + "ab" * 16)):
            row = dict(broken)
            if "totalExitAmount" in row:
                row["totalExitAmount"] = "0"
            if row:
                with self.assertRaises(IntegrationError):
                    realized_pnl(row)

    def test_three_stopped_deals_cut_sleeve_equity_and_trip_the_breaker(self):
        now = datetime.now(timezone.utc)
        account = live_account(now=now)
        decision = GlobalRisk(shared_cfg()).assess(
            account, [], [], prices={}, marks=[], now=now, journaled=frozenset(),
            shared_since=now - timedelta(days=1), realized_since=Decimal("-15"),
            day_start=Decimal("50"), peak=(now - timedelta(hours=1), Decimal("50")),
        )
        self.assertEqual(decision.equity, Decimal("35"))
        self.assertEqual(decision.reason, "drawdown")
        self.assertFalse(decision.execution_permitted)
        self.assertEqual(decision.orders, [])
        daily = GlobalRisk(shared_cfg(max_drawdown=Decimal("0.9"))).assess(
            account, [], [], prices={}, marks=[], now=now, journaled=frozenset(),
            shared_since=now - timedelta(days=1), realized_since=Decimal("-15"),
            day_start=Decimal("50"), peak=(now - timedelta(hours=1), Decimal("50")),
        )
        self.assertEqual(daily.reason, "daily_loss")
        self.assertFalse(daily.execution_permitted)

    def _sleeve(self, tmp, broker, **overrides):
        cfg = live_cfg(tmp, sleeve_usdt=Decimal("50"), **overrides)
        journal = OrderJournal(cfg.db_path)
        self.addCleanup(journal.close)
        now = datetime.now(timezone.utc)
        journal.set_setting("shared_account_since", (now - timedelta(days=1)).isoformat())
        journal.set_setting("shared_day_start", now.date().isoformat() + "|50")
        journal.set_setting("shared_equity_peak", (now - timedelta(hours=2)).isoformat() + "|50")
        return cfg, journal, Engine(cfg, broker, Research(), journal, Markets())

    def test_engine_books_three_closes_and_the_breaker_survives_restart(self):
        deals = []
        with tempfile.TemporaryDirectory() as tmp:
            ids = ["warp3c-" + digit * 32 for digit in "123"]
            for client_id in ids:
                deals.append(_closed(client_id))
            cfg, journal, engine = self._sleeve(tmp, RecordingBroker(live_account(), deals))
            for index, client_id in enumerate(ids):
                _own(journal, f"aaaaaaa{index + 1}", client_id)
            self.assertEqual(engine.cycle(), 0)
            self.assertEqual(engine.status.reason, "drawdown")
            self.assertFalse(engine.status.execution_permitted)
            self.assertEqual(engine.broker.created, [])
            booked = sum((row["realized_usdt"] for row in journal.realized_pnl()), Decimal("0"))
            self.assertEqual(booked, Decimal("-15"))
            self.assertEqual(journal.equity_marks(scope="sleeve")[-1].equity, Decimal("35"))
            peak = journal.get_setting("shared_equity_peak")
            restarted = OrderJournal(cfg.db_path)
            self.addCleanup(restarted.close)
            again = Engine(cfg, RecordingBroker(live_account()), Research(), restarted, Markets())
            self.assertEqual(again.cycle(), 0)
            self.assertEqual(again.status.reason, "drawdown")
            self.assertEqual(restarted.get_setting("shared_equity_peak"), peak)
            self.assertEqual(sum((row["realized_usdt"] for row in restarted.realized_pnl()), Decimal("0")), Decimal("-15"))

    def test_missing_or_unreadable_realized_pnl_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            deal = _closed("warp3c-" + "45" * 16)
            deal.pop("totalExitCost")
            _cfg, journal, engine = self._sleeve(tmp, RecordingBroker(live_account(), [deal]))
            _own(journal, "bbbbbbb1", deal["clientId"])
            with self.assertRaisesRegex(IntegrationError, "Realized P&L unreadable"):
                engine.cycle()
            self.assertEqual(journal.realized_pnl(), [])
            self.assertEqual(journal.warp_bots()[0]["status"], "confirmed")
            self.assertEqual(engine.broker.created, [])

        with tempfile.TemporaryDirectory() as tmp:
            _cfg, journal, engine = self._sleeve(tmp, RecordingBroker(live_account()))
            client_id = "warp3c-" + "67" * 16
            _own(journal, "bbbbbbb2", client_id, status="closed")
            with self.assertRaisesRegex(IntegrationError, "no realized P&L"):
                engine.cycle()
            self.assertEqual(engine.broker.created, [])

        with tempfile.TemporaryDirectory() as tmp:
            _cfg, journal, engine = self._sleeve(tmp, RecordingBroker(live_account()))
            client_id = "warp3c-" + "89" * 16
            _own(journal, "bbbbbbb3", client_id, status="closed")
            journal.record_realized(client_id, "SOLUSDT", datetime.now(timezone.utc), Decimal("-5"), Decimal("0"))
            journal.db.execute("UPDATE realized_pnl SET realized_usdt='nope' WHERE client_id=?", (client_id,))
            with self.assertRaisesRegex(IntegrationError, "unreadable"):
                engine.cycle()

    def test_trade_loss_and_stop_streak_veto_without_tripping_the_circuit(self):
        loose = dict(max_daily_loss=Decimal("0.5"), max_drawdown=Decimal("0.5"), sleeve_usdt=Decimal("200"))
        with tempfile.TemporaryDirectory() as tmp:
            cfg = live_cfg(tmp, trade_daily_loss=Decimal("0.02"), **loose)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            now = datetime.now(timezone.utc)
            journal.set_setting("shared_account_since", (now - timedelta(days=1)).isoformat())
            journal.set_setting("shared_day_start", now.date().isoformat() + "|200")
            journal.set_setting("shared_equity_peak", (now - timedelta(hours=2)).isoformat() + "|200")
            journal.record_realized("warp3c-" + "ab" * 16, "SOLUSDT", now - timedelta(minutes=5), Decimal("-8"), Decimal("0"))
            engine = Engine(cfg, RecordingBroker(live_account()), Research(), journal, Markets())
            self.assertEqual(engine.cycle(), 0)
            self.assertEqual(engine.broker.created, [])
            self.assertEqual(engine.last_aegis[-1]["reasons"], ["daily_loss"])

        with tempfile.TemporaryDirectory() as tmp:
            cfg = live_cfg(tmp, trade_daily_loss=Decimal("0.5"), trade_drawdown=Decimal("0.5"), **loose)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            now = datetime.now(timezone.utc)
            journal.set_setting("shared_account_since", (now - timedelta(days=1)).isoformat())
            journal.set_setting("shared_day_start", now.date().isoformat() + "|200")
            journal.set_setting("shared_equity_peak", (now - timedelta(hours=2)).isoformat() + "|200")
            for index in (1, 2):
                journal.record_realized(
                    "warp3c-" + str(index) * 32, "SOLUSDT", now - timedelta(minutes=index), Decimal("-0.01"), Decimal("0")
                )
            engine = Engine(cfg, RecordingBroker(live_account()), Research(), journal, Markets())
            self.assertEqual(engine.cycle(), 0)
            self.assertEqual(engine.broker.created, [])
            self.assertEqual(engine.last_aegis[-1]["reasons"], ["stop_streak"])

    def test_strict_mode_does_not_require_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = live_cfg(tmp, shared_account=False)
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            _own(journal, "ccccccc1", "warp3c-" + "ff" * 16, status="closed")
            engine = Engine(cfg, RecordingBroker(live_account(external=False)), Research(), journal, Markets())
            engine.cycle()
            self.assertEqual(journal.realized_pnl(), [])
            self.assertNotEqual(engine.status.reason, "drawdown")


if __name__ == "__main__":
    unittest.main()
