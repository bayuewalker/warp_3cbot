"""XAU tables only. Own connection, autocommit, busy_timeout 2000, no cross-table writes."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS xau_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xau_signals (
        id TEXT PRIMARY KEY,
        created_at_utc TEXT NOT NULL,
        rules_version TEXT NOT NULL,
        feed TEXT,
        basis TEXT,
        engine TEXT NOT NULL,
        model TEXT NOT NULL,
        window TEXT,
        direction TEXT NOT NULL,
        grade TEXT NOT NULL,
        confidence TEXT,
        entry_type TEXT NOT NULL,
        entry TEXT NOT NULL,
        sl TEXT NOT NULL,
        tp1 TEXT NOT NULL,
        tp2 TEXT,
        r_usd TEXT NOT NULL,
        rr_tp1 TEXT,
        rr_tp2 TEXT,
        risk_pct TEXT,
        example_lot TEXT,
        valid_until TEXT,
        checklist_json TEXT,
        reason TEXT,
        bias TEXT,
        dol TEXT,
        dxy_note TEXT,
        tags_json TEXT,
        sent INTEGER NOT NULL,
        tg_event TEXT,
        status TEXT NOT NULL,
        filled_at TEXT,
        fill_price TEXT,
        exit_at TEXT,
        exit_reason TEXT,
        realized_r TEXT,
        mae_r TEXT,
        mfe_r TEXT,
        spread_assumed TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xau_signal_events (
        id INTEGER PRIMARY KEY,
        signal_id TEXT NOT NULL,
        at_utc TEXT NOT NULL,
        kind TEXT NOT NULL,
        price TEXT,
        realized_r TEXT,
        detail TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xau_decisions (
        id INTEGER PRIMARY KEY,
        at_utc TEXT NOT NULL,
        signal_id TEXT,
        rule_ids TEXT NOT NULL,
        accepted INTEGER NOT NULL,
        grade TEXT,
        detail TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xau_windows (
        window_key TEXT PRIMARY KEY,
        started_at_utc TEXT NOT NULL,
        engine TEXT NOT NULL,
        reason TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xau_news (
        id INTEGER PRIMARY KEY,
        fetched_at_utc TEXT NOT NULL,
        source TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xau_feed_health (
        id INTEGER PRIMARY KEY,
        at_utc TEXT NOT NULL,
        feed TEXT NOT NULL,
        ok INTEGER NOT NULL,
        detail TEXT
    )
    """,
)

_SIGNAL_COLUMNS = (
    "id", "created_at_utc", "rules_version", "feed", "basis", "engine", "model", "window",
    "direction", "grade", "confidence", "entry_type", "entry", "sl", "tp1", "tp2", "r_usd",
    "rr_tp1", "rr_tp2", "risk_pct", "example_lot", "valid_until", "checklist_json", "reason",
    "bias", "dol", "dxy_note", "tags_json", "sent", "tg_event", "status", "filled_at",
    "fill_price", "exit_at", "exit_reason", "realized_r", "mae_r", "mfe_r", "spread_assumed",
)


def new_id(moment: datetime | None = None) -> str:
    """ULID. Time-ordered, stdlib only."""
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    now = moment or datetime.now(timezone.utc)
    millis = int(now.timestamp() * 1000) & ((1 << 48) - 1)
    value = (millis << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(alphabet[value & 31])
        value >>= 5
    return "".join(reversed(chars))


class XauStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None, timeout=2, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=2000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrated = False

    def close(self) -> None:
        self.db.close()

    def migrate(self) -> None:
        """Forward-only and idempotent. One short transaction, then single-statement writes."""
        if self._migrated:
            return
        self.db.execute("BEGIN")
        try:
            for statement in _DDL:
                self.db.execute(statement)
            self.db.execute(
                "INSERT INTO xau_meta(key, value) VALUES('schema_version', '1') "
                "ON CONFLICT(key) DO NOTHING"
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self._migrated = True

    def meta_get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM xau_meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def meta_set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO xau_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def insert_signal(self, row: dict) -> None:
        columns = _SIGNAL_COLUMNS
        sql = (
            "INSERT INTO xau_signals(" + ",".join(columns) + ") VALUES("
            + ",".join("?" for _ in columns) + ")"
        )
        self.db.execute(sql, tuple(row.get(name) for name in columns))

    def update_signal(self, signal_id: str, **fields: object) -> None:
        allowed = set(_SIGNAL_COLUMNS) - {"id"}
        keys = [key for key in fields if key in allowed]
        if not keys:
            return
        sql = "UPDATE xau_signals SET " + ",".join(f"{key}=?" for key in keys) + " WHERE id=?"
        self.db.execute(sql, tuple(fields[key] for key in keys) + (signal_id,))

    def signal(self, signal_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM xau_signals WHERE id=?", (signal_id,)).fetchone()
        return None if row is None else dict(row)

    def signals(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM xau_signals ORDER BY created_at_utc").fetchall()
        return [dict(row) for row in rows]

    def open_signals(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM xau_signals WHERE status IN ('PENDING','FILLED','TP1','TRAIL') "
            "ORDER BY created_at_utc"
        ).fetchall()
        return [dict(row) for row in rows]

    def recent_signals(self, limit: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM xau_signals ORDER BY created_at_utc DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_event(self, signal_id: str, at_utc: str, kind: str, price: str, realized_r: str, detail: str) -> None:
        self.db.execute(
            "INSERT INTO xau_signal_events(signal_id, at_utc, kind, price, realized_r, detail) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (signal_id, at_utc, kind, price, realized_r, detail),
        )

    def add_decision(self, at_utc: str, signal_id: str | None, rule_ids: list[str], accepted: bool, grade: str, detail: str) -> None:
        self.db.execute(
            "INSERT INTO xau_decisions(at_utc, signal_id, rule_ids, accepted, grade, detail) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (at_utc, signal_id, json.dumps(rule_ids), 1 if accepted else 0, grade, detail),
        )
        self.db.execute(
            "DELETE FROM xau_decisions WHERE id IN ("
            "SELECT id FROM xau_decisions ORDER BY id DESC LIMIT -1 OFFSET 5000)"
        )

    def lock_window(self, key: str, started: str, engine: str, reason: str) -> None:
        self.db.execute(
            "INSERT INTO xau_windows(window_key, started_at_utc, engine, reason) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(window_key) DO NOTHING",
            (key, started, engine, reason),
        )

    def window_engine(self, key: str) -> str | None:
        row = self.db.execute("SELECT engine FROM xau_windows WHERE window_key=?", (key,)).fetchone()
        return None if row is None else str(row["engine"])

    def save_news(self, fetched_at: str, source: str, payload: str) -> None:
        self.db.execute(
            "INSERT INTO xau_news(fetched_at_utc, source, payload_json) VALUES(?, ?, ?)",
            (fetched_at, source, payload),
        )

    def latest_news(self) -> dict | None:
        row = self.db.execute(
            "SELECT fetched_at_utc, source, payload_json FROM xau_news ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return None if row is None else dict(row)

    def note_feed(self, at_utc: str, feed: str, ok: bool, detail: str) -> None:
        self.db.execute(
            "INSERT INTO xau_feed_health(at_utc, feed, ok, detail) VALUES(?, ?, ?, ?)",
            (at_utc, feed, 1 if ok else 0, detail),
        )

    def tracked_through(self, signal_id: str) -> str | None:
        return self.meta_get("track:" + signal_id)

    def mark_tracked(self, signal_id: str, open_time: str) -> None:
        self.meta_set("track:" + signal_id, open_time)
