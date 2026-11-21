"""Authenticated server-sent events and a small public price poll.

Prices come from Binance public spot REST, about every few seconds. A websocket
would hold a second always-on connection on a shared-cpu-1x Machine. The poll
stays inside the 1–5s band and the 30s quote freshness limit.
"""

from __future__ import annotations

import json
import queue
import threading
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal

import httpx

HEARTBEAT_SECONDS = 15
PRICE_SECONDS = 3
SCREEN_SECONDS = 15
ACCOUNT_SECONDS = 30
MAX_SUBSCRIBERS = 8
QUOTE_FRESH_SECONDS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode_event(event: str, data: dict) -> bytes:
    body = json.dumps(data, default=str, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n".encode()


class LiveBus:
    def __init__(self, heartbeat: float = HEARTBEAT_SECONDS, max_subscribers: int = MAX_SUBSCRIBERS):
        self.heartbeat = heartbeat
        self.max_subscribers = max_subscribers
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self.prices: dict[str, dict] = {}
        self.prices_at: str | None = None
        self.prices_error: str | None = None

    def subscribe(self) -> queue.Queue | None:
        inbox: queue.Queue = queue.Queue(maxsize=32)
        with self._lock:
            if len(self._subs) >= self.max_subscribers:
                return None
            self._subs.append(inbox)
        return inbox

    def unsubscribe(self, inbox: queue.Queue) -> None:
        with self._lock:
            if inbox in self._subs:
                self._subs.remove(inbox)

    def publish(self, event: str, data: dict) -> None:
        payload = encode_event(event, data)
        with self._lock:
            targets = list(self._subs)
        for inbox in targets:
            _offer(inbox, payload)

    def emit_snapshot(self, routes: dict) -> None:
        self.publish("snapshot", {"routes": routes, "published_at": _now()})

    def note_prices(self, rows: dict, error: str | None) -> None:
        if rows:
            self.prices = rows
            self.prices_at = _now()
            self.prices_error = None
        elif error:
            self.prices_error = error
        self.publish(
            "prices",
            {
                "symbols": self.prices,
                "as_of": self.prices_at,
                "error": self.prices_error,
                "limit_seconds": QUOTE_FRESH_SECONDS,
            },
        )


def _offer(inbox: queue.Queue, payload: bytes) -> None:
    try:
        inbox.put_nowait(payload)
    except queue.Full:
        try:
            inbox.get_nowait()
        except queue.Empty:
            return
        try:
            inbox.put_nowait(payload)
        except queue.Full:
            return


def iter_server_events(inbox: queue.Queue, bus: LiveBus, stop: threading.Event):
    yield encode_event("hello", {"ts": _now()})
    while not stop.is_set():
        try:
            payload = inbox.get(timeout=bus.heartbeat)
        except queue.Empty:
            payload = encode_event("heartbeat", {"ts": _now()})
        yield payload


def parse_tickers(payload: object, wanted: set[str]) -> dict[str, dict]:
    rows = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else None
    if rows is None:
        raise ValueError("Ticker payload is not a list")
    found: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "")
        if wanted and symbol not in wanted:
            continue
        last = _decimal(row.get("lastPrice"), positive=True)
        change = _decimal(row.get("priceChangePercent"))
        if last is None:
            continue
        found[symbol] = {"last": format(last, "f"), "change_pct": None if change is None else format(change, "f")}
    return found


def _decimal(value: object, *, positive: bool = False) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    if not number.is_finite() or (positive and number <= 0):
        return None
    return number


def poll_prices(client: httpx.Client, symbols: list[str]) -> dict[str, dict]:
    names = [symbol for symbol in symbols if symbol][:12]
    if not names:
        return {}
    response = client.get("/api/v3/ticker/24hr", params={"symbols": json.dumps(names, separators=(",", ":"))})
    response.raise_for_status()
    return parse_tickers(response.json(), set(names))


EVENTS: deque[dict] = deque(maxlen=40)


def remember_event(payload: dict) -> None:
    EVENTS.append(payload)


def recent_events() -> list[dict]:
    return list(EVENTS)


BUS = LiveBus()
