"""Rulebook fixtures. No network. Rule ids are in the test names."""

import json
import os
import tempfile
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from warp3c.notify import enqueue_incident, flush_alerts
from warp3c.state import OrderJournal
from warp3c.telemetry import send_telegram
from warp3c.xau.alerts import SIGNAL, SUMMARY, Outbox, daily, follow_up, new_signal, ops, weekly
from warp3c.xau.basis import BasisBook
from warp3c.xau.candles import Bar, prepare
from warp3c.xau.clock import ema_entry_open, market_closed, scalp_entry_open, smc_entry_open
from warp3c.xau.config import XauConfigError, XauSettings
from warp3c.xau.engines.ema import _exhausted, ema_entry, ema_stop, evaluate_ema
from warp3c.xau.engines.scalp import evaluate_scalp
from warp3c.xau.engines.smc import (
    _l1,
    _liquidity_levels,
    _sweep_at,
    _sweep_of,
    build_setup,
    clean_session_sweep,
    evaluate_smc,
    s3_signal,
    sl_price,
    targets,
)
from warp3c.xau.execution import ExecutionDisabled, NullAdapter, OrderIntent
from warp3c.xau.feeds import HttpFeeds, parse_binance, parse_bybit, parse_dxy, parse_okx, parse_spot
from warp3c.xau.gate import GateContext, evaluate
from warp3c.xau.news import NewsBook, parse_ff
from warp3c.xau.outcome import Position, dump_tags, on_bar, position_from_row
from warp3c.xau.profiles import ProfileError, example_lot, load_profile
from warp3c.xau.riskbook import RiskBook
from warp3c.xau.runner import XauRunner, _due_bars, _h1_veto, start_xau
from warp3c.xau.stats import expectancy
from warp3c.xau.status import HUB, snapshot
from warp3c.xau.store import XauStore, new_id
from warp3c.xau.structure import (
    displacement,
    find_sweeps,
    fvgs,
    q_away,
    q_toward,
    structure_events,
    swings,
)

D = Decimal
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
PROFILE = Path(__file__).resolve().parents[1] / "src" / "warp3c" / "xau" / "profiles" / "example.toml"
ROOT = Path(__file__).resolve().parents[1]


def _bar(moment, o, h, low, c, timeframe="15m"):
    return Bar(moment, D(o), D(h), D(low), D(c), D("1"), timeframe)


def _m15(index, o, h, low, c, start=None):
    base = start or datetime(2026, 11, 25, 8, tzinfo=UTC)
    return _bar(base + timedelta(minutes=15 * index), o, h, low, c, "15m")


def _gate(**overrides):
    now = datetime(2026, 11, 26, 8, tzinfo=UTC)
    ctx = GateContext(
        window_engine="SMC",
        now=now,
        feed_fresh=True,
        basis_healthy=True,
        dirty=False,
        news_clear=True,
        risk_clear=True,
        smc_open=True,
        ema_open=True,
    )
    for key, value in overrides.items():
        object.__setattr__(ctx, key, value)
    return ctx


def _closed(model="S1", realized="-1", window="London KZ", engine="SMC", when="2026-11-20T12:00:00+00:00"):
    return {
        "sent": "1",
        "status": "SL",
        "model": model,
        "engine": engine,
        "window": window,
        "realized_r": realized,
        "risk_pct": "0.40",
        "created_at_utc": when,
        "exit_at": when,
        "filled_at": "",
    }


class ClockTests(unittest.TestCase):
    def test_k1_k5_window_edges_are_exclusive(self):
        friday = datetime(2026, 11, 27, tzinfo=UTC)
        thursday = datetime(2026, 11, 26, tzinfo=UTC)

        def at(day, hour, minute):
            # hour:minute are WIB, which is UTC+7.
            return day + timedelta(hours=hour - 7, minutes=minute)

        self.assertTrue(scalp_entry_open(at(thursday, 16, 14)))
        self.assertFalse(scalp_entry_open(at(thursday, 16, 15)))
        self.assertTrue(smc_entry_open(at(thursday, 16, 15)))
        self.assertTrue(scalp_entry_open(at(friday, 21, 44)))
        self.assertFalse(scalp_entry_open(at(friday, 21, 45)))
        self.assertTrue(scalp_entry_open(at(thursday, 22, 14)))
        self.assertFalse(scalp_entry_open(at(thursday, 22, 15)))
        self.assertTrue(smc_entry_open(at(friday, 22, 29)))
        self.assertFalse(smc_entry_open(at(friday, 22, 30)))
        self.assertTrue(ema_entry_open(at(thursday, 23, 0)))
        self.assertFalse(ema_entry_open(at(friday, 23, 0)))
        self.assertTrue(ema_entry_open(at(friday, 1, 59)))
        self.assertFalse(ema_entry_open(at(friday, 2, 0)))
        # Friday 23:00–02:00 includes Saturday 00:00–02:00 WIB.
        self.assertFalse(ema_entry_open(at(friday + timedelta(days=1), 1, 0)))

    def test_k4_dst_close_and_daily_break(self):
        self.assertTrue(market_closed(datetime(2026, 11, 27, 17, tzinfo=NY)))
        self.assertTrue(market_closed(datetime(2026, 7, 17, 17, tzinfo=NY)))
        self.assertTrue(market_closed(datetime(2026, 11, 29, 17, 59, tzinfo=NY)))
        self.assertFalse(market_closed(datetime(2026, 11, 29, 18, tzinfo=NY)))
        self.assertTrue(market_closed(datetime(2026, 11, 23, 17, 30, tzinfo=NY)))
        self.assertFalse(market_closed(datetime(2026, 11, 23, 18, tzinfo=NY)))


class StructureTests(unittest.TestCase):
    def test_d1_swing_waits_for_the_second_right_candle(self):
        bars = [
            _m15(0, 10, 11, 9, 10),
            _m15(1, 10, 12, 9, 11),
            _m15(2, 11, 20, 10, 12),
            _m15(3, 12, 13, 10, 11),
        ]
        self.assertEqual(swings(bars, 2), [])
        bars.append(_m15(4, 11, 12, 9, 10))
        found = swings(bars, 2)
        self.assertEqual(found[0].index, 2)
        self.assertEqual(found[0].confirmed_index, 4)

    def test_d3_wick_only_choch_is_rejected(self):
        bars = [
            _m15(0, 10, 11, 9, 10),
            _m15(1, 10, 12, 9, 11),
            _m15(2, 11, 20, 10, 19),
            _m15(3, 18, 19, 12, 13),
            _m15(4, 13, 14, 11, 12),
            _m15(5, 12, 21, 11, 19),
        ]
        events = structure_events(bars, 2)
        self.assertFalse(any(event.index == 5 for event in events))
        bars[-1] = _m15(5, 12, 21, 11, 20.5)
        events = structure_events(bars, 2)
        self.assertTrue(any(event.index == 5 and event.kind == "bos" for event in events))

    def test_d9_two_closes_outside_are_a_break_not_a_sweep(self):
        level = D("100")
        start = datetime(2026, 11, 25, 8, tzinfo=UTC)
        held = [
            _bar(start, 100, 100.2, 99.5, 99.2),
            _bar(start + timedelta(minutes=15), 99.2, 99.4, 99.0, 99.1),
        ]
        self.assertEqual(find_sweeps(held, level, "ssl"), [])
        swept = [_bar(start, 100.2, 100.4, 99.8, 100.3)]
        self.assertEqual(len(find_sweeps(swept, level, "ssl")), 1)

    def test_d6_fvg_under_min_size_is_rejected(self):
        start = datetime(2026, 11, 25, 8, tzinfo=UTC)
        small = [
            _bar(start, 100, 100, 99, 100),
            _bar(start + timedelta(minutes=15), 100, 101, 100, 101),
            _bar(start + timedelta(minutes=30), 100.2, 101, 100.4, 101),
        ]
        self.assertEqual(fvgs(small, D("10")), [])
        large = [
            _bar(start, 100, 100, 99, 100),
            _bar(start + timedelta(minutes=15), 101, 106, 101, 105),
            _bar(start + timedelta(minutes=30), 106, 108, 102, 107),
        ]
        found = fvgs(large, D("10"))
        self.assertEqual(len(found), 1)
        self.assertGreaterEqual(found[0].size, D("1"))

    def test_d5_displacement_without_an_fvg_is_rejected(self):
        start = datetime(2026, 11, 25, 8, tzinfo=UTC)
        gap = [
            _bar(start, 100, 101, 99, 100),
            _bar(start + timedelta(minutes=15), 101, 108, 101, 107),
            _bar(start + timedelta(minutes=30), 107, 114, 106, 113),
        ]
        self.assertEqual(displacement(gap, 1, "bullish", D("10")), (1, 2))
        no_gap = [
            _bar(start, 100, 110, 99, 100),
            _bar(start + timedelta(minutes=15), 101, 108, 101, 107),
            _bar(start + timedelta(minutes=30), 107, 114, 100, 113),
        ]
        self.assertIsNone(displacement(no_gap, 1, "bullish", D("10")))


class PricingTests(unittest.TestCase):
    def test_r04_sl_rounds_away_and_tp_rounds_toward(self):
        entry = D("3565.40")
        self.assertEqual(q_away(D("3558.059"), entry), D("3558.05"))
        self.assertEqual(q_toward(D("3580.109"), entry), D("3580.10"))

    def test_s1_positive_worked_example(self):
        entry = D("3565.40")
        atr = D("12")
        spread = D("0.30")
        sl = sl_price(D("3560"), atr, spread, "long", entry)
        self.assertEqual(sl, D("3558.05"))
        tp1, _tp2, _rr1, _rr2 = targets(entry, sl, "long", D("3620"))
        self.assertEqual(tp1, D("3580.10"))
        profile = load_profile(PROFILE)
        self.assertEqual(example_lot(D("40"), D("7.35"), profile), D("0.05"))
        setup = build_setup(
            model="S1", direction="long", entry_type="limit", entry=entry, extreme=D("3560"),
            atr_h1=atr, spread=spread, l1=D("3600"), dol_price=D("3620"),
            ote=False, clean_sweep=True, vacuum=False, fresh_reversal=False,
            bias="bullish", window="London KZ", valid_until=None, reason="S1",
            poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=D("0.40"),
        )
        self.assertIsNotNone(setup)
        self.assertEqual(setup.grade, "A+")
        self.assertEqual(setup.sl, D("3558.05"))
        self.assertEqual(setup.tp1, D("3580.10"))
        gated = evaluate(setup, _gate())
        self.assertTrue(gated.sent)
        self.assertEqual(gated.failures, ())

    def test_s1_negative_wide_stop_is_skipped(self):
        setup = build_setup(
            model="S1", direction="long", entry_type="limit", entry=D("3565.40"), extreme=D("3560"),
            atr_h1=D("4"), spread=D("0.30"), l1=D("3600"), dol_price=D("3620"),
            ote=False, clean_sweep=True, vacuum=False, fresh_reversal=False,
            bias="bullish", window="London KZ", valid_until=None, reason="wide",
            poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=D("0.40"),
        )
        self.assertIsNone(setup)

    def test_s1_negative_l1_inside_2r(self):
        setup = build_setup(
            model="S1", direction="long", entry_type="limit", entry=D("3565.40"), extreme=D("3560"),
            atr_h1=D("12"), spread=D("0.30"), l1=D("3566.40"), dol_price=D("3620"),
            ote=False, clean_sweep=True, vacuum=False, fresh_reversal=False,
            bias="bullish", window="London KZ", valid_until=None, reason="near",
            poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=D("0.40"),
        )
        self.assertIsNone(setup)

    def test_s1_negative_flat_scan_and_s3_never_generated(self):
        flat = tuple(_m15(i, 100, 101, 99, 100) for i in range(8))
        found = evaluate_smc(
            m15=flat, h1=flat, h4=flat, direction="long", atr_h1=D("10"), spread=D("0.30"),
            fresh_reversal=False, aligned=True, bias_text="range", window="London KZ",
            valid_until=None, risk_pct=D("0.40"), in_kz=True,
        )
        self.assertIsNone(found)
        self.assertIsNone(s3_signal())

    def test_s1_fresh_reversal_capped_at_a_and_market_entry_is_recorded(self):
        setup = build_setup(
            model="S1", direction="long", entry_type="market", entry=D("3565.40"), extreme=D("3560"),
            atr_h1=D("12"), spread=D("0.30"), l1=D("3600"), dol_price=D("3620"),
            ote=True, clean_sweep=True, vacuum=True, fresh_reversal=True,
            bias="bullish", window="London KZ", valid_until=None, reason="fresh",
            poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=D("0.40"),
        )
        self.assertEqual(setup.grade, "A")
        self.assertEqual(setup.entry_type, "market")
        self.assertTrue(setup.chase_ok)

    def test_s1_13_clean_sweep_is_asia_pdl_or_london_and_not_assumed(self):
        plain = build_setup(
            model="S1", direction="long", entry_type="limit", entry=D("3565.40"), extreme=D("3560"),
            atr_h1=D("12"), spread=D("0.30"), l1=D("3600"), dol_price=D("3620"),
            ote=False, clean_sweep=False, vacuum=False, fresh_reversal=False,
            bias="bullish", window="London KZ", valid_until=None, reason="plain",
            poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=D("0.40"),
        )
        self.assertEqual(plain.grade, "A")
        asia = datetime(2026, 11, 25, 0, tzinfo=UTC)  # 07:00 WIB
        london = datetime(2026, 11, 25, 7, tzinfo=UTC)  # 14:00 WIB
        prior = datetime(2026, 11, 24, 5, tzinfo=UTC)
        moment = datetime(2026, 11, 25, 8, tzinfo=UTC)  # 15:00 WIB, London KZ
        bars = (
            _bar(prior, "100", "110", "90", "100", "15m"),
            _bar(asia, "101", "102", "100", "101", "15m"),
            _bar(asia + timedelta(hours=1), "101", "102", "100.4", "101.2", "15m"),
            _bar(london, "99", "100", "95", "98", "15m"),
        )
        self.assertTrue(clean_session_sweep(D("100"), "long", bars, moment, "London KZ"))
        self.assertTrue(clean_session_sweep(D("90"), "long", bars, moment, "London KZ"))
        self.assertFalse(clean_session_sweep(D("95"), "long", bars, moment, "London KZ"))
        ny = datetime(2026, 11, 25, 14, tzinfo=UTC)  # 21:00 WIB
        self.assertTrue(clean_session_sweep(D("95"), "long", bars, ny, "NY KZ"))
        self.assertFalse(clean_session_sweep(D("80"), "long", bars, ny, "NY KZ"))

    def test_c9_market_outside_the_poi_is_a_chase(self):
        setup = build_setup(
            model="S1", direction="long", entry_type="market", entry=D("3565.40"), extreme=D("3560"),
            atr_h1=D("12"), spread=D("0.30"), l1=D("3600"), dol_price=D("3620"),
            ote=False, clean_sweep=False, vacuum=False, fresh_reversal=False,
            bias="bullish", window="London KZ", valid_until=None, reason="market",
            poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=D("0.40"),
            inside_poi=False,
        )
        self.assertFalse(setup.chase_ok)
        gated = evaluate(setup, _gate())
        self.assertEqual(gated.failures, ("C9",))
        self.assertEqual(gated.grade, "B")
        self.assertFalse(gated.sent)

    def test_s2_positive_and_two_negatives(self):
        setup = build_setup(
            model="S2", direction="long", entry_type="limit", entry=D("3565.40"), extreme=D("3560"),
            atr_h1=D("12"), spread=D("0.30"), l1=D("3600"), dol_price=D("3620"),
            ote=True, clean_sweep=False, vacuum=False, fresh_reversal=False,
            bias="bullish", window="NY KZ", valid_until=None, reason="S2",
            poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=D("0.40"),
        )
        self.assertEqual(setup.model, "S2")
        self.assertIsNone(build_setup(
            model="S2", direction="long", entry_type="limit", entry=D("3565.40"), extreme=D("3560"),
            atr_h1=D("4"), spread=D("0.30"), l1=D("3600"), dol_price=None,
            ote=False, clean_sweep=False, vacuum=False, fresh_reversal=False,
            bias="bullish", window="NY KZ", valid_until=None, reason="wide",
            poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=D("0.40"),
        ))
        self.assertIsNone(evaluate_smc(
            m15=(), h1=(), h4=(), direction="long", atr_h1=D("10"), spread=D("0.30"),
            fresh_reversal=False, aligned=False, bias_text="range", window="NY KZ",
            valid_until=None, risk_pct=D("0.40"), in_kz=False,
        ))


def _wick_world(close="124", open_="122.4", high="125", low="121"):
    """Three one-candle wick retests. The last one is the M10 confirmation."""
    count = 19
    ema50 = [D(100 + i) for i in range(count)]
    ema20 = [ema50[i] - 1 if i < 12 else ema50[i] + 4 for i in range(count)]
    bars = [_m15(i, 140, 141, 139, 140) for i in range(count)]
    bars[13] = _m15(13, "119", "121", "118", "120")
    bars[14] = _m15(14, "118.2", "120", "117.5", "119.2")
    bars[15] = _m15(15, "120.5", "122", "120", "121")
    bars[16] = _m15(16, "120.2", "122", "119.5", "121")
    bars[17] = _m15(17, "122", "124", "122", "123")
    bars[18] = _m15(18, open_, high, low, close)
    return tuple(bars), ema20, ema50


def _ema_world(confirm_close="128", confirm_open="124", confirm_high="130", confirm_low="123"):
    count = 23
    ema50 = [D(100 + i) for i in range(count)]
    ema20 = [ema50[i] - 1 if i < 12 else ema50[i] + 4 for i in range(count)]
    bars = []
    for i in range(count):
        bars.append(_m15(i, 150, 151, 149, 150))
    specials = {
        13: (120, 122, 118, 121),
        14: (118, 119, 117, 117),
        15: (117, 118, 116, 117),
        16: (119, 122, 118, 121),
        17: (123, 125, 122, 124),
        18: (122, 123, 121, 121),
        19: (122, 125, 122, 124),
        20: (126, 128, 125, 127),
        21: (125, 126, 123, 124),
        22: (confirm_open, confirm_high, confirm_low, confirm_close),
    }
    for index, prices in specials.items():
        bars[index] = _m15(index, *prices)
    return tuple(bars), ema20, ema50


class EmaTests(unittest.TestCase):
    def test_ema_tr_positive_three_candles_count_as_one_retest(self):
        bars, ema20, ema50 = _ema_world()
        setup = evaluate_ema(
            bars, spread=D("0.30"), risk_pct=D("0.50"), window="London EMA", valid_until=None,
            in_window=True, bias_text="bullish", ema20=ema20, ema50=ema50, atr=D("10"),
        )
        self.assertIsNotNone(setup)
        self.assertEqual(setup.model, "EMA_TR")
        self.assertEqual(setup.tags["retests"][0], 3)
        self.assertEqual(setup.grade, "A")
        self.assertEqual(setup.entry_type, "stop")

    def test_m11_m13_worked_example_and_lot(self):
        entry = ema_entry(D("3572.10"), "long")
        self.assertEqual(entry, D("3572.30"))
        sl = ema_stop(entry, D("3566"), D("3565.20"), D("0.30"), "long")
        self.assertEqual(sl, D("3564.00"))
        tp1 = q_toward(entry + D("1.5") * (entry - sl), entry)
        self.assertEqual(tp1, D("3584.75"))
        profile = load_profile(PROFILE)
        self.assertEqual(example_lot(D("50"), D("8.30"), profile), D("0.06"))

    def test_ema_negative_fourth_retest_rewrite_rejected(self):
        bars, ema20, ema50 = _ema_world("127", "129", "130", "126")
        setup = evaluate_ema(
            bars, spread=D("0.30"), risk_pct=D("0.50"), window="London EMA", valid_until=None,
            in_window=True, bias_text="bullish", ema20=ema20, ema50=ema50, atr=D("10"),
        )
        self.assertIsNone(setup)

    def test_ema_negative_close_below_ema50_resets(self):
        bars, ema20, ema50 = _ema_world()
        bars = list(bars)
        bars[15] = _m15(15, 112, 113, 109, 110)
        setup = evaluate_ema(
            tuple(bars), spread=D("0.30"), risk_pct=D("0.50"), window="London EMA", valid_until=None,
            in_window=True, bias_text="bullish", ema20=ema20, ema50=ema50, atr=D("10"),
        )
        self.assertIsNone(setup)

    def test_m6_m10_one_candle_wick_retest_confirms(self):
        bars, ema20, ema50 = _wick_world()
        setup = evaluate_ema(
            bars, spread=D("0.30"), risk_pct=D("0.50"), window="London EMA", valid_until=None,
            in_window=True, bias_text="bullish", ema20=ema20, ema50=ema50, atr=D("10"),
        )
        self.assertIsNotNone(setup)
        self.assertEqual(setup.tags["retests"], [1, 1, 1])
        self.assertTrue(setup.chase_ok)
        gated = evaluate(setup, _gate(window_engine="EMA"))
        self.assertNotIn("C9", gated.failures)
        self.assertTrue(gated.sent)

    def test_m10_wick_in_the_lower_half_does_not_confirm(self):
        bars, ema20, ema50 = _wick_world(close="122.2", open_="122.0", high="125", low="121")
        setup = evaluate_ema(
            bars, spread=D("0.30"), risk_pct=D("0.50"), window="London EMA", valid_until=None,
            in_window=True, bias_text="bullish", ema20=ema20, ema50=ema50, atr=D("10"),
        )
        self.assertIsNone(setup)

    def test_m2_stale_confirmation_is_not_reissued(self):
        bars, ema20, ema50 = _ema_world()
        bars = list(bars)
        bars.append(_m15(len(bars), "128.2", "130", "128", "129"))
        ema50 = list(ema50) + [D(100 + len(ema50))]
        ema20 = list(ema20) + [ema50[-1] + 4]
        setup = evaluate_ema(
            tuple(bars), spread=D("0.30"), risk_pct=D("0.50"), window="London EMA", valid_until=None,
            in_window=True, bias_text="bullish", ema20=ema20, ema50=ema50, atr=D("10"),
        )
        self.assertIsNone(setup)

    def test_k3_confirmation_outside_the_window_is_rejected(self):
        bars, ema20, ema50 = _ema_world()
        common = dict(
            spread=D("0.30"), risk_pct=D("0.50"), window="London EMA", valid_until=None,
            in_window=True, bias_text="bullish", ema20=ema20, ema50=ema50, atr=D("10"),
        )
        late = bars[-1].open_time + timedelta(minutes=30)
        self.assertIsNone(evaluate_ema(bars, window_start=late, **common))
        self.assertIsNotNone(evaluate_ema(bars, window_start=bars[0].open_time, **common))

    def test_m12_c9_chase_fails_the_gate(self):
        bars, ema20, ema50 = _ema_world("137", "130", "140", "128")
        setup = evaluate_ema(
            bars, spread=D("0.30"), risk_pct=D("0.50"), window="London EMA", valid_until=None,
            in_window=True, bias_text="bullish", ema20=ema20, ema50=ema50, atr=D("10"),
        )
        self.assertIsNotNone(setup)
        self.assertFalse(setup.chase_ok)
        gated = evaluate(setup, _gate(window_engine="EMA"))
        self.assertIn("C9", gated.failures)
        self.assertFalse(gated.sent)

    def test_m9_exhaustion_rejects(self):
        ema = [D(i * 2) for i in range(7)] + [D("12"), D("12.05"), D("12.1")]
        far = [_m15(i, 1, 2, 0, 1) for i in range(10)]
        far[6] = _m15(6, 1, 50, 0, 50)
        found = {"retests": [type("R", (), {"end": 2})(), type("R", (), {"end": 6})()], "confirm": 9}
        self.assertTrue(_exhausted(far, ema, found, D("10"), "long"))


class ScalpTests(unittest.TestCase):
    def _k1(self, close="101.20", low="99.80"):
        sweep = datetime(2026, 11, 25, 10, tzinfo=UTC)
        m5 = (_bar(sweep, 100.2, 101, low, 100.4, "5m"),)
        m1 = []
        specs = [
            (sweep - timedelta(minutes=3), 100, 100.2, 99.9, 100.1),
            (sweep - timedelta(minutes=2), 100.1, 100.6, 100, 100.4),
            (sweep - timedelta(minutes=1), 100.2, 100.4, 100, 100.2),
            (sweep, 100.1, 100.3, 100, 100.2),
            (sweep + timedelta(minutes=1), 100.2, 100.4, 100.1, 100.3),
            (sweep + timedelta(minutes=2), 100.4, 101.4, 100.3, close),
        ]
        for moment, o, h, low, c in specs:
            m1.append(_bar(moment, o, h, low, c, "1m"))
        return evaluate_scalp(
            m5=m5, m1=tuple(m1), direction="long", spread=D("0.30"), risk_pct=D("0.25"),
            window="London KZ", valid_until=None, bias_text="long", or_high=D("101"), or_low=D("100"),
            atr_m5=D("2"),
        )

    def test_k1_positive_or15_sweep(self):
        setup = self._k1()
        self.assertIsNotNone(setup)
        self.assertEqual(setup.model, "K1")
        gated = evaluate(setup, _gate())
        self.assertFalse(gated.sent)

    def test_k1_negative_no_sweep(self):
        self.assertIsNone(self._k1(low="99.95"))

    def test_k1_negative_distance_too_wide(self):
        self.assertIsNone(self._k1(close="110"))

    def test_k2_positive_and_two_negatives(self):
        start = datetime(2026, 11, 25, 10, tzinfo=UTC)
        m5 = [
            _bar(start, "97.5", "98", "97", "97.8", "5m"),
            _bar(start + timedelta(minutes=5), "98", "104", "98", "103", "5m"),
            _bar(start + timedelta(minutes=10), "100", "102", "100", "101", "5m"),
            _bar(start + timedelta(minutes=15), "101", "103", "100.5", "102", "5m"),
            _bar(start + timedelta(minutes=20), "100.2", "101.5", "98.5", "100.5", "5m"),
        ]
        m1 = [
            _bar(start, "99", "99.2", "98.8", "99", "1m"),
            _bar(start + timedelta(minutes=1), "99.2", "100.2", "99", "99.8", "1m"),
            _bar(start + timedelta(minutes=2), "99.6", "100.0", "99.4", "99.7", "1m"),
            _bar(start + timedelta(minutes=3), "99.8", "100.8", "99.6", "100.6", "1m"),
        ]
        common = dict(
            m5=tuple(m5), m1=tuple(m1), direction="long", spread=D("0.30"), risk_pct=D("0.25"),
            window="NY KZ", valid_until=None, bias_text="long", or_high=D("100"), or_low=D("90"),
            atr_m5=D("10"),
        )
        setup = evaluate_scalp(**common)
        self.assertIsNotNone(setup)
        self.assertEqual(setup.model, "K2")
        self.assertFalse(evaluate(setup, _gate()).sent)
        self.assertIsNone(evaluate_scalp(**{**common, "attempts": 2}))
        flat = list(m5)
        flat[-1] = _bar(start + timedelta(minutes=20), "99", "99.5", "98.5", "99.2", "5m")
        self.assertIsNone(evaluate_scalp(**{**common, "m5": tuple(flat)}))


class GateAndRiskTests(unittest.TestCase):
    def test_c_one_failure_is_b_and_two_are_c(self):
        setup = build_setup(
            model="S1", direction="long", entry_type="limit", entry=D("3565.40"), extreme=D("3560"),
            atr_h1=D("12"), spread=D("0.30"), l1=D("3600"), dol_price=D("3620"),
            ote=False, clean_sweep=True, vacuum=False, fresh_reversal=False,
            bias="bullish", window="London KZ", valid_until=None, reason="S1",
            poi_ok=True, liquidity_ok=True, trigger_ok=True, displacement_ok=True, risk_pct=D("0.40"),
        )
        one = evaluate(setup, _gate(news_clear=False))
        self.assertEqual(one.grade, "B")
        self.assertFalse(one.sent)
        two = evaluate(setup, _gate(news_clear=False, risk_clear=False))
        self.assertEqual(two.grade, "C")
        self.assertFalse(two.sent)

    def test_e4_one_live_signal_blocks(self):
        profile = load_profile(PROFILE)
        now = datetime(2026, 11, 26, 8, tzinfo=UTC)
        rows = [{"sent": "1", "status": "PENDING", "model": "S1", "engine": "SMC", "window": "London KZ"}]
        allowed, rule = RiskBook(rows, profile, now).allow("SMC", "S1", "London KZ")
        self.assertEqual((allowed, rule), (False, "E4"))
        shadow = [{"sent": "0", "status": "PENDING", "model": "K1", "engine": "SCALP", "window": "NY KZ"}]
        self.assertTrue(RiskBook(shadow, profile, now).allow("SMC", "S1", "London KZ")[0])
        rejected = [{"sent": 0, "status": "PENDING", "model": "S1", "engine": "SMC", "window": "London KZ"}]
        self.assertTrue(RiskBook(rejected, profile, now).allow("EMA", "EMA_TR", "London EMA")[0])

    def test_u_rules_and_m19_caps(self):
        profile = load_profile(PROFILE)
        now = datetime(2026, 11, 26, 8, tzinfo=UTC)
        book = RiskBook([_closed() for _ in range(30)], profile, now)
        self.assertEqual(book.allow("SMC", "S1", "other")[1], "U7")
        recent = "2026-11-26T01:00:00+00:00"
        losses = [_closed(realized="-1", when=recent, window="London KZ") for _ in range(2)]
        self.assertEqual(RiskBook(losses, profile, now).allow("SMC", "S1", "London KZ")[1], "U2")
        day_stop = [_closed(realized="-1.6", when=recent)]
        self.assertEqual(RiskBook(day_stop, profile, now).allow("SMC", "S1", "other")[1], "U3")
        locked = [_closed(realized="2.6", when=recent)]
        self.assertEqual(RiskBook(locked, profile, now).allow("SMC", "S1", "other")[1], "U4")
        week = [_closed(realized="-5", when="2026-11-24T12:00:00+00:00", engine="SMC")]
        self.assertEqual(RiskBook(week, profile, now).allow("SMC", "S1", "other")[1], "U6")
        streak = [_closed(realized="-1", when=recent) for _ in range(4)]
        self.assertEqual(RiskBook(streak, profile, now).allow("SMC", "S1", "other")[1], "U5")
        filled = []
        for _ in range(4):
            row = _closed(realized="0.1", when=recent, window="London KZ")
            row["status"] = "TP2"
            row["filled_at"] = recent
            filled.append(row)
        self.assertEqual(RiskBook(filled, profile, now).allow("SMC", "S1", "London KZ")[1], "U1")
        ema = []
        for _ in range(2):
            row = _closed(realized="0.2", when=recent, engine="EMA", model="EMA_TR")
            row["status"] = "TP2"
            ema.append(row)
        self.assertEqual(RiskBook(ema, profile, now).allow("EMA", "EMA_TR", "other")[1], "M19")

    def test_u7_expectancy_arithmetic(self):
        self.assertEqual(expectancy([D("2"), D("-1")]), D("0.5"))
        self.assertEqual(expectancy([D("1"), D("1"), D("-1"), D("-1")]), D("0"))


class OutcomeTests(unittest.TestCase):
    def _pos(self, **overrides):
        pos = Position("long", "SMC", D("100"), D("98"), D("104"), D("110"), D("0.30"))
        for key, value in overrides.items():
            setattr(pos, key, value)
        return pos

    def _bar(self, minute, o, h, low, c):
        return _bar(datetime(2026, 11, 26, 8, minute, tzinfo=UTC), o, h, low, c, "1m")

    def test_f1_limit_fill_and_no_fill(self):
        filled = on_bar(self._pos(), self._bar(0, 100, 100.5, 99.8, 100.2))
        self.assertEqual(filled.status, "FILLED")
        self.assertEqual(filled.fill_price, D("100"))
        waiting = on_bar(self._pos(), self._bar(0, 100.2, 100.6, 99.9, 100.4))
        self.assertEqual(waiting.status, "PENDING")

    def test_f1_stop_fill_and_gap_through(self):
        stop = self._pos(entry_type="stop")
        filled = on_bar(stop, self._bar(0, 99.5, 100.5, 99.4, 100.1))
        self.assertEqual(filled.fill_price, D("100"))
        gapped = on_bar(self._pos(entry_type="stop"), self._bar(0, 101, 101.4, 100.8, 101.2))
        self.assertEqual(gapped.fill_price, D("101"))

    def test_f3_same_bar_takes_the_stop(self):
        done = on_bar(self._pos(), self._bar(0, 100, 100.4, 98, 99))
        self.assertEqual(done.status, "SL")
        self.assertEqual(done.events[0], "FILLED")

    def test_sr2_tp1_then_be_and_sr3_trail(self):
        pos = on_bar(self._pos(), self._bar(0, 100, 100.5, 99.8, 100.2))
        pos = on_bar(pos, self._bar(1, 101, 105, 100.5, 104.5))
        self.assertEqual(pos.status, "TP1")
        self.assertEqual(pos.sl, D("101"))
        stopped = on_bar(pos, self._bar(2, 103, 103, 100.5, 101))
        self.assertEqual(stopped.status, "BE")
        runner = on_bar(self._pos(), self._bar(0, 100, 100.5, 99.8, 100.2))
        runner = on_bar(runner, self._bar(1, 101, 105, 100.5, 104.5))
        managed = on_bar(
            runner, self._bar(3, 106, 109, 105, 106),
            m15_close=self._bar(3, 106, 109, 105, 106), swing_extreme=D("107"),
        )
        self.assertEqual(managed.status, "TRAIL")
        self.assertGreater(managed.sl, D("101"))

    def test_sr4_time_stop_ema_runner_and_news_cut(self):
        filled = on_bar(self._pos(), self._bar(0, 100, 100.5, 99.8, 100.2))
        filled = on_bar(filled, self._bar(1, 101, 105, 100.5, 104.5))
        later = _bar(datetime(2026, 11, 26, 12, tzinfo=UTC), 101.3, 101.5, 101.2, 101.3, "15m")
        stopped = on_bar(filled, later, m15_close=later, lost_delivery=True)
        self.assertEqual(stopped.status, "TIME")
        ema = self._pos(engine="EMA")
        ema = on_bar(ema, self._bar(0, 100, 100.5, 99.8, 100.2))
        ema = on_bar(ema, self._bar(1, 101, 105, 100.5, 104.5))
        exit_bar = self._bar(2, 104, 104.4, 103, 103.2)
        done = on_bar(ema, exit_bar, m15_close=exit_bar, ema50=D("103.5"))
        self.assertEqual(done.status, "RUNNER_EMA50")
        fresh = on_bar(self._pos(engine="EMA"), self._bar(0, 100, 100.5, 99.8, 100.2))
        cut = on_bar(fresh, self._bar(1, 100.4, 100.6, 100.2, 100.5), news_cut=True)
        self.assertEqual(cut.status, "NEWS_CUT")
        smc = on_bar(self._pos(), self._bar(0, 100, 100.5, 99.8, 100.2))
        partial = on_bar(smc, self._bar(1, 100.4, 100.6, 100.2, 100.5), news_cut=True)
        self.assertEqual(partial.remaining, D("0.5"))
        key = "2026-11-26T08:30:00+00:00"
        fresh_smc = on_bar(self._pos(), self._bar(0, 100, 100.5, 99.8, 100.2))
        once = on_bar(fresh_smc, self._bar(1, 100.4, 100.6, 100.2, 100.5), news_cut=True, news_key=key)
        again = on_bar(once, self._bar(2, 100.4, 100.6, 100.2, 100.5), news_cut=True, news_key=key)
        self.assertEqual(again.remaining, D("0.5"))
        self.assertEqual(again.status, "FILLED")
        restored = position_from_row({
            "direction": "long", "engine": "SMC", "entry": "100", "sl": "98", "tp1": "104",
            "tp2": "110", "spread_assumed": "0.30", "status": "FILLED", "fill_price": "100",
            "entry_type": "limit", "realized_r": str(once.realized_r), "tags_json": dump_tags(once, {}),
        })
        held = on_bar(restored, self._bar(3, 100.4, 100.6, 100.2, 100.5), news_cut=True, news_key=key)
        self.assertEqual(held.remaining, D("0.5"))

    def test_restart_resumes_from_tags(self):
        pos = self._pos(status="TP1", fill_price=D("100"), tp1_done=True, remaining=D("0.5"))
        row = {
            "direction": "long", "engine": "SMC", "entry": "100", "sl": "101", "tp1": "104",
            "tp2": "110", "spread_assumed": "0.30", "status": "TP1", "fill_price": "100",
            "entry_type": "limit", "realized_r": "0.5", "tags_json": dump_tags(pos, {}),
        }
        again = position_from_row(row)
        self.assertTrue(again.tp1_done)
        self.assertEqual(again.remaining, D("0.5"))


class AlertTests(unittest.TestCase):
    def _signal(self, reason="S1 Judas sweep through a killzone POI", id6="ABC123"):
        return {
            "direction": "long", "entry_type": "limit", "grade": "A+", "id6": id6,
            "model": "S1", "window": "London KZ", "confidence": 85,
            "entry": "3565.40", "sl": "3558.05", "r_usd": "7.35",
            "tp1": "3580.10", "rr_tp1": "2.00", "tp2": "3620.00", "rr_tp2": "7.43",
            "risk_pct": "0.40", "lot": "0.05", "ref": "10000", "valid_wib": "16:45",
            "reason": reason, "news_wib": "20:00",
        }

    def test_every_text_starts_with_xau_and_fits_400(self):
        samples = [
            new_signal(self._signal()),
            new_signal(self._signal(reason="x" * 200)),
            follow_up("TP1", "ABC123", "3580.10", "1.00", "TP1"),
            follow_up("SL", "ABC123", "3558.05", "-1.00", "SL"),
            daily("2026-11-26", "0.40", 2, 1),
            weekly("2026-W48", "1.20", 4),
            ops("feed_unavailable", "3", "binance,bybit,okx"),
            ops("stalled", "4", "heartbeat late"),
            ops("config_error", "2026-11-26", "XauConfigError"),
        ]
        for event, detail in samples:
            self.assertTrue(event.startswith("XAU "))
            self.assertLessEqual(len(f"{event}: {detail}"), 400)
        event, detail = new_signal(self._signal(reason="x" * 200))
        self.assertIn("…", detail)

    def test_events_are_unique_and_the_cap_drops_summaries_first(self):
        first, _ = new_signal(self._signal(id6="ABC123"))
        second, _ = new_signal(self._signal(id6="ZZZ999"))
        self.assertNotEqual(first, second)
        one, _ = ops("stalled", "1")
        two, _ = ops("stalled", "2")
        self.assertNotEqual(one, two)
        now = datetime(2026, 11, 26, 8, tzinfo=UTC)
        box = Outbox(lambda _event, _detail: True, cap=1)
        dropped = box.submit_batch(now, [
            (SUMMARY, "XAU daily 2026-11-26", "sent 1"),
            (SIGNAL, first, "detail"),
        ])
        self.assertEqual(dropped, ["XAU daily 2026-11-26"])

    def test_xau_source_has_no_token_and_shares_the_outbox(self):
        blob = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "warp3c" / "xau").rglob("*.py"))
        self.assertNotIn("api.telegram.org", blob)
        self.assertNotRegex(blob, r"\d{8,}:[A-Za-z0-9_-]{30,}")
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content.decode()))
            return httpx.Response(200, json={"ok": True})

        with tempfile.TemporaryDirectory() as tmp:
            journal = OrderJournal(os.path.join(tmp, "warp.db"))
            self.addCleanup(journal.close)
            now = datetime(2026, 11, 26, 8, tzinfo=UTC)
            dca_event = "deal_closed:SOLUSDT:abc"
            dca_detail = "realized 1.5 fee 0.5 status completed"
            journal.enqueue_alert(dca_event, dca_detail, now=now)
            event, detail = follow_up("TP1", "ABC123", "3580.10", "1.00", "TP1")
            self.assertTrue(enqueue_incident(journal, event, detail, now=now, channel=True))
            transport = httpx.MockTransport(handler)

            def deliver(name, text):
                return send_telegram("test-token", "1", name, text, transport=transport)

            self.assertEqual(flush_alerts(journal, deliver, now=now), 2)
        texts = [item["text"] for item in seen]
        self.assertIn(f"{dca_event}: {dca_detail}", texts)
        self.assertTrue(any(text.startswith("XAU TP1 #ABC123:") for text in texts))


class FeedTests(unittest.TestCase):
    def test_parsers_keep_source_order_and_drop_the_okx_open_bar(self):
        older = 1_700_000_000_000
        newer = older + 60_000
        binance = parse_binance(
            [[older, "1", "2", "0.5", "1.5", "9"], [newer, "1.5", "2", "1", "1.8", "8"]], "1m"
        )
        self.assertLess(binance[0].open_time, binance[1].open_time)
        bybit = parse_bybit(
            {"result": {"list": [[newer, "1.5", "2", "1", "1.8", "8"], [older, "1", "2", "0.5", "1.5", "9"]]}},
            "1m",
        )
        self.assertLess(bybit[0].open_time, bybit[1].open_time)
        okx = parse_okx(
            {"data": [
                [newer, "1.5", "2", "1", "1.8", "8", "0", "0", "0"],
                [older, "1", "2", "0.5", "1.5", "9", "0", "0", "1"],
            ]},
            "1m",
        )
        self.assertEqual(len(okx), 1)
        self.assertEqual(okx[0].close, D("1.5"))
        self.assertEqual(parse_spot({"data": [{"s": "OANDA:XAUUSD", "d": [2650.5]}]}), D("2650.5"))
        self.assertEqual(parse_dxy({"chart": {"result": [{"meta": {"regularMarketPrice": 104.2}}]}}), "DXY 104.2")

    def test_prepare_drops_open_weekend_and_stale_m5(self):
        now = datetime(2026, 11, 25, 15, tzinfo=UTC)
        closed = _bar(now - timedelta(minutes=5), 1, 2, 0.5, 1.5, "1m")
        still_open = _bar(now - timedelta(seconds=30), 1, 2, 0.5, 1.5, "1m")
        weekend = _bar(datetime(2026, 11, 27, 22, 30, tzinfo=UTC), 1, 2, 0.5, 1.5, "1m")
        book = prepare({"1m": [closed, still_open, weekend]}, now)
        self.assertEqual(book.bars["1m"], (closed,))
        stale = prepare({"5m": [_bar(now - timedelta(minutes=20), 1, 2, 0.5, 1.5, "5m")]}, now)
        self.assertTrue(stale.stale_m5)
        fresh = prepare({"5m": [_bar(now - timedelta(minutes=10), 1, 2, 0.5, 1.5, "5m")]}, now)
        self.assertFalse(fresh.stale_m5)

    def test_prepare_treats_weekend_and_the_daily_break_as_continuous(self):
        start = datetime(2026, 11, 20, 14, tzinfo=UTC)  # Friday
        end = datetime(2026, 11, 23, 20, tzinfo=UTC)  # Monday
        bars = []
        cursor = start
        while cursor <= end:
            bars.append(_bar(cursor, "1", "2", "0.5", "1.5", "1h"))
            cursor += timedelta(hours=1)
        now = end + timedelta(hours=2)
        self.assertEqual(prepare({"1h": bars}, now).gaps, ())
        missing = datetime(2026, 11, 23, 15, tzinfo=UTC)
        holed = [bar for bar in bars if bar.open_time != missing]
        self.assertEqual(prepare({"1h": holed}, now).gaps, ("1h",))
        created = datetime(2026, 11, 23, 16, tzinfo=UTC)
        due = _due_bars(bars, None, created)
        self.assertTrue(due)
        self.assertGreaterEqual(due[0].open_time, created)

    def test_fallback_uses_bybit_when_binance_fails(self):
        older = 1_700_000_000_000
        newer = older + 60_000
        payload = {"result": {"list": [[newer, "1", "2", "0.5", "1.5", "3"], [older, "1", "2", "0.5", "1.4", "3"]]}}

        def transport(base):
            def handler(_request):
                if "bybit" in base:
                    return httpx.Response(200, json=payload)
                return httpx.Response(500)
            return httpx.MockTransport(handler)

        feeds = HttpFeeds("binance", ("bybit", "okx"), transport=transport)
        self.addCleanup(feeds.close)
        name, bars = feeds.book()
        self.assertEqual(name, "bybit")
        self.assertLess(bars["1m"][0].open_time, bars["1m"][1].open_time)


class BasisAndNewsTests(unittest.TestCase):
    def test_basis_healthy_and_unhealthy_bounds(self):
        book = BasisBook(D("15"))
        moment = datetime(2026, 11, 26, 8, tzinfo=UTC)
        for index in range(9):
            state = book.observe(D("100"), D("99"), moment + timedelta(minutes=index))
        self.assertEqual(state.reason, "few_samples")
        state = book.observe(D("100"), D("99"), moment + timedelta(minutes=9))
        self.assertTrue(state.healthy)
        self.assertEqual(state.value, D("1"))
        wide = BasisBook(D("15"))
        for index in range(10):
            state = wide.observe(D("120"), D("100"), moment + timedelta(minutes=index))
        self.assertEqual(state.reason, "basis_wide")
        noisy = BasisBook(D("15"))
        for index in range(10):
            perp = D("100") if index % 2 == 0 else D("104")
            state = noisy.observe(perp, D("100"), moment + timedelta(minutes=index))
        self.assertEqual(state.reason, "basis_noisy")

    def test_n2_n3_n6_and_ff_offsets(self):
        parsed = parse_ff([
            {"title": "CPI y/y", "country": "USD", "date": "2026-12-10T08:30:00-05:00", "impact": "High"},
        ])
        self.assertEqual(parsed[0].at.utcoffset(), timedelta(hours=-5))
        book = NewsBook()
        release = datetime(2026, 11, 26, 8, tzinfo=UTC)
        book.note_fetch(release - timedelta(hours=1), [
            {"title": "FOMC", "country": "USD", "date": release.isoformat(), "impact": "High"},
        ])
        self.assertIsNotNone(book.in_window(release - timedelta(minutes=20), "SMC"))
        self.assertIsNone(book.in_window(release - timedelta(minutes=40), "SMC"))
        self.assertIsNotNone(book.in_window(release + timedelta(minutes=10), "EMA"))
        self.assertIsNone(book.in_window(release + timedelta(minutes=18), "EMA"))
        self.assertIsNotNone(book.in_window(release + timedelta(minutes=18), "SMC"))
        self.assertTrue(book.cancel_pending(release - timedelta(minutes=25), release + timedelta(hours=1)))
        self.assertFalse(book.cancel_pending(release - timedelta(hours=2), release + timedelta(hours=1)))
        born_after = release + timedelta(hours=1)
        self.assertFalse(book.cancel_pending(release + timedelta(hours=3), release + timedelta(hours=4), born_after))
        self.assertTrue(
            book.cancel_pending(release - timedelta(minutes=25), release + timedelta(hours=1), release - timedelta(hours=2))
        )
        stale_release = NewsBook()
        yesterday = release - timedelta(days=1)
        stale_release.note_fetch(release, [
            {"title": "GDP", "country": "USD", "date": yesterday.isoformat(), "impact": "High"},
        ])
        self.assertFalse(
            stale_release.cancel_pending(release, release + timedelta(hours=2), release - timedelta(minutes=30))
        )
        self.assertTrue(
            stale_release.cancel_pending(
                yesterday - timedelta(minutes=20),
                yesterday + timedelta(hours=1),
                yesterday - timedelta(hours=2),
            )
        )
        stale = NewsBook()
        stale.note_fetch(datetime(2026, 11, 24, tzinfo=UTC), [])
        self.assertTrue(stale.view(datetime(2026, 11, 26, tzinfo=UTC)).blocked)
        fresh = NewsBook()
        fresh.note_fetch(datetime(2026, 11, 26, 1, tzinfo=UTC), [])
        self.assertFalse(fresh.view(datetime(2026, 11, 26, 2, tzinfo=UTC)).blocked)

    def test_n2_scalp_window_n5_rearm_and_embedded_calendar(self):
        from warp3c.xau.news import _STATIC_TOML, load_static_text, parse_static

        self.assertEqual(_STATIC_TOML.strip(), load_static_text().strip())
        self.assertGreater(len(parse_static(_STATIC_TOML)[2]), 0)
        book = NewsBook()
        moment = datetime(2026, 11, 26, 14, tzinfo=UTC)  # 21:00 WIB, inside the NY scalp window
        book.note_fetch(moment, [
            {"title": "CPI", "country": "USD", "date": "2026-11-26T21:30:00+07:00", "impact": "High"},
        ])
        self.assertTrue(book.scalp_window_blocked(moment))
        clear = NewsBook()
        clear.note_fetch(moment, [])
        self.assertFalse(clear.scalp_window_blocked(moment))
        release = datetime(2026, 11, 26, 8, tzinfo=UTC)
        book.note_fetch(release, [
            {"title": "FOMC", "country": "USD", "date": release.isoformat(), "impact": "High"},
        ])
        after = release + timedelta(minutes=30)
        self.assertTrue(book.smc_rearm_needed(after, None))
        self.assertFalse(book.smc_rearm_needed(after, after))


class StoreAndStatusTests(unittest.TestCase):
    def test_x2_tables_are_idempotent_and_leave_warp_tables_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "warp.db")
            journal = OrderJournal(path)
            self.addCleanup(journal.close)
            before = journal.db.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'xau_%' ORDER BY name"
            ).fetchall()
            store = XauStore(path)
            self.addCleanup(store.close)
            store.migrate()
            store._migrated = False
            store.migrate()
            after = journal.db.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'xau_%' ORDER BY name"
            ).fetchall()
            self.assertEqual(before, after)
            names = {row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name in (
                "xau_signals", "xau_signal_events", "xau_decisions", "xau_windows",
                "xau_news", "xau_feed_health", "xau_meta",
            ):
                self.assertIn(name, names)
            self.assertEqual(store.meta_get("schema_version"), "1")
        source = (ROOT / "src" / "warp3c" / "xau" / "store.py").read_text(encoding="utf-8")
        import re
        names = re.findall(
            r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+([A-Za-z_][A-Za-z0-9_]*)",
            source,
        )
        for name in names:
            if name.lower() in {"set", "if", "exists", "excluded"}:
                continue
            self.assertTrue(name.lower().startswith("xau_"), name)

    def test_ulid_is_26_crockford_chars(self):
        value = new_id(datetime(2026, 11, 26, tzinfo=UTC))
        self.assertEqual(len(value), 26)
        self.assertNotRegex(value, r"[ILOU]")

    def test_invalid_cycle_seconds_raise_xau_config_error(self):
        with self.assertRaises(XauConfigError):
            XauSettings(cycle_seconds=1)

    def test_config_error_thread_reports_and_returns(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(
            os.environ, {"WARP_XAU_CYCLE_SECONDS": "1"}, clear=False
        ):
            runner = start_xau(os.path.join(tmp, "warp.db"), lambda _e, _d: True)
            self.addCleanup(lambda: runner.stop(0.5))
            deadline = time.time() + 2
            while time.time() < deadline and snapshot().get("state") != "config_error":
                time.sleep(0.05)
            self.assertEqual(snapshot()["state"], "config_error")

    def test_only_the_adapter_can_place_and_it_refuses(self):
        adapter = NullAdapter()
        intent = OrderIntent("XAUUSD", "buy", "0.01")
        for method in (adapter.place, adapter.order_send, adapter.open_trade):
            with self.assertRaises(ExecutionDisabled):
                method(intent)
        blob = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "src" / "warp3c" / "xau").rglob("*.py")
            if path.name != "execution.py"
        )
        self.assertNotIn("order_send", blob)
        self.assertNotIn("open_trade", blob)

    def test_unrecognized_flag_warns_once_and_does_not_raise(self):
        import warp3c.config as config

        config._XAU_FLAG_WARNED = False
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(
            os.environ, {"WARP_DB_PATH": os.path.join(tmp, "warp.db"), "WARP_XAU_ENABLED": "true"}, clear=False
        ), self.assertLogs("warp3c", level="INFO") as logs:
            from warp3c.config import Settings
            cfg = Settings.from_env()
            Settings.from_env()
        self.assertFalse(cfg.xau_enabled)
        self.assertEqual(sum("xau_config_warning" in line for line in logs.output), 1)

    def test_profile_validation_errors(self):
        text = PROFILE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.toml"
            missing.write_text("\n".join(line for line in text.splitlines() if not line.startswith("name ")), encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_profile(missing)
            bad_lot = Path(tmp) / "lot.toml"
            bad_lot.write_text(text.replace('lot_min = "0.01"', 'lot_min = "0"'), encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_profile(bad_lot)
            bad_tz = Path(tmp) / "tz.toml"
            bad_tz.write_text(text.replace("America/New_York", "Not/AZone"), encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_profile(bad_tz)


class StatusBlockTests(unittest.TestCase):
    def test_status_key_when_disabled_running_and_unavailable(self):
        from test_failures import ExplodingBroker, Markets, Research

        from warp3c.config import Settings
        from warp3c.engine import Engine
        from warp3c.operator import SNAPSHOT, publish

        HUB.publish({"enabled": False, "state": "disabled"})
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"))
            journal = OrderJournal(cfg.db_path)
            self.addCleanup(journal.close)
            engine = Engine(cfg, ExplodingBroker(), Research(), journal, Markets())
            publish(cfg, journal, engine)
            keys = {
                "alive", "mode", "shared_account", "reason", "market_data_fresh",
                "dependencies_healthy", "risk_permitted", "execution_permitted", "opened",
                "universe", "halt", "pnl_today", "equity", "providers", "research",
                "pipeline", "published_at", "risk_decisions", "xau", "lanes",
            }
            self.assertEqual(set(SNAPSHOT["/api/status"]), keys)
            self.assertEqual(SNAPSHOT["/api/status"]["xau"], {"enabled": False, "state": "disabled"})

            def quiet():
                return None

            runner = start_xau(cfg.db_path, lambda _e, _d: True, cycle=quiet)
            self.addCleanup(lambda: runner.stop(0.5))
            deadline = time.time() + 2
            while time.time() < deadline and snapshot().get("state") != "running":
                time.sleep(0.05)
            publish(cfg, journal, engine)
            block = SNAPSHOT["/api/status"]["xau"]
            self.assertTrue(block["enabled"])
            self.assertEqual(block["state"], "running")
            for name in ("heartbeat_at", "window", "today", "basis_healthy", "halt", "rules_version"):
                self.assertIn(name, block)
            self.assertEqual(set(SNAPSHOT["/api/status"]), keys)

            def broken():
                raise RuntimeError("status down")

            with unittest.mock.patch("warp3c.xau.status.snapshot", side_effect=broken):
                publish(cfg, journal, engine)
            self.assertEqual(SNAPSHOT["/api/status"]["xau"], {"enabled": False, "state": "unavailable"})
            self.assertEqual(set(SNAPSHOT["/api/status"]), keys)


def _px(moment, o, h, low, c, timeframe="15m"):
    return _bar(moment, o, h, low, c, timeframe)


def _h1_poi():
    """Unmitigated bullish H1 FVG at 108–116 and an unswept swing high at 150."""
    origin = datetime(2026, 11, 25, tzinfo=UTC)
    spec = [
        ("100", "108", "99", "107"),
        ("107", "110", "106", "109"),
        ("116.20", "150", "116", "140"),
        ("140", "142", "109", "135"),
        ("135", "138", "120", "132"),
    ]
    return tuple(
        _px(origin + timedelta(hours=index), o, h, low, c, "1h")
        for index, (o, h, low, c) in enumerate(spec)
    )


def _killzone_m15(rows):
    """Nine Asia bars from 00:00 UTC, then four bars from the London open (07:00 UTC)."""
    origin = datetime(2026, 11, 25, tzinfo=UTC)
    london = datetime(2026, 11, 25, 7, tzinfo=UTC)
    stamps = [origin + timedelta(minutes=15 * index) for index in range(9)]
    stamps += [london + timedelta(minutes=15 * index) for index in range(4)]
    return tuple(_px(stamp, *row) for stamp, row in zip(stamps, rows, strict=True))


def _limit_rows(target="140"):
    # Asia low is 100. The 110 swing is the CHoCH swing and is only broken by $0.05.
    # The far high is the first unswept target. The sweep reclaims on the next candle.
    return [
        ("102", "103", "100.00", "102"),
        ("102", "104", "100.50", "103"),
        ("103", target, "100.50", "104"),
        ("104", "106", "100.50", "103"),
        ("103", "105", "100.50", "104"),
        ("104", "106", "100.50", "103"),
        ("103", "110", "100.50", "104"),
        ("104", "107", "100.50", "103"),
        ("103", "106", "100.50", "104"),
        ("104", "109", "99.80", "99.90"),
        ("100.20", "108", "99.95", "107"),
        ("107", "109.80", "106.80", "109.50"),
        ("110.00", "110.05", "110.00", "110.05"),
    ]


def _market_rows(target="140", close="112", high="112.40"):
    # Displacement FVG sits below the POI, so the only legal entry is the market close.
    return [
        ("102", "103", "102.20", "102.40"),
        ("102", "104", "102.50", "103"),
        ("103", target, "102.50", "104"),
        ("104", "106", "102.50", "103"),
        ("103", "105", "102.50", "104"),
        ("104", "106", "102.50", "103"),
        ("103", "110", "102.50", "104"),
        ("104", "107", "102.50", "103"),
        ("103", "106", "102.50", "104"),
        ("104", "111", "102.00", "102.05"),
        ("102.30", "104", "102.10", "103.80"),
        ("103.80", "108.50", "103.50", "108"),
        ("106.20", high, "106", close),
    ]


def _scan(m15, window="London KZ"):
    return evaluate_smc(
        m15=tuple(m15), h1=_h1_poi(), h4=(), direction="long", atr_h1=D("10"),
        spread=D("0.30"), fresh_reversal=False, aligned=True, bias_text="bullish",
        window=window, valid_until=None, risk_pct=D("0.40"), in_kz=True,
    )


def _flat(timeframe):
    origin = datetime(2026, 11, 20, tzinfo=UTC)
    step = timedelta(days=1) if timeframe == "1d" else timedelta(hours=4)
    return tuple(_bar(origin + step * index, "100", "101", "99", "100", timeframe) for index in range(4))


def _timed(spec, timeframe, step):
    origin = datetime(2026, 11, 25, 8, tzinfo=UTC)
    return tuple(
        _bar(origin + step * index, *row, timeframe) for index, row in enumerate(spec)
    )


class P2FollowupTests(unittest.TestCase):
    def test_s1_10_s1_7_d9_limit_at_the_fvg_skips_the_choch_swing(self):
        setup = _scan(_killzone_m15(_limit_rows()))
        self.assertIsNotNone(setup)
        self.assertEqual(setup.model, "S1")
        self.assertEqual(setup.entry_type, "limit")
        self.assertEqual(setup.entry, D("109.00"))
        self.assertTrue(setup.chase_ok)
        self.assertTrue(setup.tags["clean_sweep"])
        # The CHoCH only cleared 110 by $0.05, so that swing is still inside 2R of 109.
        # Keeping it as L1 would skip the setup. The unswept 140 high is the target.
        self.assertGreater(setup.tp1, setup.entry)

    def test_s1_10_nearer_unswept_target_is_still_inside_2r(self):
        # 115 is unswept and is not the CHoCH swing. It is still inside 2R, so no signal.
        self.assertIsNone(_scan(_killzone_m15(_limit_rows(target="115"))))

    def test_s1_7_market_only_when_no_limit_and_still_2r_checked(self):
        market = _scan(_killzone_m15(_market_rows()))
        self.assertIsNotNone(market)
        self.assertEqual(market.entry_type, "market")
        self.assertEqual(market.entry, D("112.00"))
        self.assertTrue(market.chase_ok)
        self.assertIsNone(_scan(_killzone_m15(_market_rows(target="120"))))
        outside = _scan(_killzone_m15(_market_rows(close="120", high="120.50")))
        self.assertIsNone(outside)

    def test_s1_10_l1_skips_swept_swings_and_the_choch_level(self):
        origin = datetime(2026, 11, 25, 8, tzinfo=UTC)
        highs = [
            "100", "101", "140", "102", "103", "104", "120", "105", "106",
            "120.20", "120.20", "107", "108", "110", "108", "109", "110.05",
        ]
        bars = tuple(
            _px(origin + timedelta(minutes=15 * index), "100", high, "99", "110.05" if index == 16 else "100")
            for index, high in enumerate(highs)
        )
        series = [(point, bars) for point in swings(bars, 2)]
        self.assertEqual(_l1(D("109"), "long", series, None), D("110"))
        self.assertEqual(_l1(D("109"), "long", series, None, broken=D("110")), D("140"))
        self.assertEqual(_l1(D("109"), "long", series, D("130"), broken=D("110")), D("130"))
        lows = [
            "120", "119", "80", "118", "117", "116", "100", "115", "114",
            "99.80", "99.80", "113", "112", "110", "112", "111", "109.95",
        ]
        short = tuple(
            _px(origin + timedelta(minutes=15 * index), "110", "121", low, "109.95" if index == 16 else "110")
            for index, low in enumerate(lows)
        )
        mirrored = [(point, short) for point in swings(short, 2)]
        self.assertEqual(_l1(D("111"), "short", mirrored, None), D("110"))
        self.assertEqual(_l1(D("111"), "short", mirrored, None, broken=D("110")), D("80"))

    def test_e2_h1_veto_mirrors_m15_bias(self):
        bearish_m15 = _timed(
            [
                ("98", "100", "95", "97"),
                ("97", "101", "94", "96"),
                ("96", "102", "90", "95"),
                ("95", "101", "94", "94"),
                ("94", "100", "95", "93"),
                ("93", "95", "88", "89"),
                ("89", "110", "87", "90"),
                ("90", "100", "86", "91"),
                ("91", "99", "85", "92"),
                ("92", "98", "90", "95"),
            ],
            "15m",
            timedelta(minutes=15),
        )
        bullish_m15 = _timed(
            [
                ("90", "95", "88", "94"),
                ("94", "96", "89", "95"),
                ("95", "110", "90", "100"),
                ("100", "105", "94", "99"),
                ("99", "104", "95", "98"),
                ("98", "112", "96", "111"),
                ("111", "112", "80", "100"),
                ("100", "111", "84", "105"),
                ("105", "111", "85", "106"),
                ("106", "112", "90", "108"),
            ],
            "15m",
            timedelta(minutes=15),
        )
        bullish_h1 = _timed(
            [
                ("10", "12", "9", "11"),
                ("11", "13", "10", "12"),
                ("12", "20", "11", "13"),
                ("13", "15", "12", "14"),
                ("14", "14", "11", "12"),
                ("12", "22", "12", "21"),
                ("21", "23", "20", "22"),
                ("22", "24", "21", "23"),
                ("23", "25", "22", "24"),
            ],
            "1h",
            timedelta(hours=1),
        )
        bearish_h1 = _timed(
            [
                ("20", "21", "18", "19"),
                ("19", "20", "16", "17"),
                ("17", "18", "10", "16"),
                ("16", "17", "14", "15"),
                ("15", "16", "13", "14"),
                ("14", "14", "8", "9"),
                ("9", "12", "8", "11"),
                ("11", "13", "9", "10"),
                ("10", "12", "8", "9"),
            ],
            "1h",
            timedelta(hours=1),
        )
        self.assertTrue(_h1_veto(bullish_h1, "bearish"))
        self.assertFalse(_h1_veto(bullish_h1, "bullish"))
        self.assertTrue(_h1_veto(bearish_h1, "bullish"))
        self.assertFalse(_h1_veto(bearish_h1, "bearish"))

        def choose(m15, h1):
            return XauRunner._select(None, {"1d": _flat("1d"), "4h": _flat("4h"), "1h": h1, "15m": m15})[0]

        self.assertEqual(choose(bearish_m15, bullish_h1), "NO_TRADE")
        self.assertEqual(choose(bearish_m15, bearish_h1), "SCALP")
        self.assertEqual(choose(bullish_m15, bearish_h1), "NO_TRADE")
        self.assertEqual(choose(bullish_m15, bullish_h1), "SCALP")

    def test_d8_d9_pool_is_asia_pdl_london_swings_and_two_candle_close(self):
        start = datetime(2026, 11, 23, 12, tzinfo=UTC)
        bars = [
            _bar(start + timedelta(minutes=15 * index), "101", "102", "100.50", "101")
            for index in range(200)
        ]
        bars[10] = _px(bars[10].open_time, "101", "102", "80", "101")
        bars[60] = _px(bars[60].open_time, "101", "102", "90", "101")
        bars[160] = _px(bars[160].open_time, "101", "102", "100.00", "101")
        for index in range(172, 184):
            bars[index] = _px(bars[index].open_time, "101", "102", "95", "101")
        bars[186] = _px(bars[186].open_time, "101", "102", "97.00", "101")
        bars[190] = _px(bars[190].open_time, "101", "102", "97.40", "101")
        book = tuple(bars)
        moment = book[198].open_time
        london = _liquidity_levels(book, direction="long", sweep_at=198, moment=moment, window="London KZ")
        ny = _liquidity_levels(book, direction="long", sweep_at=198, moment=moment, window="NY KZ")
        self.assertIn(D("100.00"), london)
        self.assertIn(D("90"), london)
        self.assertIn(D("97.00"), london)
        self.assertIn(D("97.40"), london)
        self.assertIn(D("97.20"), london)
        self.assertNotIn(D("80"), london)
        self.assertNotIn(D("95"), london)
        self.assertIn(D("95"), ny)
        m5_origin = moment - timedelta(minutes=50)
        m5 = tuple(
            _bar(
                m5_origin + timedelta(minutes=5 * index), "55", "56",
                "50" if index in {2, 6} else "60", "55", "5m",
            )
            for index in range(10)
        )
        with_m5 = _liquidity_levels(
            book, direction="long", sweep_at=198, moment=moment, window="London KZ", m5=m5,
        )
        self.assertIn(D("50"), with_m5)

        asia = datetime(2026, 11, 25, 1, tzinfo=UTC)
        sweep = datetime(2026, 11, 25, 7, tzinfo=UTC)
        short = [
            _px(asia, "102", "103", "100", "102"),
            _px(asia + timedelta(minutes=15), "102", "103", "100.50", "102"),
            _px(asia + timedelta(minutes=30), "102", "103", "100.50", "102"),
            _px(sweep, "104", "105", "99.80", "99.90"),
            _px(sweep + timedelta(minutes=15), "100", "106", "99.95", "101"),
        ]
        levels = _liquidity_levels(
            tuple(short), direction="long", sweep_at=3, moment=sweep, window="London KZ",
        )
        self.assertEqual(levels, (D("100"),))
        hit = _sweep_of(tuple(short), 3, levels, "ssl")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.level, D("100"))
        held = short[:-1] + [_px(sweep + timedelta(minutes=15), "99.90", "100", "99.50", "99.70")]
        self.assertIsNone(_sweep_of(tuple(held), 3, levels, "ssl"))

    def test_d9_one_bar_sweep_matches_the_full_scan(self):
        origin = datetime(2026, 11, 25, 8, tzinfo=UTC)
        bars = []
        for index in range(40):
            low = "99.70" if index % 7 in {0, 1} else "100.40"
            close = "99.80" if index % 7 == 0 else "100.60"
            bars.append(_px(origin + timedelta(minutes=15 * index), "100.20", "101", low, close))
        book = tuple(bars)
        for level in (D("100"), D("99.50"), D("100.50")):
            found = {sweep.index: sweep for sweep in find_sweeps(book, level, "ssl")}
            for index in range(len(book)):
                self.assertEqual(_sweep_at(book, index, level, "ssl"), found.get(index))
        mirrored = tuple(
            _px(bar.open_time, "100.20", str(D("200") - bar.low), str(D("200") - bar.high), str(D("200") - bar.close))
            for bar in book
        )
        for level in (D("100"), D("99.50"), D("100.50")):
            found = {sweep.index: sweep for sweep in find_sweeps(mirrored, level, "bsl")}
            for index in range(len(mirrored)):
                self.assertEqual(_sweep_at(mirrored, index, level, "bsl"), found.get(index))

    def test_s1_sweep_search_stays_inside_the_trigger_window(self):
        origin = datetime(2026, 11, 20, tzinfo=UTC)
        bars = []
        for index in range(500):
            # Distinct swing lows inside $0.50, so the equal-level pool does not collapse to one price.
            low = str(D("109.00") + D(index % 48) * D("0.01")) if index % 3 == 0 else "109.60"
            bars.append(_bar(origin + timedelta(minutes=15 * index), "109.20", "112", low, "109.80"))
        book = tuple(bars)
        started = time.perf_counter()
        found = evaluate_smc(
            m15=book, h1=_h1_poi(), h4=(), direction="long", atr_h1=D("10"), spread=D("0.30"),
            fresh_reversal=False, aligned=True, bias_text="bullish", window="London KZ",
            valid_until=None, risk_pct=D("0.40"), in_kz=True, window_start=book[-5].open_time,
        )
        elapsed = time.perf_counter() - started
        self.assertIsNone(found)
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
