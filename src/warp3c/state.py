"""Durable SQLite order journal: fail closed if an API POST has an unknown outcome."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class OrderJournal:
    def __init__(self, filename: str):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(filename, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                client_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                state TEXT NOT NULL, payload TEXT NOT NULL,
                strategy_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def close(self) -> None:
        self.db.close()

    def reserve(self, client_id: str, symbol: str, payload: dict) -> None:
        self.db.execute("INSERT INTO submissions(client_id, symbol, state, payload) VALUES(?,?,?,?)",
                        (client_id, symbol, "unknown", json.dumps(payload, sort_keys=True)))

    def confirm(self, client_id: str, strategy_id: str) -> None:
        self.db.execute("UPDATE submissions SET state='confirmed',strategy_id=?,updated_at=CURRENT_TIMESTAMP WHERE client_id=?",
                        (strategy_id, client_id))

    def unresolved(self) -> list[str]:
        return [x[0] for x in self.db.execute("SELECT client_id FROM submissions WHERE state='unknown'")]

    def reconcile(self, active: list[dict]) -> list[str]:
        ids = {str(x.get("clientId")): str(x.get("strategyId")) for x in active
               if x.get("clientId") and x.get("strategyId")}
        for client_id in self.unresolved():
            if client_id in ids:
                self.confirm(client_id, ids[client_id])
        return self.unresolved()

    def recently_submitted(self, symbol: str, hours: int = 24) -> bool:
        row = self.db.execute(
            "SELECT COUNT(*) FROM submissions WHERE symbol=? AND created_at >= datetime('now', ?)",
            (symbol, f"-{hours} hours"),
        ).fetchone()
        return bool(row and row[0])
