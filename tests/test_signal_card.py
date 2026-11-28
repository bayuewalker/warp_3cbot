"""Telegram verdict cards. Text only. No network and no order."""

import json
import unittest
from decimal import Decimal

import httpx

from warp3c.signalcard import (
    block_why,
    blocked_card,
    halt_off_card,
    halt_on_card,
    spot_card,
    telegram_text,
    worker_block_card,
)
from warp3c.telemetry import send_telegram
from warp3c.xau.alerts import new_signal, xau_card

_BUY = """🎯 Verdict — ETHUSDT
Action: 🟢 BUY
Confidence: 82%
Technical: 📊 trend up, RSI 58
Why: Higher lows on the 1h.
Entry: 3450
SL: 2760
TP: 3536
Size: 15% pair cap. Not sent.
Next: No order. Research only."""

_WAIT = """🎯 Verdict — SOLUSDT
Action: 🟡 WAIT
Confidence: 61%
Technical: 📊 trend up, RSI 58
Why: Backtest did not pass, so the BUY was held.
Size: none
Next: No order. Checked again on the next research run."""

_SKIP = """🎯 Verdict — BTCUSDT
Action: 🔵 SKIP
Confidence: 44%
Technical: 📊 range, RSI 48
Why: Evidence is too weak to buy.
Size: none
Next: No order. This pair is not sized."""

_BLOCKED = """🎯 Verdict — ADAUSDT
Action: 🔴 BLOCKED
Confidence: 80% on a BUY
Why: Rubric score is under the threshold.
Size: none
Next: No order. Risk rejected this pair."""

_HALT = """🎯 Verdict — HALT
Action: 🛑 HALT
Why: The halt switch is on.
Size: none
Next: No new orders until the switch is off. Open positions stay as they are."""

_XAU = """🎯 Verdict — XAUUSD
Action: 🔴 SELL LIMIT
Grade: A+
Bias: HTF bias short
Entry: 2650
SL: 2662
TP1: 2638
Risk: 0.35%
Why: Judas sweep through a killzone POI.
Next: Signal only. No order."""


def _icons(line: str) -> list[str]:
    return [char for char in line if ord(char) > 0x2600]


class CardTextTests(unittest.TestCase):
    def test_approved_examples(self):
        buy = spot_card(
            symbol="ETHUSDT",
            action="BUY",
            confidence=Decimal("0.82"),
            thesis="Higher lows on the 1h.",
            regime="trend_up",
            rsi=Decimal("58"),
            entry="3450",
            stop="2760",
            take="3536",
            pair_cap=Decimal("0.15"),
        )
        wait = spot_card(
            symbol="SOLUSDT",
            action="WAIT",
            confidence=Decimal("0.61"),
            thesis="ignored when the gate rewrites the line",
            regime="trend_up",
            rsi="58",
            model_action="BUY",
            backtest_passed=False,
        )
        skip = spot_card(
            symbol="BTCUSDT",
            action="SKIP",
            confidence=Decimal("0.44"),
            thesis="Evidence is too weak to buy.",
            regime="range",
            rsi=Decimal("48"),
        )
        blocked = blocked_card(
            symbol="ADAUSDT",
            confidence=Decimal("0.80"),
            why=block_why("score"),
        )
        gold = xau_card({
            "direction": "short",
            "entry_type": "limit",
            "grade": "A+",
            "bias": "HTF bias short",
            "entry": "2650",
            "sl": "2662",
            "tp1": "2638",
            "tp2": None,
            "risk_pct": "0.35",
            "reason": "Judas sweep through a killzone POI.",
            "r_usd": "12.5",
            "lot": "0.05",
            "ref": "10000",
            "id6": "ABC123",
            "confidence": 85,
        })
        cards = {
            "buy": buy,
            "wait": wait,
            "skip": skip,
            "blocked": blocked,
            "halt": halt_on_card(),
            "xau": gold,
        }
        expected = {
            "buy": _BUY,
            "wait": _WAIT,
            "skip": _SKIP,
            "blocked": _BLOCKED,
            "halt": _HALT,
            "xau": _XAU,
        }
        for name, text in cards.items():
            with self.subTest(name=name):
                self.assertEqual(text, expected[name])
                self.assertLessEqual(len(text), 400)
                self.assertNotIn("\n\n", text)
                for line in text.splitlines():
                    self.assertLessEqual(len(_icons(line)), 1)
                self.assertNotIn("10000", text)
                self.assertNotIn("0.05", text)
                self.assertNotIn("ABC123", text)
                self.assertNotIn("order was", text.lower())
                self.assertNotIn("parse_mode", text)

    def test_missing_rows_are_omitted(self):
        bare = spot_card(symbol="ETHUSDT", action="BUY", confidence=Decimal("0.82"), thesis="Higher lows.")
        self.assertNotIn("Technical:", bare)
        self.assertNotIn("Entry:", bare)
        self.assertNotIn("SL:", bare)
        self.assertNotIn("TP:", bare)
        self.assertIn("Size: none", bare)
        gold = xau_card({
            "direction": "long",
            "entry_type": "market",
            "grade": "A",
            "entry": "2650",
            "sl": "2600",
            "tp1": "2700",
            "risk_pct": "0.40",
            "reason": "pullback",
        })
        self.assertNotIn("Bias:", gold)
        self.assertNotIn("TP2:", gold)
        self.assertNotIn("Fundamental:", gold)
        self.assertNotIn("Confluence:", gold)
        self.assertNotIn("⭐", gold)
        self.assertIn("Action: 🟢 BUY MKT", gold)
        self.assertIn("Risk: 0.4%", gold)

    def test_worker_block_does_not_carry_the_exception(self):
        text = worker_block_card()
        self.assertIn("Research did not finish.", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("IntegrationError", text)
        self.assertLessEqual(len(text), 400)

    def test_halt_off_is_not_a_clear_trade(self):
        text = halt_off_card()
        self.assertNotIn("CLEAR", text)
        self.assertIn("Halt is off.", text)
        self.assertLessEqual(len(text), 400)

    def test_unknown_block_code_is_not_echoed(self):
        text = blocked_card(
            symbol="ADAUSDT",
            confidence="0.8",
            why=block_why("https://secret.example/token"),
        )
        self.assertNotIn("https://", text)
        self.assertNotIn("token", text)
        self.assertIn("Risk rejected this pair.", text)

    def test_telegram_sends_the_card_without_a_prefix_or_parse_mode(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"ok": True})

        event, detail = new_signal({
            "direction": "short",
            "entry_type": "limit",
            "grade": "A+",
            "id6": "ABC123",
            "bias": "HTF bias short",
            "entry": "2650",
            "sl": "2662",
            "tp1": "2638",
            "risk_pct": "0.35",
            "reason": "Judas sweep through a killzone POI.",
            "lot": "0.05",
            "ref": "10000",
            "r_usd": "9",
        })
        self.assertTrue(event.startswith("XAU "))
        self.assertEqual(telegram_text(event, detail), detail)
        self.assertTrue(send_telegram("123456:secret", "99", event, detail, transport=httpx.MockTransport(handler)))
        self.assertEqual(set(seen["body"]), {"chat_id", "text"})
        self.assertEqual(seen["body"]["text"], detail)
        self.assertNotIn("ABC123", seen["body"]["text"])
        self.assertNotIn("10000", seen["body"]["text"])
        self.assertNotIn("secret", seen["body"]["text"])

    def test_operator_alerts_keep_the_event_prefix(self):
        text = telegram_text("worker_started", "mode=paper shared_account=true")
        self.assertEqual(text, "worker_started: mode=paper shared_account=true")


if __name__ == "__main__":
    unittest.main()
