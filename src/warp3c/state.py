"""SQLite journal. An uncertain POST stays unknown until a strategy id is found."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .models import EquityMark


class OrderJournal:
    def __init__(self, filename: str):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(filename, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                client_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                state TEXT NOT NULL, payload TEXT NOT NULL,
                strategy_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS equity_marks (
                id INTEGER PRIMARY KEY,
                marked_at TEXT NOT NULL,
                equity TEXT NOT NULL,
                reliable INTEGER NOT NULL
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def close(self) -> None:
        self.db.close()

    def reserve(self, client_id: str, symbol: str, payload: dict) -> None:
        self.db.execute(
            "INSERT INTO submissions(client_id, symbol, state, payload) VALUES(?,?,?,?)",
            (client_id, symbol, "unknown", json.dumps(payload, sort_keys=True, default=_json)),
        )

    def confirm(self, client_id: str, strategy_id: str) -> None:
        self.db.execute(
            """
            UPDATE submissions
               SET state='confirmed', strategy_id=?, updated_at=CURRENT_TIMESTAMP
             WHERE client_id=? AND state='unknown'
            """,
            (strategy_id, client_id),
        )

    def reject(self, client_id: str) -> None:
        """A definite broker rejection. Unknown timeouts must not call this."""
        self.db.execute(
            """
            UPDATE submissions
               SET state='rejected', updated_at=CURRENT_TIMESTAMP
             WHERE client_id=? AND state='unknown'
            """,
            (client_id,),
        )

    def unresolved(self) -> list[str]:
        return [row[0] for row in self.db.execute("SELECT client_id FROM submissions WHERE state='unknown'")]

    def reconcile_ids(self, found: dict[str, str]) -> list[str]:
        for client_id in self.unresolved():
            strategy_id = found.get(client_id)
            if strategy_id:
                self.confirm(client_id, strategy_id)
        return self.unresolved()

    def reconcile(self, active: list[dict]) -> list[str]:
        found = {
            str(row.get("clientId")): str(row.get("strategyId"))
            for row in active
            if row.get("clientId") and row.get("strategyId")
        }
        return self.reconcile_ids(found)

    def recently_submitted(self, symbol: str, hours: int = 24) -> bool:
        row = self.db.execute(
            """
            SELECT COUNT(*) FROM submissions
             WHERE symbol=? AND state IN ('unknown', 'confirmed')
               AND created_at >= datetime('now', ?)
            """,
            (symbol, f"-{hours} hours"),
        ).fetchone()
        return bool(row and row[0])

    def record_equity(self, equity: Decimal, reliable: bool, *, now: datetime | None = None) -> None:
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.db.execute(
            "INSERT INTO equity_marks(marked_at, equity, reliable) VALUES(?,?,?)",
            (moment.isoformat(), format(equity, "f"), 1 if reliable else 0),
        )

    def equity_marks(self) -> list[EquityMark]:
        marks = []
        for marked_at, equity, reliable in self.db.execute(
            "SELECT marked_at, equity, reliable FROM equity_marks ORDER BY marked_at"
        ):
            moment = datetime.fromisoformat(marked_at)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            marks.append(EquityMark(moment, Decimal(equity), bool(reliable)))
        return marks

    def heartbeat(self, detail: dict) -> None:
        self.db.execute(
            """
            INSERT INTO runtime(key, value, updated_at) VALUES('heartbeat', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """,
            (json.dumps(detail, sort_keys=True),),
        )

    def clear_alert(self) -> None:
        self.db.execute("DELETE FROM runtime WHERE key='alert'")

    def claim_alert(self, key: str) -> bool:
        """True when this alert key has not been sent yet."""
        row = self.db.execute("SELECT value FROM runtime WHERE key='alert'").fetchone()
        if row and row[0] == key:
            return False
        self.db.execute(
            """
            INSERT INTO runtime(key, value, updated_at) VALUES('alert', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """,
            (key,),
        )
        return True


def _json(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Cannot store {type(value).__name__}")
