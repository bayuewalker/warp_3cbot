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
# Marks for holdings and open positions outside the price tape, read with the accounts.
MARK_FRESH_SECONDS = 90
MAX_MARKS = 15
STABLE = {"USDT", "USDC", "USD", "FDUSD"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def quote_age_seconds(as_of: str | None, now: datetime | None = None) -> int | None:
    if not as_of:
        return None
    try:
        stamp = datetime.fromisoformat(as_of)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    moment = now or datetime.now(timezone.utc)
    return max(0, int((moment - stamp).total_seconds()))


def quote_is_fresh(as_of: str | None, limit: int = QUOTE_FRESH_SECONDS, now: datetime | None = None) -> bool:
    """A retained ticker is not a current mark once it is older than the quote limit."""
    age = quote_age_seconds(as_of, now)
    return age is not None and age <= limit


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
        self.marks: dict[str, dict] = {}
        self.marks_at: str | None = None

    def note_marks(self, rows: dict) -> None:
        if rows:
            self.marks = rows
            self.marks_at = _now()

    def fresh_marks(self) -> dict[str, dict]:
        """Tape prices inside the quote limit, then account marks inside their own limit."""
        merged: dict[str, dict] = {}
        if quote_is_fresh(self.marks_at, MARK_FRESH_SECONDS):
            merged.update(self.marks)
        if quote_is_fresh(self.prices_at):
            merged.update(self.prices)
        return merged

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


class DashboardReads:
    """Read-only 3Commas refresh. It never runs on the monitoring thread."""

    def __init__(self, refresh, interval: float = ACCOUNT_SECONDS):
        self.refresh = refresh
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="warp-accounts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                pass
            self._stop.wait(self.interval)


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


def iter_server_events(inbox: queue.Queue, bus: LiveBus, stop: threading.Event, alive=None):
    """Yield events until stop, or until alive() is false. alive() is the session check."""
    if alive is not None and not alive():
        return
    yield encode_event("hello", {"ts": _now()})
    while not stop.is_set():
        if alive is not None and not alive():
            return
        try:
            payload = inbox.get(timeout=bus.heartbeat)
        except queue.Empty:
            if alive is not None and not alive():
                return
            payload = encode_event("heartbeat", {"ts": _now()})
        if alive is not None and not alive():
            return
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


def mark_symbols(accounts: list, rows: list[dict], skip: set[str]) -> list[str]:
    """USDT pairs for held assets and open positions that the price tape does not cover."""
    wanted: list[str] = []
    for account in accounts:
        held = getattr(account, "holdings", ())
        for entry in () if callable(held) else held or ():
            asset = entry[0] if isinstance(entry, tuple) and entry else None
            if isinstance(asset, str) and asset not in STABLE:
                wanted.append(f"{asset}USDT")
    for row in rows:
        if row.get("quote_asset") == "USDT" and row.get("symbol"):
            wanted.append(str(row["symbol"]))
    return [symbol for symbol in dict.fromkeys(wanted) if symbol not in skip][:MAX_MARKS]


def poll_marks(client: httpx.Client, symbols: list[str]) -> dict[str, dict]:
    """One public ticker read per symbol, so an unlisted asset cannot fail the batch."""
    found: dict[str, dict] = {}
    for symbol in symbols[:MAX_MARKS]:
        try:
            response = client.get("/api/v3/ticker/price", params={"symbol": symbol})
            if response.status_code != 200:
                continue
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            continue
        last = _decimal(payload.get("price") if isinstance(payload, dict) else None, positive=True)
        if last is not None:
            found[symbol] = {"last": format(last, "f"), "change_pct": None}
    return found


EVENTS: deque[dict] = deque(maxlen=40)


def remember_event(payload: dict) -> None:
    EVENTS.append(payload)


def recent_events() -> list[dict]:
    return list(EVENTS)


BUS = LiveBus()
