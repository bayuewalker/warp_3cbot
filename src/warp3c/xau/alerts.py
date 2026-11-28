"""Plain-text XAU alerts. The sink is the existing outbox. No token and no Telegram URL."""

from __future__ import annotations

from datetime import datetime, timedelta

from warp3c.telemetry import log_event

from .clock import format_wib

_CARD = "🎯 Verdict"

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
    return event, _fit(event, xau_card(signal))


def xau_card(signal: dict) -> str:
    """Labeled rows from the setup. No lot, dollar risk, account reference, or id."""
    side = "BUY" if signal.get("direction") == "long" else "SELL"
    icon = "🟢" if side == "BUY" else "🔴"
    kind = {"limit": "LIMIT", "stop": "STOP", "market": "MKT"}.get(str(signal.get("entry_type") or ""))
    action = f"{icon} {side}" + (f" {kind}" if kind else "")
    rows = [f"{_CARD} — XAUUSD", f"Action: {action}"]
    grade = str(signal.get("grade") or "").strip()
    if grade:
        rows.append(f"Grade: {grade}")
    bias = str(signal.get("bias") or "").strip()
    if bias:
        rows.append(f"Bias: {_clip(bias, 90)}")
    for label, key in (("Entry", "entry"), ("SL", "sl"), ("TP1", "tp1")):
        shown = _plain(signal.get(key))
        if shown:
            rows.append(f"{label}: {shown}")
    tp2 = signal.get("tp2")
    if tp2 not in (None, "", "-"):
        shown = _plain(tp2)
        if shown:
            rows.append(f"TP2: {shown}")
    risk = _plain(signal.get("risk_pct"))
    if risk:
        rows.append(f"Risk: {risk}%")
    reason = str(signal.get("reason") or "").strip()
    if reason:
        rows.append(f"Why: {_clip(reason, 90)}")
    rows.append("Next: Signal only. No order.")
    text = "\n".join(rows)
    return text if len(text) <= 400 else _clip(text, 400)


def _plain(value: object) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or None


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
    if detail.startswith(_CARD):
        if len(detail) <= 400:
            return detail
        return _clip(detail, 400)
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
        budget = detail if detail.startswith(_CARD) else f"{event}: {detail}"
        if len(budget) > 400:
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
