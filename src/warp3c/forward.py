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
# A failed lookup waits, then retries. The wait doubles and stops growing at an hour,
# so one transient miss does not pin the row, and it does not take a cap slot every scan.
_RETRY_BASE_SECONDS = 60
_RETRY_MAX_SECONDS = 3600
# key -> (failure count, next time the lookup may run)
_FAILED: dict[tuple[str, int, str], tuple[int, datetime]] = {}


def is_hit(verdict: str, move: Decimal) -> bool:
    if verdict == "BUY":
        return move > 0
    if verdict == "SKIP":
        return move < 0
    if verdict == "WAIT":
        return abs(move) <= WAIT_BAND
    return False


def _backoff_seconds(failures: int) -> int:
    delay = _RETRY_BASE_SECONDS
    for _ in range(max(0, failures - 1)):
        if delay >= _RETRY_MAX_SECONDS:
            return _RETRY_MAX_SECONDS
        delay *= 2
    return min(delay, _RETRY_MAX_SECONDS)


def _mark_failed(key: tuple[str, int, str], moment: datetime) -> None:
    prior = _FAILED.get(key)
    failures = 1 if prior is None else prior[0] + 1
    _FAILED[key] = (failures, moment + timedelta(seconds=_backoff_seconds(failures)))


def evaluate_due(journal, price_at, now: datetime, *, limit: int = 20) -> int:
    """Fill horizons that are already due. ``limit`` counts price lookups, not only fills.

    A missing price leaves the cell empty. That horizon waits out a backoff before
    the next lookup, and the wait counts against the same per-scan cap, so the
    rest of the backlog still moves.
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
        pending: list[tuple[str, datetime]] = []
        for label, at in due:
            key = (journal_key, int(row["id"]), label)
            record = _FAILED.get(key)
            if record is not None and moment < record[1]:
                continue
            pending.append((label, at))
        if due and not pending:
            continue
        for label, at in pending:
            if attempts >= limit:
                break
            key = (journal_key, int(row["id"]), label)
            attempts += 1
            try:
                later = price_at(row["symbol"], at)
            except (MarketDataError, ValueError, ArithmeticError, TypeError, OSError):
                later = None
            if later is None:
                _mark_failed(key, moment)
                continue
            try:
                later_price = Decimal(str(later))
            except (ValueError, ArithmeticError, TypeError):
                _mark_failed(key, moment)
                continue
            if later_price <= 0:
                _mark_failed(key, moment)
                continue
            _FAILED.pop(key, None)
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
