"""JSON logs and optional HTTPS alerts. Never include credentials or signed headers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

LOG = logging.getLogger("warp3c")
_HIDDEN = {"api_key", "api_secret", "secret", "signature", "authorization", "token", "webhook"}


def log_event(event: str, **fields: object) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **{key: value for key, value in fields.items() if key.lower() not in _HIDDEN},
    }
    LOG.info("%s", json.dumps(payload, default=str, separators=(",", ":")))


def send_telegram(token: str, chat_id: str, event: str, detail: str, *, transport=None) -> bool:
    """POST to Telegram. The bot token is not written to the log."""
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {"chat_id": chat_id, "text": f"{event}: {detail[:400]}"}
    try:
        with httpx.Client(transport=transport, timeout=5) as client:
            response = client.post(url, json=body)
            response.raise_for_status()
    except httpx.HTTPError:
        log_event("alert_failed", alert_event=event, channel="telegram")
        return False
    return True


def send_alert(url: str, event: str, detail: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        log_event("alert_skipped", reason="webhook_not_https")
        return False
    body = {"event": event, "detail": detail[:500], "ts": datetime.now(timezone.utc).isoformat()}
    try:
        response = httpx.post(url, json=body, timeout=5)
        response.raise_for_status()
    except httpx.HTTPError:
        log_event("alert_failed", alert_event=event)
        return False
    return True
