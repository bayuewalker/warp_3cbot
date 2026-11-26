"""Alert outbox. A row stays pending until the sender accepts it."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

ALERTS_PER_LOOP = 3
ALERT_LOOP_SECONDS = 10


class Delivery:
    """One delivery attempt. ``skipped`` is not a success and is not a retry."""

    __slots__ = ("ok", "skipped", "retry_after")

    def __init__(self, *, ok: bool = False, skipped: bool = False, retry_after: int | None = None):
        self.ok = bool(ok) and not skipped
        self.skipped = skipped
        self.retry_after = retry_after

    def __bool__(self) -> bool:
        return self.ok


def channel_configured(cfg) -> bool:
    if cfg is None:
        return False
    return bool(getattr(cfg, "alert_webhook", "") or getattr(cfg, "telegram_bot_token", ""))


def enqueue_incident(
    journal,
    event: str,
    detail: str,
    *,
    now: datetime | None = None,
    channel: bool = True,
) -> bool:
    """Queue one incident. With no channel, a skipped row suppresses repeats of the same event."""
    if journal is None:
        return False
    if not channel and journal.latest_alert_state(event) == "skipped_no_channel":
        return False
    return bool(journal.enqueue_alert(event, detail[:400], now=now))


def backoff_seconds(attempts: int) -> int:
    if attempts < 0:
        raise ValueError("Alert attempts cannot be negative")
    return min(3600, 5 * (2 ** attempts))


def _retry_delay(outcome, attempts: int, delay_for) -> int:
    hinted = getattr(outcome, "retry_after", None)
    if type(hinted) is int and hinted >= 0:
        return min(3600, hinted)
    return min(3600, int(delay_for(attempts)))


def flush_alerts(
    journal,
    deliver,
    *,
    now: datetime | None = None,
    delay_for=backoff_seconds,
    monotonic=time.monotonic,
) -> int:
    """Deliver due rows. At most three sends, and no new send after ten seconds."""
    moment = now or datetime.now(timezone.utc)
    note_halt = getattr(journal, "note_halt", None)
    if callable(note_halt):
        note_halt()
    started = monotonic()
    delivered = 0
    handled = 0
    for alert_id, event, detail, attempts in journal.due_alerts(moment):
        if handled >= ALERTS_PER_LOOP or monotonic() - started >= ALERT_LOOP_SECONDS:
            break
        handled += 1
        try:
            outcome = deliver(event, detail)
        except Exception:
            outcome = Delivery(ok=False)
        if getattr(outcome, "skipped", False):
            journal.mark_skipped_no_channel(alert_id, moment)
            continue
        if outcome:
            journal.mark_delivered(alert_id, moment)
            delivered += 1
            continue
        delay = _retry_delay(outcome, attempts, delay_for)
        journal.mark_retry(
            alert_id,
            attempts=attempts + 1,
            next_at=moment + timedelta(seconds=delay),
            error="delivery_failed",
        )
    return delivered
