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
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY,
                event TEXT NOT NULL,
                detail TEXT NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                delivered_at TEXT
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY,
                recorded_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                stage TEXT NOT NULL,
                reason TEXT NOT NULL,
                detail TEXT NOT NULL
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
        """Allow a new incident after recovery. Undelivered rows stay pending."""
        self.db.execute("DELETE FROM runtime WHERE key='alert'")
        self.db.execute("UPDATE alerts SET state='resolved' WHERE state='delivered'")

    def enqueue_alert(self, event: str, detail: str, *, now: datetime | None = None) -> bool:
        """Queue one incident. A pending or unresolved delivery suppresses duplicates."""
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        existing = self.db.execute(
            """
            SELECT state FROM alerts
             WHERE event=? AND state IN ('pending', 'delivered')
             ORDER BY id DESC LIMIT 1
            """,
            (event,),
        ).fetchone()
        if existing:
            return False
        self.db.execute(
            """
            INSERT INTO alerts(event, detail, state, attempts, next_attempt_at, created_at)
            VALUES(?, ?, 'pending', 0, ?, ?)
            """,
            (event, detail[:500], moment.isoformat(), moment.isoformat()),
        )
        return True

    def due_alerts(self, now: datetime) -> list[tuple[int, str, str, int]]:
        moment = now.astimezone(timezone.utc).isoformat()
        return list(
            self.db.execute(
                """
                SELECT id, event, detail, attempts FROM alerts
                 WHERE state='pending' AND next_attempt_at<=?
                 ORDER BY id
                """,
                (moment,),
            )
        )

    def mark_delivered(self, alert_id: int, now: datetime) -> None:
        moment = now.astimezone(timezone.utc).isoformat()
        self.db.execute(
            """
            UPDATE alerts
               SET state='delivered', delivered_at=?, attempts=attempts+1, last_error=NULL
             WHERE id=? AND state='pending'
            """,
            (moment, alert_id),
        )

    def mark_retry(self, alert_id: int, *, attempts: int, next_at: datetime, error: str) -> None:
        self.db.execute(
            """
            UPDATE alerts
               SET attempts=?, next_attempt_at=?, last_error=?
             WHERE id=? AND state='pending'
            """,
            (attempts, next_at.astimezone(timezone.utc).isoformat(), error[:200], alert_id),
        )

    def recent_alerts(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT id, event, state, attempts, last_error, created_at, delivered_at
              FROM alerts ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "id": row[0],
                "event": row[1],
                "state": row[2],
                "attempts": row[3],
                "last_error": row[4],
                "created_at": row[5],
                "delivered_at": row[6],
            }
            for row in rows
        ]

    def record_decisions(self, rows: list[tuple[str, str, str, str, str]]) -> None:
        self.db.executemany(
            "INSERT INTO decisions(recorded_at, symbol, stage, reason, detail) VALUES(?,?,?,?,?)",
            rows,
        )

    def recent_decisions(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT recorded_at, symbol, stage, reason, detail
              FROM decisions ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        return [
            {"recorded_at": row[0], "symbol": row[1], "stage": row[2], "reason": row[3], "detail": row[4]}
            for row in rows
        ]

    def recent_submissions(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT client_id, symbol, state, strategy_id, created_at
              FROM submissions ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "client_id": row[0],
                "symbol": row[1],
                "state": row[2],
                "strategy_id": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    def backup(self, dest: str) -> None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(dest)
        try:
            self.db.backup(target)
        finally:
            target.close()

    def restore_from(self, src: str) -> None:
        source = sqlite3.connect(src)
        try:
            source.backup(self.db)
        finally:
            source.close()


def _json(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Cannot store {type(value).__name__}")
