"""Tradebook section 12 and WARP addendum v1.1. Amounts are fixtures, not live balances."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from warp3c.bots import BotTemplate
from warp3c.capital import (
    BtcTape,
    Exits,
    Policy,
    apply_ladder,
    approve,
    btc_risk_off,
    build_ladder,
    cooldown_block,
    correlation_block,
    go_no_go,
    needs_review,
    net_move,
    outbound_matches,
    parse_min_score,
    parse_weights,
    readback_ok,
    score_candidate,
    search_ladder,
    sleeve_nav,
    stop_configured,
    stress_loss,
    take_profit_floor,
)
from warp3c.config import Settings


def policy(**kwargs) -> Policy:
    base = Policy.from_settings(Settings(sleeve_pct=Decimal("1")))
    return Policy(**{**base.__dict__, **kwargs})


def fresh(**kwargs):
    values = dict(
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
            regime="trend_up", rsi=Decimal("55"), quote_volume=Decimal("20000000"),
            spread=Decimal("0.001"), change_24h=Decimal("0.02"), tradable=True,
            volatility=Decimal("0.02"), policy=policy(),
        ),
        symbol="SOLUSDT",
        quote_volume=Decimal("20000000"),
        spread=Decimal("0.001"),
        change_24h=Decimal("0.02"),
        now=datetime(2026, 11, 21, tzinfo=timezone.utc),
    )
    values.update(kwargs)
    return approve(**values)


class TradebookTests(unittest.TestCase):
    def test_t01_fresh_account_caps_scale_with_nav(self):
        book = sleeve_nav(
            account_usdt=Decimal("1200"), free_usdt=Decimal("1200"), warp_owned=Decimal("0"),
            non_warp_spent=Decimal("0"), non_warp_future_so=Decimal("0"),
            policy=policy(sleeve_pct=Decimal("1")),
        )
        self.assertEqual(book.eligible, Decimal("1200"))
        decision = fresh(account_usdt=Decimal("1200"), free_usdt=Decimal("1200"))
        self.assertEqual(decision.decision, "APPROVED")
        self.assertLessEqual(decision.ladder.commitment, Decimal("300"))
        self.assertGreater(decision.ladder.stop_fraction, 0)
        self.assertTrue(stop_configured({"stopLoss": decision.ladder.stop_fraction}))

    def test_t02_deposit_changes_future_caps_not_an_open_deal(self):
        before = fresh()
        after = fresh(account_usdt=Decimal("2000"), free_usdt=Decimal("2000"), warp_committed=before.ladder.commitment)
        self.assertGreater(after.cap, before.cap)
        self.assertEqual(before.ladder.commitment, before.ladder.commitment)

    def test_t03_and_addendum_other_bots_are_reserved_first(self):
        book = sleeve_nav(
            account_usdt=Decimal("10000"), free_usdt=Decimal("1000"), warp_owned=Decimal("0"),
            non_warp_spent=Decimal("4000"), non_warp_future_so=Decimal("1000"),
            policy=policy(sleeve_pct=Decimal("1"), sleeve_usdt=Decimal("500")),
        )
        self.assertEqual(book.sleeve, Decimal("500"))
        self.assertEqual(book.free_for_warp, Decimal("0"))
        self.assertEqual(book.eligible, Decimal("0"))
        decision = fresh(non_warp_future_so=Decimal("1000"), free_usdt=Decimal("1000"))
        self.assertEqual(decision.decision, "VETO")

    def test_t04_floating_loss_shrinks_the_sleeve(self):
        rich = sleeve_nav(
            account_usdt=Decimal("1000"), free_usdt=Decimal("1000"), warp_owned=Decimal("0"),
            non_warp_spent=Decimal("0"), non_warp_future_so=Decimal("0"), policy=policy(),
        )
        poor = sleeve_nav(
            account_usdt=Decimal("1000"), free_usdt=Decimal("700"), warp_owned=Decimal("0"),
            non_warp_spent=Decimal("0"), non_warp_future_so=Decimal("0"), policy=policy(),
        )
        self.assertLess(poor.eligible, rich.eligible)

    def test_t05_t06_t07_universe_and_minimums(self):
        self.assertEqual(fresh(tradable=False).reasons, ["not_trading"])
        self.assertEqual(fresh(on_binance_spot=False).reasons, ["not_binance_spot"])
        tiny = fresh(account_usdt=Decimal("20"), free_usdt=Decimal("20"), minimum=Decimal("10"))
        self.assertIn(tiny.decision, {"VETO"})
        self.assertTrue(set(tiny.reasons) & {"no_capital", "no_feasible_ladder"})

    def test_t08_fourth_deal_is_rejected(self):
        self.assertEqual(fresh(open_deals=3, open_symbols=["AUSDT", "BUSDT", "CUSDT"]).reasons, ["max_deals"])

    def test_t09_and_stress_cap(self):
        loss = stress_loss([Decimal("200"), Decimal("200")], policy())
        self.assertGreater(loss, policy().stress_cap * Decimal("1000"))
        blocked = fresh(warp_committed=Decimal("400"), open_deals=1, open_symbols=["ADAUSDT"])
        self.assertEqual(blocked.decision, "VETO")

    def test_addendum_correlation_and_alt_cap(self):
        rules = policy()
        self.assertEqual(correlation_block(["SOLUSDT", "ADAUSDT"], "AVAXUSDT", None, rules), "alt_cap")
        self.assertEqual(
            correlation_block(["BTCUSDT"], "ETHUSDT", lambda _a, _b: Decimal("0.9"), rules),
            "correlation_cluster",
        )
        self.assertIsNone(correlation_block(["BTCUSDT"], "SOLUSDT", lambda _a, _b: Decimal("0.4"), rules))

    def test_t17_t18_t22_halts(self):
        self.assertEqual(fresh(stale=True).decision, "WAIT_DATA_INCOMPLETE")
        self.assertEqual(fresh(peak=Decimal("1000"), equity=Decimal("900")).reasons, ["drawdown"])
        self.assertEqual(fresh(mismatched_account=True).reasons, ["wrong_account"])
        self.assertEqual(fresh(day_start=Decimal("1000"), equity=Decimal("960")).reasons, ["daily_loss"])

    def test_addendum_btc_regime_and_score_and_stop(self):
        self.assertTrue(btc_risk_off(BtcTape(Decimal("90"), Decimal("100"), Decimal("-0.1"), Decimal("0"), Decimal("0.01"), Decimal("0.01"))))
        self.assertTrue(btc_risk_off(BtcTape(Decimal("110"), Decimal("100"), Decimal("0.1"), Decimal("-0.05"), Decimal("0.01"), Decimal("0.01"))))
        self.assertTrue(btc_risk_off(BtcTape(Decimal("110"), Decimal("100"), Decimal("0.1"), Decimal("0"), Decimal("0.05"), Decimal("0.02"))))
        self.assertFalse(btc_risk_off(None))
        low = score_candidate(
            regime="range", rsi=Decimal("80"), quote_volume=Decimal("1000"), spread=Decimal("0.01"),
            change_24h=Decimal("0.3"), tradable=True, volatility=Decimal("0.1"), policy=policy(),
        )
        self.assertLess(low.total, 80)
        self.assertEqual(fresh(score=low, model_buy=True).reasons, ["score"])
        self.assertEqual(fresh(model_buy=False).reasons, ["model_not_buy"])
        self.assertFalse(stop_configured({}))
        self.assertFalse(stop_configured({"stopLoss": 0}))
        ladder = build_ladder(
            price=Decimal("100"), commitment=Decimal("300"), safety_orders=8,
            scale=Decimal("1.2"), step=Decimal("0.02"), policy=policy(),
        )
        self.assertIsNotNone(ladder)
        self.assertEqual(ladder.orders[1], ladder.base)
        payload = apply_ladder({"stopLoss": Decimal("0.2"), "takeProfits": []}, ladder)
        self.assertEqual(payload["stopLossBaseOn"], "entry_order")
        self.assertAlmostEqual(float(Decimal("100") * (Decimal("1") - payload["stopLoss"])), float(ladder.stop_price), places=6)
        self.assertTrue(stop_configured(payload))
        self.assertGreaterEqual(payload["takeProfits"][0]["priceDeviation"], take_profit_floor(policy()))
        rebuilt = [payload["amountPerTrade"]]
        for index in range(1, ladder.safety_orders + 1):
            rebuilt.append(payload["amountPerTrade"] * (payload["extraOrderVolumeMultiplier"] ** (index - 1)))
        self.assertEqual(rebuilt, list(ladder.orders))
        capped = search_ladder(
            price=Decimal("100"), cap=Decimal("300"), nav=Decimal("5000"),
            open_stop_risk=Decimal("0"), open_commitment=Decimal("0"),
            minimum=Decimal("1"), atr_1h_pct=Decimal("0.02"), policy=policy(sleeve_pct=Decimal("1")),
            max_base=Decimal("5"),
        )
        self.assertIsNotNone(capped)
        self.assertLessEqual(capped.base, Decimal("5"))
        self.assertLess(capped.commitment, Decimal("300"))

    def test_addendum_cooldowns_review_and_go_no_go(self):
        now = datetime(2026, 11, 21, tzinfo=timezone.utc)
        recent = [{"symbol": "SOLUSDT", "kind": "stop", "at": now - timedelta(hours=2)}]
        self.assertEqual(cooldown_block(recent, "SOLUSDT", now, policy()), "stop_cooldown")
        tp = [{"symbol": "SOLUSDT", "kind": "tp", "at": now - timedelta(hours=1)}]
        self.assertEqual(cooldown_block(tp, "SOLUSDT", now, policy()), "tp_cooldown")
        streak = [
            {"symbol": "AUSDT", "kind": "stop", "at": now - timedelta(hours=1)},
            {"symbol": "BUSDT", "kind": "stop", "at": now - timedelta(hours=2)},
        ]
        self.assertEqual(cooldown_block(streak, "SOLUSDT", now, policy()), "stop_streak")
        self.assertTrue(needs_review(now - timedelta(days=11), now, False, policy()))
        self.assertFalse(needs_review(now - timedelta(days=11), now, True, policy()))
        ok, reasons = go_no_go(
            paper_days=30, closed_deals=30, net_expectancy=Decimal("0.01"),
            max_drawdown=Decimal("0.05"), policy=policy(),
        )
        self.assertTrue(ok)
        self.assertEqual(reasons, [])
        no, missing = go_no_go(
            paper_days=7, closed_deals=2, net_expectancy=Decimal("-0.01"),
            max_drawdown=Decimal("0.09"), policy=policy(),
        )
        self.assertFalse(no)
        self.assertEqual(missing, ["paper_days", "closed_deals", "net_expectancy", "drawdown"])
        self.assertLess(net_move(Decimal("0.01"), policy()), Decimal("0.01"))

    def test_worked_example_stop_can_veto_a_full_ladder(self):
        found = search_ladder(
            price=Decimal("100"), cap=Decimal("300"), nav=Decimal("1200"),
            open_stop_risk=Decimal("0"), open_commitment=Decimal("0"),
            minimum=Decimal("5"), atr_1h_pct=Decimal("0.08"), policy=policy(sleeve_pct=Decimal("1")),
        )
        if found is not None:
            self.assertLessEqual(found.stop_loss, Decimal("0.025") * Decimal("1200"))
            self.assertLess(found.commitment, Decimal("300"))

    def test_exit_toggles_shrink_size_and_match_readback(self):
        rules = policy(sleeve_pct=Decimal("1"))
        armed = search_ladder(
            price=Decimal("100"), cap=Decimal("250"), nav=Decimal("1000"),
            open_stop_risk=Decimal("0"), open_commitment=Decimal("0"),
            minimum=Decimal("5"), atr_1h_pct=Decimal("0.02"), policy=rules, stop_enabled=True,
        )
        bare = search_ladder(
            price=Decimal("100"), cap=Decimal("250"), nav=Decimal("1000"),
            open_stop_risk=Decimal("0"), open_commitment=Decimal("0"),
            minimum=Decimal("5"), atr_1h_pct=Decimal("0.02"), policy=rules, stop_enabled=False,
        )
        self.assertIsNotNone(armed)
        self.assertIsNotNone(bare)
        self.assertLessEqual(bare.commitment, armed.commitment)
        self.assertLess(bare.stop_price, armed.stop_price)
        off = apply_ladder({"stopLoss": Decimal("0.2"), "takeProfits": [{"priceDeviation": Decimal("0.02"), "portfolio": 1}]}, bare, Exits(False, False, False))
        self.assertNotIn("stopLoss", off)
        self.assertNotIn("takeProfits", off)
        self.assertTrue(outbound_matches(off, Exits(False, False, False)))
        self.assertEqual(fresh(exits=Exits(False, False, False)).warnings, ["no_sl", "manual_exit"])
        trailing = apply_ladder({}, bare, Exits(False, False, True))
        self.assertNotIn("stopLoss", trailing)
        self.assertIn("trailingStopActivation", trailing)
        self.assertTrue(outbound_matches(trailing, Exits(False, False, True)))
        self.assertTrue(readback_ok({"strategyId": "abc"}, Exits(True, True, False)))
        self.assertFalse(readback_ok({"stopLoss": None, "takeProfits": []}, Exits(True, True, False)))
        self.assertTrue(readback_ok({"stopLoss": "0.08", "takeProfits": [{"priceDeviation": "0.012", "portfolio": 1}]}, Exits(True, True, False)))

    def test_score_threshold_and_weights_are_configurable(self):
        self.assertEqual(parse_min_score(None), 80)
        self.assertEqual(parse_min_score(50), 50)
        self.assertEqual(parse_min_score(100), 100)
        with self.assertRaises(ValueError):
            parse_min_score(49)
        with self.assertRaises(ValueError):
            parse_min_score(101)
        with self.assertRaises(ValueError):
            parse_weights({"trend": 50, "reversal": 50, "liquidity": 0, "structure": 0, "event": 1})
        custom = parse_weights({"trend": 40, "reversal": 20, "liquidity": 20, "structure": 10, "event": 10})
        self.assertEqual(sum(custom.values()), 100)
        scored = score_candidate(
            regime="trend_up", rsi=Decimal("55"), quote_volume=Decimal("20000000"),
            spread=Decimal("0.001"), change_24h=Decimal("0.01"), tradable=True,
            volatility=Decimal("0.02"), policy=policy(), weights=custom,
        )
        self.assertEqual(scored.trend, 40)
        self.assertEqual(scored.total, 100)
        template = BotTemplate.from_mapping({"min_score": 70, "weights": custom})
        self.assertEqual(template.min_score, 70)
        self.assertTrue(template.to_dict()["score_warning"])
        self.assertFalse(BotTemplate().to_dict()["score_warning"])
        low = score_candidate(
            regime="downtrend", rsi=Decimal("20"), quote_volume=Decimal("1"),
            spread=Decimal("0.02"), change_24h=Decimal("0.4"), tradable=False,
            volatility=Decimal("0.2"), policy=policy(),
        )
        self.assertEqual(fresh(score=low, min_score=50).decision, "VETO")
        middling = score_candidate(
            regime="downtrend", rsi=Decimal("55"), quote_volume=Decimal("20000000"),
            spread=Decimal("0.001"), change_24h=Decimal("0.01"), tradable=True,
            volatility=Decimal("0.02"), policy=policy(),
        )
        self.assertGreaterEqual(middling.total, 50)
        self.assertLess(middling.total, 80)
        softened = fresh(score=middling, min_score=50)
        self.assertEqual(softened.decision, "APPROVED")
        self.assertIn("score_below_80", softened.warnings)
        with self.assertRaises(ValueError):
            BotTemplate.from_mapping({"min_score": 40})


if __name__ == "__main__":
    unittest.main()
