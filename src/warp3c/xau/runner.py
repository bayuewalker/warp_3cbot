"""XAU thread. A failure here never reaches the DCA loop."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from warp3c.telemetry import log_event

from .alerts import (
    LIFECYCLE,
    OPS,
    SIGNAL,
    SUMMARY,
    Outbox,
    daily,
    follow_up,
    id6,
    new_signal,
    ops,
    weekly,
)
from .basis import MIN_SAMPLES, BasisBook
from .candles import prepare, shift
from .clock import (
    ema_entry_open,
    format_wib,
    market_closed,
    smc_entry_open,
    trading_day_start,
    week_start,
    wib,
    window_at,
)
from .config import XauConfigError, XauSettings
from .engines.ema import evaluate_ema
from .engines.scalp import evaluate_scalp
from .engines.smc import evaluate_smc
from .feeds import FeedUnavailable, HttpFeeds
from .gate import GateContext, evaluate
from .news import NewsBook
from .outcome import dump_tags, on_bar, position_from_row
from .profiles import ProfileError, example_lot, load_profile, risk_pct
from .riskbook import RiskBook
from .selector import select_engine
from .stats import grouped, paused_models
from .status import HUB
from .store import XauStore, new_id
from .structure import atr_last, bias, dol, ema_series, m15_state, structure_events, swings

D = Decimal


def _clock_of(clock):
    if clock is None:
        return lambda: datetime.now(timezone.utc)
    if callable(clock):
        return clock
    return clock.now


class XauRunner:
    def __init__(self, db_path: str, alert_sink, settings: XauSettings, profile, *, clock, feeds, cycle):
        self.db_path = db_path
        self.settings = settings
        self.profile = profile
        self.clock = clock
        self.feeds = feeds
        self.cycle_fn = cycle
        self.stop_event = threading.Event()
        self.store = XauStore(db_path)
        self.basis = BasisBook(settings.basis_max)
        self.news = NewsBook(settings.news_max_age_h)
        self.outbox = Outbox(alert_sink)
        self.hub = HUB
        self.hub.clock = clock
        self.hub.cycle_seconds = settings.cycle_seconds
        self._backoff = 30
        self._stall_open = False
        self._feed_down = False
        self._basis_down = False
        self._basis_alerted = False
        self._episode = 0
        self._error = ""
        self._started_at = clock() if callable(clock) else datetime.now(timezone.utc)
        self._live_feed: str | None = None
        self._last_bar_at: datetime | None = None
        self._basis_state = None
        self._warming = False
        self.state = "starting"
        self.thread = threading.Thread(target=self._loop, name="xau", daemon=True)

    def stop(self, timeout: float = 2) -> None:
        self.stop_event.set()
        self.thread.join(timeout)
        if self.feeds is not None:
            try:
                self.feeds.close()
            except Exception:
                log_event("xau_feed_close_failed", error="feed_close")
        current = dict(self.hub.snapshot())
        current["state"] = "stopped"
        self.hub.publish(current)

    def _loop(self) -> None:
        self._beat()
        while not self.stop_event.is_set():
            sleep_for = self.settings.cycle_seconds
            try:
                self._note_stall()
                if self.cycle_fn is not None:
                    self.cycle_fn()
                else:
                    self.cycle()
                self._backoff = 30
                if self.state not in {"feed_unavailable", "config_error", "warming"}:
                    self.state = "running"
            except Exception as exc:
                self.state = "degraded"
                self._error = type(exc).__name__[:120]
                log_event("xau_cycle_failed", error=self._error)
                sleep_for = self._backoff
                self._backoff = min(600, self._backoff * 2)
            finally:
                self._beat()
            self._wait(sleep_for)

    def _wait(self, seconds: float) -> None:
        deadline = datetime.now(timezone.utc).timestamp() + seconds
        while not self.stop_event.is_set():
            self._beat()
            remaining = deadline - datetime.now(timezone.utc).timestamp()
            if remaining <= 0:
                return
            self.stop_event.wait(min(5, remaining))

    def _beat(self) -> None:
        snap = self._snapshot_body()
        snap["heartbeat_at"] = self.clock().isoformat()
        self.hub.publish(snap)

    def _note_stall(self) -> None:
        if self.hub.snapshot().get("state") != "stalled":
            self._stall_open = False
            return
        if self._stall_open:
            return
        self._stall_open = True
        self._episode += 1
        event, detail = ops("stalled", str(self._episode), "heartbeat late")
        self.outbox.submit(self.clock(), OPS, event, detail)

    def cycle(self) -> None:
        now = self.clock()
        self.store.migrate()
        self._announce_start(now)
        parent = Path(self.db_path).parent
        halt_global = (parent / "HALT").exists()
        halt_xau = (parent / "XAU_HALT").exists()
        if self.feeds is None:
            self._feed_unavailable(now)
            return
        try:
            feed_name, raw = self.feeds.book()
            spot = self.feeds.spot()
            dxy = self.feeds.dxy()
        except FeedUnavailable:
            self._feed_unavailable(now)
            return
        self.store.note_feed(now.isoformat(), feed_name, True, "ok")
        if self._feed_down:
            self._feed_down = False
            event, detail = ops("feed_recovered", str(self._episode), feed_name)
            self.outbox.submit(now, OPS, event, detail)
        cleaned = prepare(raw, now)
        self._refresh_news(now)
        view = self.news.view(now)
        if view.blocked and self.store.meta_get("news:" + wib(now).date().isoformat()) is None:
            event, detail = ops("news_stale", wib(now).date().isoformat(), "calendar stale")
            if self.outbox.submit(now, OPS, event, detail):
                self.store.meta_set("news:" + wib(now).date().isoformat(), event)
        m1 = cleaned.bars.get("1m", ())
        perp_close = m1[-1].close if m1 else None
        basis = self.basis.observe(perp_close, spot, now)
        self._live_feed = feed_name
        if m1:
            self._last_bar_at = m1[-1].open_time
        self._basis_state = basis
        warming = self._watch_basis(basis, now)
        shifted = {
            name: shift(bars, basis.value or D("0")) if basis.healthy else bars
            for name, bars in cleaned.bars.items()
        }
        self._track(shifted, now)
        window = window_at(now)
        engine = self._locked_engine(window, shifted, now)
        dirty = bool(cleaned.gaps) or cleaned.stale_m5 or not basis.healthy
        if warming:
            self.state = "warming"
        elif dirty:
            self.state = "degraded"
        else:
            self.state = "running"
        if not halt_global and not halt_xau and not dirty and not view.blocked and not market_closed(now):
            self._maybe_signal(engine, window, shifted, now, feed_name, basis.value, dxy, basis.healthy, dirty)
        self._maybe_summaries(now)
        self._error = ""

    def _watch_basis(self, basis, now: datetime) -> bool:
        """Warm-up is the sample requirement plus five minutes, measured from start.

        few_samples during that window reports warming and does not alert.
        Any unhealthy basis after warm-up, or after a healthy reading, alerts once.
        Recovery alerts once, and only after an alert was sent.
        """
        deadline = self._started_at + timedelta(minutes=MIN_SAMPLES) + timedelta(minutes=5)
        warming = now < deadline and not basis.healthy and basis.reason == "few_samples"
        self._warming = warming
        if warming:
            return True
        if not basis.healthy:
            if not self._basis_alerted:
                self._basis_alerted = True
                self._basis_down = True
                self._episode += 1
                event, detail = ops("basis_unhealthy", str(self._episode), basis.reason)
                self.outbox.submit(now, OPS, event, detail)
            return False
        if self._basis_alerted:
            event, detail = ops("basis_recovered", str(self._episode), "ok")
            self.outbox.submit(now, OPS, event, detail)
            self._basis_alerted = False
        self._basis_down = False
        return False

    def _feed_unavailable(self, now: datetime) -> None:
        self.state = "feed_unavailable"
        if not self._feed_down:
            self._feed_down = True
            self._episode += 1
            event, detail = ops("feed_unavailable", str(self._episode), "no candle source")
            self.outbox.submit(now, OPS, event, detail)
        self.store.note_feed(now.isoformat(), "none", False, "unavailable")

    def _announce_start(self, now: datetime) -> None:
        """One XAU started alert per WIB date. Delivery stays on the shared outbox."""
        key = "started:" + wib(now).date().isoformat()
        if self.store.meta_get(key):
            return
        event, detail = ops("started", wib(now).date().isoformat(), "signal only")
        if self.outbox.submit(now, OPS, event, detail):
            self.store.meta_set(key, event)

    def _news_clear(self, now: datetime, engine: str, bars) -> bool:
        """N2, N5, N6. SMC also waits for a fresh M15 BOS or CHoCH after T+20."""
        if self.news.view(now).blocked or self.news.in_window(now, engine) is not None:
            return False
        if engine != "SMC":
            return True
        m15 = bars.get("15m", ())
        events = structure_events(m15, 2) if m15 else []
        last = m15[events[-1].index].open_time if events else None
        return not self.news.smc_rearm_needed(now, last)

    def _refresh_news(self, now: datetime) -> None:
        if not self.news.due(now) or self.feeds is None:
            return
        payload = self.feeds.ff_calendar()
        if payload is None:
            return
        self.news.note_fetch(now, payload)
        self.store.save_news(now.isoformat(), "ff", json.dumps(payload)[:200000])

    def _locked_engine(self, window, bars, now) -> str:
        if window.kind == "off" or window.kind == "closed":
            return "NO_TRADE"
        saved = self.store.window_engine(window.key)
        if saved:
            return saved
        if window.kind == "ema":
            choice, reason = "EMA", "K3"
        else:
            choice, reason = self._select(bars)
        self.store.lock_window(window.key, now.isoformat(), choice, reason)
        return choice

    def _select(self, bars) -> tuple[str, str]:
        d1 = bars.get("1d", ())
        h4 = bars.get("4h", ())
        h1 = bars.get("1h", ())
        m15 = bars.get("15m", ())
        d1_bias = bias(d1) if d1 else "range"
        h4_bias = bias(h4) if h4 else "range"
        atr_h1 = atr_last(h1) if h1 else None
        price = h1[-1].close if h1 else (m15[-1].close if m15 else None)
        target = dol(h4, "long" if d1_bias == "bullish" else "short") if h4 else None
        distance = abs(target - price) if target is not None and price is not None else None
        m15_bias = m15_state(m15, atr_last(m15) if m15 else None) if m15 else "chop"
        m15_dir = m15_bias in {"bullish", "bearish"}
        fresh = _fresh(h4, d1_bias)
        poi = bool(h1) and atr_h1 is not None
        # E2. Veto the H1 BOS that runs against this M15 bias.
        veto = _h1_veto(h1, m15_bias if m15_dir else "")
        return select_engine(
            d1_bias=d1_bias, h4_bias=h4_bias, fresh_reversal=fresh, h1_poi=poi,
            dol_distance=distance, atr_h1=atr_h1, m15_directional=m15_dir, h1_veto=veto,
        )

    def _maybe_signal(self, engine, window, bars, now, feed, basis, dxy, basis_ok, dirty) -> None:
        profile = self.profile
        book = RiskBook(self.store.signals(), profile, now)
        direction = _direction(bars)
        if direction is None:
            return
        spread = self.settings.spread_assumed
        valid = window.end_wib.astimezone(timezone.utc)
        bias_text = _bias_text(bars, direction)
        risk = D("0.40")
        setup = None
        if engine == "SMC" and smc_entry_open(now):
            risk = D("0.60")
            setup = evaluate_smc(
                m15=bars.get("15m", ()), h1=bars.get("1h", ()), h4=bars.get("4h", ()),
                direction=direction, atr_h1=atr_last(bars.get("1h", ())), spread=spread,
                fresh_reversal=_fresh(bars.get("4h", ()), bias(bars.get("1d", ())) if bars.get("1d") else "range"),
                aligned=_aligned(bars), bias_text=bias_text, window=window.name, valid_until=valid,
                risk_pct=risk, in_kz=True, window_start=window.start_wib.astimezone(timezone.utc),
                m5=bars.get("5m", ()),
            )
        elif engine == "EMA" and ema_entry_open(now):
            risk = D("0.50")
            highs = [point.price for point in swings(bars.get("15m", ()), 2) if point.kind == "high"]
            lows = [point.price for point in swings(bars.get("15m", ()), 2) if point.kind == "low"]
            setup = evaluate_ema(
                bars.get("15m", ()), spread=spread, risk_pct=risk, window=window.name,
                valid_until=valid, in_window=True, bias_text=bias_text,
                swing_highs=highs, swing_lows=lows,
                window_start=window.start_wib.astimezone(timezone.utc),
            )
        elif engine == "SCALP":
            if self.news.scalp_window_blocked(now):
                return
            m15 = bars.get("15m", ())
            or_high, or_low = _or15(m15)
            setup = evaluate_scalp(
                m5=bars.get("5m", ()), m1=bars.get("1m", ()), direction=direction, spread=spread,
                risk_pct=D("0.25"), window=window.name, valid_until=valid, bias_text=bias_text,
                or_high=or_high, or_low=or_low,
            )
        if setup is None:
            return
        if setup.engine == "SMC":
            wide = _wide_week(self.news, bars.get("1d", ()), now)
            tier = D("0.35") if wide else (D("0.60") if setup.grade == "A+" else D("0.40"))
            setup.risk_pct = tier
        elif setup.engine == "EMA":
            setup.risk_pct = D("0.50")
        setup.risk_pct = risk_pct(setup.risk_pct, profile)
        allowed, rule = book.allow(setup.engine, setup.model, window.key)
        ctx = GateContext(
            window_engine=engine if engine in {"SMC", "EMA"} else None,
            now=now, feed_fresh=not dirty, basis_healthy=basis_ok, dirty=dirty,
            news_clear=self._news_clear(now, setup.engine, bars),
            risk_clear=allowed, smc_open=smc_entry_open(now), ema_open=ema_entry_open(now),
        )
        gated = evaluate(setup, ctx)
        if gated.sent and not allowed:
            gated.sent = False
            gated.failures = tuple(gated.failures) + ((rule,) if rule else ())
        equity = book.equity
        risk_usd = equity * gated.risk_pct / D("100")
        gated.example_lot = example_lot(risk_usd, gated.r_usd, profile)
        gated.feed = feed
        gated.basis = basis
        signal_id = new_id(now)
        gated.signal_id = signal_id
        event = ""
        if gated.sent:
            payload = _render_input(gated, equity, dxy)
            event, detail = new_signal(payload)
            self.outbox.submit(now, SIGNAL, event, detail)
        self._persist(gated, now, event, dxy)
        self.store.add_decision(
            now.isoformat(), signal_id, list(gated.checklist.keys()), gated.sent, gated.grade,
            ",".join(gated.failures),
        )
        if rule == "U5":
            day = wib(now).date().isoformat()
            paused, text = ops("pause24h", day, "four losses")
            self.outbox.submit(now, OPS, paused, text)
        if gated.model in book.paused:
            day = wib(now).date().isoformat()
            paused, text = ops(f"model_paused {gated.model}", day, "rolling 30")
            self.outbox.submit(now, OPS, paused, text)

    def _persist(self, setup, now, event, dxy) -> None:
        self.store.insert_signal({
            "id": setup.signal_id,
            "created_at_utc": now.isoformat(),
            "rules_version": self.settings.rules_version,
            "feed": setup.feed,
            "basis": None if setup.basis is None else format(setup.basis, "f"),
            "engine": setup.engine,
            "model": setup.model,
            "window": setup.window,
            "direction": setup.direction,
            "grade": setup.grade,
            "confidence": str(setup.confidence),
            "entry_type": setup.entry_type,
            "entry": format(setup.entry, "f"),
            "sl": format(setup.sl, "f"),
            "tp1": format(setup.tp1, "f"),
            "tp2": None if setup.tp2 is None else format(setup.tp2, "f"),
            "r_usd": format(setup.r_usd, "f"),
            "rr_tp1": format(setup.rr_tp1, "f"),
            "rr_tp2": None if setup.rr_tp2 is None else format(setup.rr_tp2, "f"),
            "risk_pct": format(setup.risk_pct, "f"),
            "example_lot": format(setup.example_lot, "f"),
            "valid_until": None if setup.valid_until is None else setup.valid_until.isoformat(),
            "checklist_json": json.dumps(setup.checklist, sort_keys=True),
            "reason": setup.reason,
            "bias": setup.bias,
            "dol": None if setup.dol is None else format(setup.dol, "f"),
            "dxy_note": dxy,
            "tags_json": json.dumps(setup.tags, default=str),
            "sent": 1 if setup.sent else 0,
            "tg_event": event,
            "status": "PENDING",
            "filled_at": None,
            "fill_price": None,
            "exit_at": None,
            "exit_reason": None,
            "realized_r": None,
            "mae_r": None,
            "mfe_r": None,
            "spread_assumed": format(setup.spread, "f"),
        })

    def _track(self, bars, now) -> None:
        m1 = bars.get("1m", ())
        if not m1:
            return
        for row in self.store.open_signals():
            cursor = self.store.tracked_through(row["id"])
            created = _stamp(row["created_at_utc"]) if row.get("created_at_utc") else None
            pos = position_from_row(row)
            for bar in _due_bars(m1, cursor, created):
                stamp = bar.open_time.isoformat()
                release = self.news.in_window(bar.open_time, pos.engine)
                news_key = "" if release is None else release.at.isoformat()
                pos = on_bar(
                    pos, bar,
                    news_cancel=self.news.cancel_pending(bar.open_time, pos.valid_until, created),
                    news_cut=bool(news_key),
                    news_key=news_key,
                    window_ended=pos.valid_until is not None and bar.open_time >= pos.valid_until,
                    **_management(bars, bar, pos),
                )
                self.store.mark_tracked(row["id"], stamp)
                if pos.status != row.get("status"):
                    self._emit_follow(pos, row, bar)
            tags = {}
            try:
                tags = json.loads(row.get("tags_json") or "{}")
            except json.JSONDecodeError:
                tags = {}
            self.store.update_signal(
                row["id"],
                status=pos.status,
                sl=format(pos.sl, "f"),
                fill_price=None if pos.fill_price is None else format(pos.fill_price, "f"),
                filled_at=None if pos.filled_at is None else pos.filled_at.isoformat(),
                realized_r=format(pos.realized_r, "f"),
                mae_r=format(pos.mae_r, "f"),
                mfe_r=format(pos.mfe_r, "f"),
                exit_at=getattr(pos, "exit_at", None).isoformat() if getattr(pos, "exit_at", None) else None,
                exit_reason=pos.status if pos.status not in {"PENDING", "FILLED", "TP1", "TRAIL"} else None,
                tags_json=dump_tags(pos, tags),
            )

    def _emit_follow(self, pos, row, bar) -> None:
        kind = pos.events[-1] if pos.events else pos.status
        if kind not in {"FILLED", "TP1", "TP2", "SL", "BE", "TIME", "RUNNER", "NEWS_CUT", "EXPIRED", "INVALIDATED", "CANCELLED_NEWS", "RUNNER_EMA50"}:
            return
        label = "RUNNER" if kind == "RUNNER_EMA50" else kind
        event, detail = follow_up(label, id6(row["id"]), format(bar.close, "f"), format(pos.realized_r, "f"), pos.status)
        self.outbox.submit(self.clock(), LIFECYCLE, event, detail)

    def _maybe_summaries(self, now: datetime) -> None:
        local = wib(now)
        day_key = trading_day_start(now).astimezone(timezone.utc).date().isoformat()
        if local.hour == 5 and local.minute >= 10 and self.store.meta_get("daily:" + day_key) is None:
            rows = self.store.signals()
            sent = [row for row in rows if str(row.get("sent")) == "1"]
            realized = sum((D(str(row["realized_r"])) for row in sent if row.get("realized_r")), D("0"))
            event, detail = daily(day_key, format(realized, "f"), len(sent), len(rows) - len(sent))
            if self.outbox.submit(now, SUMMARY, event, detail):
                self.store.meta_set("daily:" + day_key, event)
        if local.weekday() == 5 and local.hour >= 8 and self.store.meta_get("weekly:" + local.strftime("%G-W%V")) is None:
            label = local.strftime("%G-W%V")
            rows = self.store.signals()
            sent = [row for row in rows if str(row.get("sent")) == "1"]
            realized = sum((D(str(row["realized_r"])) for row in sent if row.get("realized_r")), D("0"))
            event, detail = weekly(label, format(realized, "f"), len(sent))
            if self.outbox.submit(now, SUMMARY, event, detail):
                self.store.meta_set("weekly:" + label, event)

    def _snapshot_body(self) -> dict:
        now = self.clock()
        window = window_at(now)
        try:
            rows = self.store.signals() if self.store._migrated else []
        except Exception:
            rows = []
        day = trading_day_start(now)
        today_rows = [row for row in rows if _stamp(row.get("created_at_utc")) >= day]
        sent = [row for row in today_rows if str(row.get("sent")) == "1"]
        opens = [row for row in rows if row.get("status") in {"PENDING", "FILLED", "TP1", "TRAIL"}]
        nxt = self.news.next_tier1(now)
        parent = Path(self.db_path).parent
        return {
            "enabled": True,
            "state": self.state,
            "heartbeat_at": None,
            "last_error": self._error[:120],
            "rules_version": self.settings.rules_version,
            "cycle_seconds": self.settings.cycle_seconds,
            "window": {
                "name": window.name,
                "start_wib": format_wib(window.start_wib, "%Y-%m-%d %H:%M"),
                "end_wib": format_wib(window.end_wib, "%Y-%m-%d %H:%M"),
            },
            "engine": self.store.window_engine(window.key) if self.store._migrated else None,
            "feed": rows[-1]["feed"] if rows else None,
            "basis": rows[-1]["basis"] if rows else None,
            "live_feed": self._live_feed,
            "last_bar_age_s": _bar_age(self._last_bar_at, now),
            "basis_value": (
                None if self._basis_state is None or self._basis_state.value is None
                else format(self._basis_state.value, "f")
            ),
            "basis_samples": None if self._basis_state is None else self._basis_state.samples,
            "basis_reason": None if self._basis_state is None else self._basis_state.reason,
            "warming": self._warming,
            "basis_healthy": self.basis.observe(None, None, now).healthy,
            "next_tier1": None if nxt is None else {"title": nxt.title, "wib": format_wib(nxt.at, "%Y-%m-%d %H:%M")},
            "halt": {"global": (parent / "HALT").exists(), "xau": (parent / "XAU_HALT").exists()},
            "today": {
                "signals_sent": len(sent),
                "journal_only": len(today_rows) - len(sent),
                "realized_r": format(sum((D(str(row["realized_r"])) for row in sent if row.get("realized_r")), D("0")), "f"),
                "attempts": sum(1 for row in sent if row.get("filled_at")),
            },
            "open_signal": None if not opens else _brief(opens[-1]),
            "recent": [_brief(row) for row in rows[-10:]],
            "stats30": grouped(rows, rolling=30).get("model", {}),
            "paused_models": paused_models(rows),
        }


def _bar_age(opened: datetime | None, now: datetime) -> int | None:
    if opened is None:
        return None
    return max(0, int((now - opened).total_seconds()))


def _management(bars, bar, pos) -> dict:
    """SR3, SR4, M18. Management runs once, on the M1 bar that closes an M15 bar."""
    m15 = bars.get("15m", ())
    closed = [item for item in m15 if item.open_time + timedelta(minutes=15) <= bar.open_time + timedelta(minutes=1)]
    if not closed:
        return {}
    latest = closed[-1]
    if bar.open_time + timedelta(minutes=1) != latest.open_time + timedelta(minutes=15):
        return {}
    points = swings(closed, 2)
    if pos.direction == "long":
        lows = [point.price for point in points if point.kind == "low"]
        swing = lows[-1] if lows else None
        lost = len(closed) >= 5 and latest.high <= max(item.high for item in closed[-5:-1])
    else:
        highs = [point.price for point in points if point.kind == "high"]
        swing = highs[-1] if highs else None
        lost = len(closed) >= 5 and latest.low >= min(item.low for item in closed[-5:-1])
    ema50 = None
    recross = False
    if len(closed) >= 50:
        series = ema_series([item.close for item in closed], 50)
        ema50 = series[-1]
        if ema50 is not None:
            previous = closed[-2].close
            if pos.direction == "long":
                recross = previous >= ema50 and latest.close < ema50
            else:
                recross = previous <= ema50 and latest.close > ema50
    return {
        "m15_close": latest,
        "swing_extreme": swing,
        "ema50": ema50,
        "ema_recross": recross,
        "lost_delivery": lost,
    }


def _brief(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "model": row.get("model"),
        "direction": row.get("direction"),
        "grade": row.get("grade"),
        "status": row.get("status"),
        "sent": row.get("sent"),
        "entry": row.get("entry"),
        "sl": row.get("sl"),
        "tp1": row.get("tp1"),
    }


def _due_bars(bars, cursor: str | None, created_at: datetime | None):
    """Bars strictly after the resume cursor and not before the signal existed."""
    due = []
    for bar in bars:
        stamp = bar.open_time.isoformat()
        if cursor and stamp <= cursor:
            continue
        if created_at is not None and bar.open_time < created_at:
            continue
        due.append(bar)
    return due


def _stamp(value) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _fresh(h4, d1_bias: str) -> bool:
    if not h4 or d1_bias not in {"bullish", "bearish"}:
        return False
    against = "bearish" if d1_bias == "bullish" else "bullish"
    events = [event for event in structure_events(h4, 2) if event.kind == "choch" and event.direction == against]
    return bool(events) and events[-1].index >= len(h4) - 6


def _h1_veto(h1, m15_direction: str) -> bool:
    if not h1 or not m15_direction:
        return False
    against = "bearish" if m15_direction == "bullish" else "bullish"
    events = [event for event in structure_events(h1, 2) if event.kind == "bos" and event.direction == against]
    return bool(events) and events[-1].index >= len(h1) - 4


def _aligned(bars) -> bool:
    d1 = bias(bars.get("1d", ())) if bars.get("1d") else "range"
    h4 = bias(bars.get("4h", ())) if bars.get("4h") else "range"
    return d1 == h4 and d1 in {"bullish", "bearish"}


def _direction(bars) -> str | None:
    d1 = bias(bars.get("1d", ())) if bars.get("1d") else "range"
    if d1 == "bullish":
        return "long"
    if d1 == "bearish":
        return "short"
    h4 = bias(bars.get("4h", ())) if bars.get("4h") else "range"
    if h4 == "bullish":
        return "long"
    if h4 == "bearish":
        return "short"
    return None


def _bias_text(bars, direction: str) -> str:
    side = "higher" if direction == "long" else "lower"
    return f"HTF bias {direction}, draw toward the nearest unswept {side} liquidity"[:120]


def _wide_week(news: NewsBook, daily: tuple, now: datetime) -> bool:
    """SR1. ATR_D1 above 1.3 × its 50-day median, or the week contains CPI, NFP, or FOMC."""
    opened = week_start(now)
    closed = opened + timedelta(days=7)
    for release in news.view(now).tier1():
        if opened <= release.at < closed and re.search(r"CPI|NFP|FOMC|Non-Farm", release.title, re.I):
            return True
    if len(daily) >= 51:
        series = [bar.high - bar.low for bar in daily[-51:]]
        current = series[-1]
        ordered = sorted(series[:-1])
        median = ordered[len(ordered) // 2]
        if median > 0 and current > D("1.3") * median:
            return True
    return False


def _or15(m15) -> tuple[D | None, D | None]:
    if len(m15) < 1:
        return None, None
    return m15[-1].high, m15[-1].low


def _render_input(setup, equity: D, _dxy: str) -> dict:
    from .alerts import valid_label
    return {
        "direction": setup.direction,
        "entry_type": setup.entry_type,
        "grade": setup.grade,
        "id6": id6(setup.signal_id),
        "model": setup.model,
        "window": setup.window,
        "confidence": setup.confidence,
        "entry": format(setup.entry, "f"),
        "sl": format(setup.sl, "f"),
        "r_usd": format(setup.r_usd, "f"),
        "tp1": format(setup.tp1, "f"),
        "rr_tp1": format(setup.rr_tp1, "f"),
        "tp2": None if setup.tp2 is None else format(setup.tp2, "f"),
        "rr_tp2": None if setup.rr_tp2 is None else format(setup.rr_tp2, "f"),
        "risk_pct": format(setup.risk_pct, "f"),
        "lot": format(setup.example_lot, "f"),
        "ref": format(equity, "f"),
        "valid_wib": valid_label(setup.valid_until),
        "reason": setup.reason,
        "news_wib": valid_label(setup.valid_until),
    }


def _config_thread(runner: XauRunner, message: str) -> None:
    runner.state = "config_error"
    runner._error = "XauConfigError"
    runner._beat()
    now = runner.clock()
    event, detail = ops("config_error", wib(now).date().isoformat(), message[:80])
    try:
        runner.store.migrate()
        runner.outbox.submit(now, OPS, event, detail)
    except Exception:
        log_event("xau_config_error_unreported", error="config_error")
    runner.stop_event.wait()


def start_xau(cfg_db_path, alert_sink, *, clock=None, feeds=None, cycle=None, settings=None):
    """Start the daemon. Config errors stay inside the thread and never propagate."""
    clock_fn = _clock_of(clock)
    try:
        loaded = settings if settings is not None else XauSettings.from_env()
        profile = load_profile(loaded.profile_path)
    except (XauConfigError, ProfileError, OSError) as exc:
        fallback = settings if isinstance(settings, XauSettings) else _fallback_settings()
        runner = XauRunner(cfg_db_path, alert_sink, fallback, None, clock=clock_fn, feeds=None, cycle=None)
        runner.thread = threading.Thread(
            target=_config_thread, args=(runner, type(exc).__name__), name="xau", daemon=True
        )
        runner.thread.start()
        return runner
    if feeds is None and cycle is None:
        feeds = HttpFeeds(loaded.feed, loaded.feed_fallbacks)
    runner = XauRunner(cfg_db_path, alert_sink, loaded, profile, clock=clock_fn, feeds=feeds, cycle=cycle)
    runner.state = "starting"
    runner.thread.start()
    return runner


def _fallback_settings() -> XauSettings:
    return XauSettings(profile_path=str(Path(__file__).resolve().parent / "profiles" / "example.toml"))
