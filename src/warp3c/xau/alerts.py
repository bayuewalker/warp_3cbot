"""Plain-text XAU alerts. The sink is the existing outbox. No token and no Telegram URL."""

from __future__ import annotations

from datetime import datetime, timedelta

from warp3c.telemetry import log_event

from .clock import format_wib

SIGNAL = 0
LIFECYCLE = 1
OPS = 2
SUMMARY = 3
CAP = 20


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def new_signal(signal: dict) -> tuple[str, str]:
    side = "BUY" if signal["direction"] == "long" else "SELL"
    kind = {"limit": "LIMIT", "stop": "STOP", "market": "MKT"}.get(signal["entry_type"], "MKT")
    event = f"XAU {side} {kind} {signal['grade']} #{signal['id6']}"
    tp2 = signal.get("tp2") or "-"
    rr2 = signal.get("rr_tp2") or "-"
    reason = _clip(str(signal.get("reason") or ""), 90)
    detail = "\n".join([
        f"{signal['model']} · {signal['window']} · conf {signal['confidence']}",
        f"E {signal['entry']} SL {signal['sl']} (${signal['r_usd']}) TP1 {signal['tp1']} {signal['rr_tp1']}R TP2 {tp2} {rr2}R",
        f"Risk {signal['risk_pct']}% · ex {signal['lot']} lot/{signal['ref']} · valid {signal['valid_wib']} WIB",
        reason,
        f"News clear to {signal['news_wib']} WIB · signal only, no order",
    ])
    return event, _fit(event, detail)


def follow_up(kind: str, id6: str, price: str, realized_r: str, status: str) -> tuple[str, str]:
    event = f"XAU {kind} #{id6}"
    detail = f"{price} · {realized_r}R · {status}"
    return event, _fit(event, detail)


def daily(day: str, realized_r: str, sent: int, journal: int) -> tuple[str, str]:
    event = f"XAU daily {day}"
    detail = f"sent {sent} journal {journal} realized {realized_r}R · signal only"
    return event, _fit(event, detail)


def weekly(label: str, realized_r: str, sent: int) -> tuple[str, str]:
    event = f"XAU weekly {label}"
    detail = f"sent {sent} realized {realized_r}R · signal only"
    return event, _fit(event, detail)


def ops(name: str, key: str, detail: str = "") -> tuple[str, str]:
    event = f"XAU {name} #{key}"
    text = detail or name
    return event, _fit(event, text)


def _fit(event: str, detail: str) -> str:
    prefix = f"{event}: "
    room = 400 - len(prefix)
    if room < 1:
        return ""
    if len(detail) <= room:
        return detail
    return _clip(detail, room)


class Outbox:
    """At most 20 XAU rows per rolling hour. Lower priority loses first inside a batch."""

    def __init__(self, sink, cap: int = CAP):
        self.sink = sink
        self.cap = cap
        self.sent: list[tuple[datetime, int, str]] = []

    def submit(self, now: datetime, priority: int, event: str, detail: str) -> bool:
        self._trim(now)
        if len(self.sent) >= self.cap:
            log_event("xau_alert_dropped", name=event, priority=priority)
            return False
        full = f"{event}: {detail}"
        if len(full) > 400:
            detail = _fit(event, detail)
        accepted = bool(self.sink(event, detail))
        if accepted:
            self.sent.append((now, priority, event))
        return accepted

    def submit_batch(self, now: datetime, items: list[tuple[int, str, str]]) -> list[str]:
        """Keep higher-priority rows (lower number) until the hourly cap. Return dropped events."""
        self._trim(now)
        room = self.cap - len(self.sent)
        ordered = sorted(items, key=lambda item: item[0])
        kept = ordered[: max(room, 0)]
        dropped = ordered[max(room, 0) :]
        for priority, event, detail in kept:
            self.submit(now, priority, event, detail)
        for _priority, event, _detail in dropped:
            log_event("xau_alert_dropped", name=event)
        return [event for _priority, event, _detail in dropped]

    def _trim(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=1)
        self.sent = [row for row in self.sent if row[0] > cutoff]


def id6(signal_id: str) -> str:
    return signal_id[-6:]


def valid_label(moment: datetime | None) -> str:
    if moment is None:
        return "--:--"
    return format_wib(moment)
