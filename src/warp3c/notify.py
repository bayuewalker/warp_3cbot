"""Alert outbox. A row stays pending until the sender accepts it."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def backoff_seconds(attempts: int) -> int:
    if attempts < 0:
        raise ValueError("Alert attempts cannot be negative")
    return min(3600, 5 * (2 ** attempts))


def flush_alerts(journal, deliver, *, now: datetime | None = None, delay_for=backoff_seconds) -> int:
    """Deliver each due row once. Failure keeps the row pending with a later retry."""
    moment = now or datetime.now(timezone.utc)
    delivered = 0
    for alert_id, event, detail, attempts in journal.due_alerts(moment):
        try:
            ok = bool(deliver(event, detail))
        except Exception:
            ok = False
        if ok:
            journal.mark_delivered(alert_id, moment)
            delivered += 1
            continue
        delay = delay_for(attempts)
        journal.mark_retry(
            alert_id,
            attempts=attempts + 1,
            next_at=moment + timedelta(seconds=delay),
            error="delivery_failed",
        )
    return delivered
