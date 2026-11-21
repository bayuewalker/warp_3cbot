"""Internal spot DCA paper book. It never places an order or changes TRADING_MODE."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .capital import Exits, Ladder, Policy, go_no_go


def _d(value: object) -> Decimal:
    return Decimal(str(value))


def _text(value: Decimal) -> str:
    return format(value, "f")


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class PaperBook:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_deals (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                entry_price TEXT NOT NULL,
                legs_json TEXT NOT NULL,
                coins TEXT NOT NULL,
                cost TEXT NOT NULL,
                fees TEXT NOT NULL,
                avg_price TEXT NOT NULL,
                tp_price TEXT NOT NULL,
                sl_price TEXT NOT NULL,
                take_profit_on INTEGER NOT NULL,
                stop_loss TEXT NOT NULL,
                pnl TEXT NOT NULL,
                sleeve TEXT NOT NULL,
                last_candle TEXT,
                close_reason TEXT
            )
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def has_open(self, symbol: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM paper_deals WHERE symbol = ? AND status = 'open'",
            (symbol,),
        ).fetchone()
        return row is not None

    def open_deals(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM paper_deals WHERE status = 'open' ORDER BY id").fetchall()
        return [self._row(row) for row in rows]

    def closed_deals(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM paper_deals WHERE status != 'open' ORDER BY closed_at, id"
        ).fetchall()
        return [self._row(row) for row in rows]

    def open_ladder(
        self,
        *,
        symbol: str,
        ladder: Ladder,
        entry: Decimal,
        exits: Exits,
        policy: Policy,
        now: datetime,
        sleeve: Decimal,
    ) -> dict:
        if self.has_open(symbol):
            raise ValueError("paper deal already open")
        if entry <= 0 or not ladder.orders:
            raise ValueError("paper entry is empty")
        base = ladder.orders[0]
        coins = base / entry
        fees = policy.fee_rate * base
        legs = [{"quote": _text(base), "price": _text(entry), "filled": True}]
        for index, quote in enumerate(ladder.orders[1:], start=1):
            level = entry * (Decimal("1") - ladder.step * index)
            legs.append({"quote": _text(quote), "price": _text(level), "filled": False})
        avg = base / coins
        tp = avg * (Decimal("1") + ladder.take_profit)
        cursor = self.db.execute(
            """
            INSERT INTO paper_deals (
                symbol, status, opened_at, entry_price, legs_json, coins, cost, fees,
                avg_price, tp_price, sl_price, take_profit_on, stop_loss, pnl, sleeve, last_candle
            ) VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '0', ?, ?)
            """,
            (
                symbol,
                _stamp(now),
                _text(entry),
                json.dumps(legs),
                _text(coins),
                _text(base),
                _text(fees),
                _text(avg),
                _text(tp),
                _text(ladder.stop_price),
                1 if exits.take_profit else 0,
                _text(ladder.stop_loss),
                _text(sleeve),
                _stamp(now),
            ),
        )
        self.db.commit()
        row = self.db.execute("SELECT * FROM paper_deals WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._row(row)

    def advance(self, symbol: str, candles: list[dict], policy: Policy, now: datetime) -> list[dict]:
        deal = self._open(symbol)
        if deal is None:
            return []
        closed: list[dict] = []
        cursor = _moment(deal["last_candle"]) if deal["last_candle"] else _moment(deal["opened_at"])
        for candle in candles:
            close_at = candle["close_at"]
            if close_at.tzinfo is None:
                close_at = close_at.replace(tzinfo=timezone.utc)
            if close_at <= cursor:
                continue
            deal = self._fill_and_exit(deal, candle, policy, close_at)
            cursor = close_at
            if deal["status"] != "open":
                closed.append(deal)
                break
        return closed

    def invalidate(self, symbol: str, price: Decimal, policy: Policy, now: datetime, reason: str) -> dict | None:
        deal = self._open(symbol)
        if deal is None or price <= 0:
            return None
        return self._finish(deal, "invalid", price, policy, now, reason)

    def events(self) -> list[dict]:
        rows = []
        for deal in self.closed_deals():
            kind = {"stop": "stop", "tp": "tp"}.get(deal["status"])
            if kind is None or not deal["closed_at"]:
                continue
            rows.append({"symbol": deal["symbol"], "kind": kind, "at": deal["closed_at"]})
        return rows

    def exposure(self) -> Decimal:
        total = Decimal("0")
        for deal in self.open_deals():
            total += _d(deal["cost"])
        return total

    def commitment(self) -> Decimal:
        """Full ladder quote, including safety orders that have not filled."""
        total = Decimal("0")
        for deal in self.open_deals():
            for leg in deal["legs"]:
                total += _d(leg["quote"])
        return total

    def pending_safety(self) -> Decimal:
        total = Decimal("0")
        for deal in self.open_deals():
            for leg in deal["legs"]:
                if not leg["filled"]:
                    total += _d(leg["quote"])
        return total

    def stop_risk(self) -> Decimal:
        total = Decimal("0")
        for deal in self.open_deals():
            total += _d(deal["stop_loss"])
        return total

    def metrics(self, policy: Policy, now: datetime, sleeve: Decimal | None) -> dict:
        closed = self.closed_deals()
        opened = self._all()
        wins = [row for row in closed if _d(row["pnl"]) > 0]
        net = sum((_d(row["pnl"]) for row in closed), Decimal("0"))
        expectancy = None if not closed else net / len(closed)
        durations = []
        for row in closed:
            if row["closed_at"]:
                durations.append((_moment(row["closed_at"]) - _moment(row["opened_at"])).total_seconds())
        average = None if not durations else Decimal(str(sum(durations) / len(durations)))
        first = min((_moment(row["opened_at"]) for row in opened), default=None)
        paper_days = 0 if first is None else (now.astimezone(timezone.utc) - first).days
        if sleeve is None or sleeve <= 0:
            return {
                "switches_live": False,
                "open": [self._public(row) for row in self.open_deals()],
                "closed": [self._public(row) for row in closed[-30:]],
                "closed_deals": len(closed),
                "win_rate": None if not closed else _text(Decimal(len(wins)) / Decimal(len(closed))),
                "net_pnl": _text(net) if closed else None,
                "net_pnl_pct": None,
                "net_pnl_reason": "sleeve_unset",
                "expectancy": None if expectancy is None else _text(expectancy),
                "max_drawdown": None,
                "average_duration_seconds": None if average is None else _text(average),
                "open_exposure": _text(self.exposure()),
                "paper_days": paper_days,
                "gate_pass": False,
                "gate_reasons": ["sleeve_unset"],
                "as_of": _stamp(now),
            }
        drawdown = self._max_drawdown(closed, sleeve)
        passed, reasons = go_no_go(
            paper_days=paper_days,
            closed_deals=len(closed),
            net_expectancy=Decimal("0") if expectancy is None else expectancy,
            max_drawdown=drawdown,
            policy=policy,
        )
        pnl_pct = None
        pnl_reason = None
        if sleeve is None or sleeve <= 0:
            pnl_reason = "sleeve_unset"
        else:
            pnl_pct = net / sleeve
        return {
            "switches_live": False,
            "open": [self._public(row) for row in self.open_deals()],
            "closed": [self._public(row) for row in closed[-30:]],
            "closed_deals": len(closed),
            "win_rate": None if not closed else _text(Decimal(len(wins)) / Decimal(len(closed))),
            "net_pnl": _text(net) if closed else None,
            "net_pnl_pct": None if pnl_pct is None else _text(pnl_pct),
            "net_pnl_reason": pnl_reason,
            "expectancy": None if expectancy is None else _text(expectancy),
            "max_drawdown": _text(drawdown),
            "average_duration_seconds": None if average is None else _text(average),
            "open_exposure": _text(self.exposure()),
            "paper_days": paper_days,
            "gate_pass": passed,
            "gate_reasons": reasons,
            "as_of": _stamp(now),
        }

    def _fill_and_exit(self, deal: dict, candle: dict, policy: Policy, close_at: datetime) -> dict:
        legs = deal["legs"]
        coins = _d(deal["coins"])
        cost = _d(deal["cost"])
        fees = _d(deal["fees"])
        for leg in legs:
            if leg["filled"]:
                continue
            level = _d(leg["price"])
            if candle["low"] <= level:
                quote = _d(leg["quote"])
                coins += quote / level
                cost += quote
                fees += policy.fee_rate * quote
                leg["filled"] = True
        avg = cost / coins
        tp = avg * (Decimal("1") + self._tp_fraction(deal))
        sl = _d(deal["sl_price"])
        deal["legs"] = legs
        deal["coins"] = _text(coins)
        deal["cost"] = _text(cost)
        deal["fees"] = _text(fees)
        deal["avg_price"] = _text(avg)
        deal["tp_price"] = _text(tp)
        deal["last_candle"] = _stamp(close_at)
        deal["stop_loss"] = _text(self._marked_stop(coins, cost, fees, sl, policy))
        hit_tp = bool(deal["take_profit_on"]) and candle["high"] >= tp
        hit_sl = candle["low"] <= sl
        if hit_tp and hit_sl:
            return self._finish(deal, "stop", sl * (Decimal("1") - policy.stop_slippage), policy, close_at, "same_bar")
        if hit_sl:
            return self._finish(deal, "stop", sl * (Decimal("1") - policy.stop_slippage), policy, close_at, "stop")
        if hit_tp:
            return self._finish(deal, "tp", tp, policy, close_at, "tp")
        self._save(deal)
        return deal

    def _tp_fraction(self, deal: dict) -> Decimal:
        avg = _d(deal["avg_price"])
        if avg <= 0:
            return Decimal("0")
        return (_d(deal["tp_price"]) / avg) - Decimal("1")

    def _marked_stop(self, coins: Decimal, cost: Decimal, fees: Decimal, sl: Decimal, policy: Policy) -> Decimal:
        fill = sl * (Decimal("1") - policy.stop_slippage)
        gross = cost - coins * fill
        exit_fee = policy.fee_rate * coins * fill
        return max(Decimal("0"), gross) + fees + exit_fee

    def _finish(self, deal: dict, status: str, fill: Decimal, policy: Policy, now: datetime, reason: str) -> dict:
        coins = _d(deal["coins"])
        exit_fee = policy.fee_rate * coins * fill
        fees = _d(deal["fees"]) + exit_fee
        pnl = coins * fill - _d(deal["cost"]) - fees
        deal["status"] = status
        deal["closed_at"] = _stamp(now)
        deal["fees"] = _text(fees)
        deal["pnl"] = _text(pnl)
        deal["close_reason"] = reason
        deal["last_candle"] = _stamp(now)
        self._save(deal)
        return deal

    def _max_drawdown(self, closed: list[dict], sleeve: Decimal | None) -> Decimal:
        if sleeve is None or sleeve <= 0 or not closed:
            return Decimal("0")
        equity = sleeve
        peak = sleeve
        worst = Decimal("0")
        for row in closed:
            equity += _d(row["pnl"])
            peak = max(peak, equity)
            if peak > 0:
                worst = max(worst, (peak - equity) / peak)
        return worst

    def _open(self, symbol: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM paper_deals WHERE symbol = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return None if row is None else self._row(row)

    def _all(self) -> list[dict]:
        return [self._row(row) for row in self.db.execute("SELECT * FROM paper_deals ORDER BY id")]

    def _save(self, deal: dict) -> None:
        self.db.execute(
            """
            UPDATE paper_deals SET
                status = ?, closed_at = ?, legs_json = ?, coins = ?, cost = ?, fees = ?,
                avg_price = ?, tp_price = ?, stop_loss = ?, pnl = ?, last_candle = ?, close_reason = ?
            WHERE id = ?
            """,
            (
                deal["status"],
                deal["closed_at"],
                json.dumps(deal["legs"]),
                deal["coins"],
                deal["cost"],
                deal["fees"],
                deal["avg_price"],
                deal["tp_price"],
                deal["stop_loss"],
                deal["pnl"],
                deal["last_candle"],
                deal["close_reason"],
                deal["id"],
            ),
        )
        self.db.commit()

    def _row(self, row: sqlite3.Row) -> dict:
        deal = dict(row)
        deal["legs"] = json.loads(deal.pop("legs_json"))
        deal["take_profit_on"] = bool(deal["take_profit_on"])
        return deal

    def _public(self, deal: dict) -> dict:
        return {
            "id": deal["id"],
            "symbol": deal["symbol"],
            "status": deal["status"],
            "opened_at": deal["opened_at"],
            "closed_at": deal["closed_at"],
            "avg_price": deal["avg_price"],
            "tp_price": deal["tp_price"],
            "sl_price": deal["sl_price"],
            "coins": deal["coins"],
            "cost": deal["cost"],
            "fees": deal["fees"],
            "pnl": deal["pnl"],
            "close_reason": deal["close_reason"],
            "filled_legs": sum(1 for leg in deal["legs"] if leg["filled"]),
            "legs": len(deal["legs"]),
        }
