"""Forward-test journal. Prices come from the market, never from the model.

A BUY hit means the later price is higher. A SKIP hit means it is lower.
A WAIT hit means the move stayed inside half a percent. Average move is signed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .market import MarketDataError

HORIZONS = (("4h", 4), ("24h", 24), ("72h", 72))
WAIT_BAND = Decimal("0.005")
# Horizons whose price lookup failed in this process. The next cycle skips them
# so a missing price does not consume the attempt cap again.
_FAILED: set[tuple[int, int, str]] = set()


def is_hit(verdict: str, move: Decimal) -> bool:
    if verdict == "BUY":
        return move > 0
    if verdict == "SKIP":
        return move < 0
    if verdict == "WAIT":
        return abs(move) <= WAIT_BAND
    return False


def evaluate_due(journal, price_at, now: datetime, *, limit: int = 20) -> int:
    """Fill horizons that are already due. ``limit`` counts price lookups, not only fills.

    A missing price leaves the cell empty. A horizon that failed earlier in this
    process is not requested again, so the backlog can move on to other rows.
    """
    if price_at is None:
        return 0
    moment = now.astimezone(timezone.utc)
    filled = 0
    attempts = 0
    journal_key = str(getattr(journal, "path", id(journal)))
    for row in journal.verdict_rows():
        if attempts >= limit:
            break
        recorded = datetime.fromisoformat(row["recorded_at"])
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
        decision = Decimal(row["price"])
        if decision <= 0:
            continue
        due: list[tuple[str, datetime]] = []
        for label, hours in HORIZONS:
            if row.get(f"move_{label}") is not None:
                continue
            at = recorded + timedelta(hours=hours)
            if moment < at:
                continue
            due.append((label, at))
        pending = [
            (label, at)
            for label, at in due
            if (journal_key, int(row["id"]), label) not in _FAILED
        ]
        if due and not pending:
            continue
        for label, at in pending:
            if attempts >= limit:
                break
            attempts += 1
            try:
                later = price_at(row["symbol"], at)
            except (MarketDataError, ValueError, ArithmeticError, TypeError, OSError):
                later = None
            if later is None:
                _FAILED.add((journal_key, int(row["id"]), label))
                continue
            try:
                later_price = Decimal(str(later))
            except (ValueError, ArithmeticError, TypeError):
                _FAILED.add((journal_key, int(row["id"]), label))
                continue
            if later_price <= 0:
                _FAILED.add((journal_key, int(row["id"]), label))
                continue
            move = (later_price - decision) / decision
            journal.set_verdict_horizon(row["id"], label, later_price, move)
            filled += 1
    return filled


def summary(journal, *, round_trip: Decimal = Decimal("0.002")) -> dict:
    buckets = {
        verdict: {
            label: {"n": 0, "hits": 0, "move_sum": Decimal("0")}
            for label, _hours in HORIZONS
        }
        for verdict in ("BUY", "WAIT", "SKIP")
    }
    pending = 0
    scored = 0
    for row in journal.verdict_rows():
        verdict = row["verdict"]
        if verdict not in buckets:
            continue
        any_score = False
        for label, _hours in HORIZONS:
            raw = row.get(f"move_{label}")
            if raw is None:
                continue
            any_score = True
            move = Decimal(raw)
            slot = buckets[verdict][label]
            slot["n"] += 1
            slot["move_sum"] += move
            if is_hit(verdict, move):
                slot["hits"] += 1
        if any_score:
            scored += 1
        else:
            pending += 1
    by_verdict = {}
    for verdict, horizons in buckets.items():
        by_verdict[verdict] = {}
        for label, slot in horizons.items():
            count = slot["n"]
            by_verdict[verdict][label] = {
                "n": count,
                "hits": slot["hits"],
                "hit_rate": None if count == 0 else format(Decimal(slot["hits"]) / Decimal(count), "f"),
                "avg_move": None if count == 0 else format(slot["move_sum"] / Decimal(count), "f"),
                "avg_net_move": None if count == 0 else format((slot["move_sum"] / Decimal(count)) - round_trip, "f"),
            }
    return {
        "hit_rule": "BUY if price rises, SKIP if it falls, WAIT if the move stays within 0.5%",
        "horizons": [label for label, _hours in HORIZONS],
        "by_verdict": by_verdict,
        "scored": scored,
        "pending": pending,
    }
