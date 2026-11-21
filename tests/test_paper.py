"""Shadow spot ladder: fills, same-bar stop, fees, cooldown, restart, and the go-live gate."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from warp3c.capital import Exits, Policy, approve, cooldown_block, score_candidate
from warp3c.config import Settings
from warp3c.engine import Engine
from warp3c.models import MarketEvidence, Signal
from warp3c.paper import PaperBook
from warp3c.state import OrderJournal


def policy() -> Policy:
    return Policy.from_settings(Settings(sleeve_usdt=Decimal("1000"), sleeve_pct=Decimal("1")))


def ladder():
    decision = approve(
        policy=policy(),
        account_usdt=Decimal("1000"),
        free_usdt=Decimal("1000"),
        warp_owned=Decimal("0"),
        warp_committed=Decimal("0"),
        warp_pending_so=Decimal("0"),
        non_warp_spent=Decimal("0"),
        non_warp_future_so=Decimal("0"),
        open_deals=0,
        open_stop_risk=Decimal("0"),
        open_symbols=[],
        price=Decimal("100"),
        minimum=Decimal("5"),
        model_buy=True,
        score=score_candidate(
            regime="trend_up",
            rsi=Decimal("55"),
            quote_volume=Decimal("20000000"),
            spread=Decimal("0.001"),
            change_24h=Decimal("0.02"),
            tradable=True,
            volatility=Decimal("0.02"),
            policy=policy(),
        ),
        symbol="SOLUSDT",
        quote_volume=Decimal("20000000"),
        spread=Decimal("0.001"),
        change_24h=Decimal("0.02"),
        now=datetime(2026, 11, 21, tzinfo=timezone.utc),
    )
    assert decision.decision == "APPROVED" and decision.ladder is not None
    return decision


class PaperBookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.book = PaperBook(os.path.join(self.tmp.name, "paper.sqlite"))
        self.now = datetime(2026, 11, 21, tzinfo=timezone.utc)

    def tearDown(self):
        self.book.close()
        self.tmp.cleanup()

    def _open(self, when: datetime | None = None):
        chosen = ladder()
        return self.book.open_ladder(
            symbol="SOLUSDT",
            ladder=chosen.ladder,
            entry=Decimal("100"),
            exits=Exits(),
            policy=policy(),
            now=when or self.now,
            sleeve=chosen.nav,
        )

    def test_safety_fill_matches_the_ladder_and_charges_fees(self):
        deal = self._open()
        level = Decimal(deal["legs"][1]["price"])
        quote = Decimal(deal["legs"][1]["quote"])
        base = Decimal(deal["legs"][0]["quote"])
        candle = {
            "close_at": self.now + timedelta(minutes=15),
            "high": level,
            "low": level,
            "close": level,
        }
        self.book.advance("SOLUSDT", [candle], policy(), candle["close_at"])
        kept = self.book.open_deals()[0]
        self.assertTrue(kept["legs"][1]["filled"])
        self.assertFalse(kept["legs"][2]["filled"])
        coins = base / Decimal("100") + quote / level
        cost = base + quote
        fees = policy().fee_rate * cost
        self.assertEqual(Decimal(kept["coins"]), coins)
        self.assertEqual(Decimal(kept["cost"]), cost)
        self.assertEqual(Decimal(kept["fees"]), fees)
        self.assertEqual(Decimal(kept["avg_price"]), cost / coins)

    def test_same_bar_tp_and_sl_books_the_stop_with_slippage(self):
        deal = self._open()
        sl = Decimal(deal["sl_price"])
        candle = {
            "close_at": self.now + timedelta(minutes=15),
            "high": Decimal(deal["tp_price"]) + Decimal("1"),
            "low": sl - Decimal("1"),
            "close": sl,
        }
        closed = self.book.advance("SOLUSDT", [candle], policy(), candle["close_at"])
        self.assertEqual(closed[0]["status"], "stop")
        self.assertEqual(closed[0]["close_reason"], "same_bar")
        fill = sl * (Decimal("1") - policy().stop_slippage)
        coins = Decimal(closed[0]["coins"])
        cost = Decimal(closed[0]["cost"])
        entry_fees = policy().fee_rate * cost
        exit_fee = policy().fee_rate * coins * fill
        self.assertEqual(Decimal(closed[0]["pnl"]), coins * fill - cost - entry_fees - exit_fee)
        self.assertEqual(cooldown_block(self.book.events(), "SOLUSDT", candle["close_at"], policy()), "stop_cooldown")

    def test_book_survives_a_restart(self):
        self._open()
        self.book.close()
        again = PaperBook(os.path.join(self.tmp.name, "paper.sqlite"))
        try:
            self.assertEqual(again.open_deals()[0]["symbol"], "SOLUSDT")
            self.assertEqual(again.open_deals()[0]["status"], "open")
        finally:
            again.close()
            self.book = again

    def test_gate_needs_thirty_days_thirty_deals_and_a_bounded_drawdown(self):
        rules = policy()
        empty = self.book.metrics(rules, self.now, Decimal("1000"))
        self.assertFalse(empty["gate_pass"])
        self.assertIn("paper_days", empty["gate_reasons"])
        self.assertIn("closed_deals", empty["gate_reasons"])
        start = self.now - timedelta(days=40)
        for index in range(30):
            self._open(start + timedelta(days=index))
            deal = self.book.open_deals()[0]
            tp = Decimal(deal["tp_price"])
            moment = start + timedelta(days=index, hours=4)
            candle = {"close_at": moment, "high": tp, "low": Decimal("100"), "close": tp}
            self.book.advance("SOLUSDT", [candle], rules, moment)
        view = self.book.metrics(rules, self.now, Decimal("1000"))
        self.assertTrue(view["gate_pass"], view["gate_reasons"])
        self.assertGreater(Decimal(view["expectancy"]), 0)
        self.assertEqual(view["switches_live"], False)
        self.book.open_ladder(
            symbol="ETHUSDT",
            ladder=ladder().ladder,
            entry=Decimal("100"),
            exits=Exits(),
            policy=rules,
            now=self.now,
            sleeve=Decimal("1000"),
        )
        crashed = self.book.open_deals()[0]
        sl = Decimal(crashed["sl_price"])
        moment = self.now + timedelta(hours=1)
        self.book.advance(
            "ETHUSDT",
            [{"close_at": moment, "high": Decimal("100"), "low": sl - Decimal("20"), "close": sl}],
            rules,
            moment,
        )
        loss = max((Decimal("0") - Decimal(row["pnl"]) for row in self.book.closed_deals()), default=Decimal("0"))
        failed = self.book.metrics(rules, moment, loss / Decimal("0.2"))
        self.assertIn("drawdown", failed["gate_reasons"])
        self.assertGreaterEqual(Decimal(failed["max_drawdown"]), Decimal("0.08"))
        self.assertFalse(failed["switches_live"])

    def test_unknown_sleeve_fails_the_gate_instead_of_reporting_zero_drawdown(self):
        view = self.book.metrics(policy(), self.now, None)
        self.assertFalse(view["gate_pass"])
        self.assertEqual(view["gate_reasons"], ["sleeve_unset"])
        self.assertIsNone(view["max_drawdown"])

    def test_open_deal_reserves_unfilled_safety_orders(self):
        deal = self._open()
        filled = sum(Decimal(leg["quote"]) for leg in deal["legs"] if leg["filled"])
        pending = sum(Decimal(leg["quote"]) for leg in deal["legs"] if not leg["filled"])
        self.assertEqual(self.book.exposure(), filled)
        self.assertEqual(self.book.pending_safety(), pending)
        self.assertGreater(self.book.commitment(), self.book.exposure())
        self.assertGreater(pending, 0)
        blocked = approve(
            policy=policy(),
            account_usdt=self.book.commitment(),
            free_usdt=self.book.commitment(),
            warp_owned=Decimal("0"),
            warp_committed=self.book.commitment(),
            warp_pending_so=self.book.pending_safety(),
            non_warp_spent=Decimal("0"),
            non_warp_future_so=Decimal("0"),
            open_deals=1,
            open_stop_risk=self.book.stop_risk(),
            open_symbols=["SOLUSDT"],
            price=Decimal("100"),
            minimum=Decimal("5"),
            model_buy=True,
            score=score_candidate(
                regime="trend_up", rsi=Decimal("55"), quote_volume=Decimal("20000000"),
                spread=Decimal("0.001"), change_24h=Decimal("0.02"), tradable=True,
                volatility=Decimal("0.02"), policy=policy(),
            ),
            symbol="ETHUSDT",
            quote_volume=Decimal("20000000"),
            spread=Decimal("0.001"),
            change_24h=Decimal("0.02"),
            now=self.now,
        )
        self.assertNotEqual(blocked.decision, "APPROVED")


def _gate(symbol: str) -> dict:
    return {
        "tradable": True,
        "on_binance_spot": True,
        "quote_volume": Decimal("20000000"),
        "spread": Decimal("0.001"),
        "change_24h": Decimal("0.02"),
        "listed_days": 400,
        "min_notional": Decimal("5"),
        "atr_1h_pct": Decimal("0.01"),
        "quote_age": 1,
        "price": Decimal("100"),
    }


class ShadowEngineTests(unittest.TestCase):
    def _engine(self, tmp: str, *, research, market, sleeve: Decimal = Decimal("1000")):
        cfg = Settings(
            db_path=os.path.join(tmp, "warp.db"),
            mode="observe",
            sleeve_usdt=sleeve,
            sleeve_pct=Decimal("1"),
            symbols=("SOLUSDT",),
        )
        journal = OrderJournal(cfg.db_path)
        engine = Engine(cfg, _Broker(), research, journal, market)
        return engine, journal

    def test_observe_without_an_account_opens_a_paper_deal_and_not_a_broker_order(self):
        class Research:
            def research(self, symbols, _age):
                now = datetime.now(timezone.utc)
                row = _evidence(symbols[0], now)
                return [Signal(symbols[0], "BUY", Decimal("0.9"), now, "verified feed at timestamp", "Fresh momentum with volume confirmation", row)]

        class Markets:
            def scan(self, symbols, now=None):
                moment = now or datetime.now(timezone.utc)
                return {symbol: _evidence(symbol, moment) for symbol in symbols}

            def tradebook_context(self, symbols, now=None):
                return {"tape": None, "correlation": {}, "gates": {symbol: _gate(symbol) for symbol in symbols}}

        with tempfile.TemporaryDirectory() as tmp:
            engine, journal = self._engine(tmp, research=Research(), market=Markets())
            try:
                engine.cycle()
                self.assertFalse(engine.status.execution_permitted)
                self.assertEqual(engine.cfg.mode, "observe")
                deals = engine._paper().open_deals()
                self.assertEqual([row["symbol"] for row in deals], ["SOLUSDT"])
            finally:
                engine._paper().close()
                journal.close()

    def test_missing_trade_book_and_stale_signal_do_not_open(self):
        now = datetime.now(timezone.utc)

        class Research:
            def research(self, symbols, _age):
                row = _evidence(symbols[0], now)
                return [Signal(symbols[0], "BUY", Decimal("0.9"), now, "verified feed at timestamp", "Fresh momentum with volume confirmation", row)]

        class Markets:
            def scan(self, symbols, now=None):
                moment = now or datetime.now(timezone.utc)
                return {symbol: _evidence(symbol, moment) for symbol in symbols}

        with tempfile.TemporaryDirectory() as tmp:
            engine, journal = self._engine(tmp, research=Research(), market=Markets())
            try:
                engine.cycle()
                self.assertEqual(engine._paper().open_deals(), [])
                self.assertEqual(engine.paper_error, "market_data")
            finally:
                engine._paper().close()
                journal.close()

        class StaleResearch:
            def research(self, symbols, _age):
                old = now - timedelta(hours=3)
                row = _evidence(symbols[0], old)
                return [Signal(symbols[0], "BUY", Decimal("0.9"), old, "verified feed at timestamp", "Fresh momentum with volume confirmation", row)]

        class GatedMarkets(Markets):
            scanned: list[tuple[str, ...]]

            def __init__(self):
                self.scanned = []

            def scan(self, symbols, now=None):
                self.scanned.append(tuple(symbols))
                return super().scan(symbols, now=now)

            def tradebook_context(self, symbols, now=None):
                return {"tape": None, "correlation": {}, "gates": {symbol: _gate(symbol) for symbol in symbols}}

        with tempfile.TemporaryDirectory() as tmp:
            market = GatedMarkets()
            engine, journal = self._engine(tmp, research=StaleResearch(), market=market)
            try:
                engine.cycle()
                self.assertEqual(engine._paper().open_deals(), [])
            finally:
                book = engine._paper()
                book.open_ladder(
                    symbol="ETHUSDT",
                    ladder=ladder().ladder,
                    entry=Decimal("100"),
                    exits=Exits(),
                    policy=policy(),
                    now=now,
                    sleeve=Decimal("1000"),
                )
                engine.refresh_market(now)
                self.assertIn("ETHUSDT", market.scanned[-1])
                book.close()
                journal.close()


def _evidence(symbol: str, moment: datetime) -> MarketEvidence:
    return MarketEvidence(
        symbol, moment, Decimal("100"), Decimal("20000000"),
        "up", Decimal("0.02"), Decimal("55"), Decimal("0.02"), "trend_up",
        Decimal("5"), 2, True, True, "ok",
    )


class _Broker:
    def create_strategy(self, _payload):
        raise AssertionError("paper shadow must not place an order")

    def list_accounts(self):
        raise AssertionError("paper shadow must not read a write endpoint")


if __name__ == "__main__":
    unittest.main()
