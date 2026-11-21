"""SQLite journal. An uncertain POST stays unknown until a strategy id is found."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .models import EquityMark


class OrderJournal:
    def __init__(self, filename: str):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        self.path = filename
        # The dashboard reads the journal from HTTP worker threads as well as the
        # monitoring loop. Autocommit mode and SQLite's serialized threading make a
        # shared connection safe; without this flag /api/accounts raised a 500.
        self.db = sqlite3.connect(filename, isolation_level=None, timeout=30, check_same_thread=False)
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
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS warp_bots (
                warp_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                client_id TEXT,
                external_code TEXT,
                kind TEXT NOT NULL,
                product TEXT NOT NULL,
                account_code TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                commitment TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS verdicts (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                verdict TEXT NOT NULL,
                confidence TEXT NOT NULL,
                price TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                price_4h TEXT,
                move_4h TEXT,
                price_24h TEXT,
                move_24h TEXT,
                price_72h TEXT,
                move_72h TEXT
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

    def submission_state(self, client_id: str) -> str | None:
        row = self.db.execute("SELECT state FROM submissions WHERE client_id=?", (client_id,)).fetchone()
        return None if row is None else str(row[0])

    def get_setting(self, key: str, *, request_local: bool = False) -> str | None:
        """HTTP handlers must read through a connection owned by their thread."""
        connection = (
            closing(sqlite3.connect(self.path, isolation_level=None, timeout=30))
            if request_local else nullcontext(self.db)
        )
        with connection as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_setting(self, key: str, value: str) -> None:
        """Write from the HTTP thread without borrowing the worker connection."""
        db = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute(
                """
                INSERT INTO settings(key, value, updated_at) VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
                """,
                (key, value),
            )
        finally:
            db.close()

    def confirmed_submissions(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT client_id, symbol, payload FROM submissions WHERE state='confirmed'"
        )
        found = []
        for client_id, symbol, payload in rows:
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Journal payload is unreadable") from exc
            if not isinstance(parsed, dict):
                raise RuntimeError("Journal payload is unreadable")
            found.append({"client_id": client_id, "symbol": symbol, "payload": parsed})
        return found

    def insert_warp_bot(self, row: dict) -> None:
        self.db.execute(
            """
            INSERT INTO warp_bots(
                warp_id, name, client_id, external_code, kind, product, account_code,
                market, symbol, status, commitment, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["warp_id"],
                row["name"],
                row.get("client_id"),
                row.get("external_code"),
                row["kind"],
                row["product"],
                row["account_code"],
                row["market"],
                row["symbol"],
                row["status"],
                row["commitment"],
                row["created_at"],
                row["updated_at"],
            ),
        )

    def warp_bot(self, warp_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM warp_bots WHERE warp_id=?", (warp_id,)).fetchone()
        return None if row is None else _warp_row(row)

    def warp_bots(self, account_code: str | None = None) -> list[dict]:
        if account_code:
            rows = self.db.execute(
                "SELECT * FROM warp_bots WHERE account_code=? ORDER BY created_at DESC",
                (account_code,),
            )
        else:
            rows = self.db.execute("SELECT * FROM warp_bots ORDER BY created_at DESC")
        return [_warp_row(row) for row in rows]

    def update_warp_bot(self, warp_id: str, **fields: str) -> None:
        allowed = {"status", "external_code", "updated_at"}
        if not fields or any(key not in allowed for key in fields):
            raise ValueError("Unexpected bot field")
        assignments = ", ".join(f"{key}=?" for key in fields)
        self.db.execute(
            f"UPDATE warp_bots SET {assignments} WHERE warp_id=?",
            (*fields.values(), warp_id),
        )

    def record_verdict(
        self, symbol: str, verdict: str, confidence: Decimal, price: Decimal, now: datetime
    ) -> None:
        moment = now.astimezone(timezone.utc).isoformat()
        self.db.execute(
            """
            INSERT INTO verdicts(symbol, verdict, confidence, price, recorded_at)
            VALUES(?,?,?,?,?)
            """,
            (symbol, verdict, format(confidence, "f"), format(price, "f"), moment),
        )

    def verdict_rows(self) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT id, symbol, verdict, confidence, price, recorded_at,
                   price_4h, move_4h, price_24h, move_24h, price_72h, move_72h
              FROM verdicts ORDER BY id
            """
        )
        found = []
        for row in rows:
            found.append(
                {
                    "id": row[0],
                    "symbol": row[1],
                    "verdict": row[2],
                    "confidence": row[3],
                    "price": row[4],
                    "recorded_at": row[5],
                    "price_4h": row[6],
                    "move_4h": row[7],
                    "price_24h": row[8],
                    "move_24h": row[9],
                    "price_72h": row[10],
                    "move_72h": row[11],
                }
            )
        return found

    def set_verdict_horizon(self, verdict_id: int, horizon: str, price: Decimal, move: Decimal) -> None:
        if horizon not in {"4h", "24h", "72h"}:
            raise ValueError("Unknown horizon")
        self.db.execute(
            f"UPDATE verdicts SET price_{horizon}=?, move_{horizon}=? WHERE id=? AND move_{horizon} IS NULL",
            (format(price, "f"), format(move, "f"), verdict_id),
        )

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


def _warp_row(row) -> dict:
    return {
        "warp_id": row[0],
        "name": row[1],
        "client_id": row[2],
        "external_code": row[3],
        "kind": row[4],
        "product": row[5],
        "account_code": row[6],
        "market": row[7],
        "symbol": row[8],
        "status": row[9],
        "commitment": row[10],
        "created_at": row[11],
        "updated_at": row[12],
    }


def _json(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Cannot store {type(value).__name__}")
