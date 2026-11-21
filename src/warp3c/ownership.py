"""WARP-owned bot actions. A foreign 3Commas bot is refused before any write."""

from __future__ import annotations

import re
from datetime import datetime, timezone

PREFIX = "WARP-"
_ID = re.compile(r"^[0-9a-f]{8}$")
ACTIONS = {"pause", "resume", "close", "delete"}


class NotOwned(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def warp_name(warp_id: str) -> str:
    if not _ID.fullmatch(warp_id or ""):
        raise ValueError("Invalid WARP id")
    return PREFIX + warp_id


def require_owned(row: dict | None, remote: dict | None = None) -> dict:
    if row is None or not str(row.get("name") or "").startswith(PREFIX):
        raise NotOwned("not_owned")
    if not str(row["name"]).removeprefix(PREFIX) or row["name"] != PREFIX + str(row.get("warp_id") or ""):
        raise NotOwned("not_owned")
    if remote is None:
        return row
    remote_name = str(remote.get("name") or "")
    if not remote_name.startswith(PREFIX) or remote_name != row["name"]:
        raise NotOwned("foreign_bot")
    remote_code = remote.get("code")
    if row.get("external_code") and remote_code and remote_code != row["external_code"]:
        raise NotOwned("foreign_bot")
    return row


def _fetch(broker, row: dict) -> dict:
    code = row.get("external_code")
    if not code:
        raise NotOwned("not_owned")
    if row["product"] == "dca_bot":
        return broker.get_dca_bot(code)
    if row["product"] == "grid_bot":
        return broker.get_grid_bot(code)
    raise NotOwned("unsupported_product")


def _running(remote: dict) -> bool:
    """True only when the cabinet says the bot is active. Pause toggles, so a paused bot must not be paused again."""
    if remote.get("active") is True:
        return True
    if remote.get("paused") is True:
        return False
    return str(remote.get("status") or "").lower() in {"active", "running", "started"}


def _write(broker, row: dict, action: str, remote: dict) -> None:
    code = row["external_code"]
    product = row["product"]
    if product == "dca_bot":
        if action == "pause":
            broker.pause_dca_bot(code)
        elif action == "resume":
            broker.resume_dca_bot(code)
        elif action == "close":
            broker.close_dca_positions(code)
        elif action == "delete":
            if _running(remote):
                broker.pause_dca_bot(code)
            broker.delete_dca_bot(code)
        else:
            raise NotOwned("unsupported_action")
        return
    if product == "grid_bot":
        if action == "pause":
            broker.stop_grid_bot(code, close=False)
        elif action == "resume":
            broker.restart_grid_bot(code)
        elif action == "close":
            broker.stop_grid_bot(code, close=True)
        elif action == "delete":
            if _running(remote):
                broker.stop_grid_bot(code, close=False)
            broker.delete_grid_bot(code)
        else:
            raise NotOwned("unsupported_action")
        return
    raise NotOwned("unsupported_product")


def apply_action(broker, journal, warp_id: str, action: str) -> dict:
    """Pause, resume, close, or delete one bot. Unknown ids never reach the broker."""
    if action not in ACTIONS:
        raise NotOwned("unsupported_action")
    row = journal.warp_bot(warp_id)
    require_owned(row, None)
    if row["product"] not in {"dca_bot", "grid_bot"}:
        raise NotOwned("unsupported_product")
    remote = _fetch(broker, row)
    require_owned(row, remote)
    _write(broker, row, action, remote)
    status = {"pause": "paused", "resume": "active", "close": "closed", "delete": "deleted"}[action]
    now = datetime.now(timezone.utc).isoformat()
    journal.update_warp_bot(warp_id, status=status, updated_at=now)
    return {"warp_id": warp_id, "action": action, "status": status, "name": row["name"]}
