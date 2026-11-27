import { test } from "node:test";
import assert from "node:assert/strict";
import { roomLife } from "./src/tradingdesk/room-life.ts";
import { spotPostponed, xauAgents, xauCard } from "./src/tradingdesk/room.ts";
import { xauTape } from "./src/tradingdesk/quotes.ts";

const now = Date.parse("2026-11-27T07:30:00Z");
const at = new Date(now).toISOString();

function status(state: string, extra: Record<string, unknown> = {}) {
  return {
    lanes: { primary: "xau", spot: { state: "postponed", flag: "WARP_SPOT_LANE_ENABLED", since: at } },
    published_at: at,
    xau: { enabled: true, state, spot_at: at, cycle_seconds: 30, ...extra },
  };
}

test("xau card reports ready, warming, degraded, unavailable, and unknown", () => {
  assert.equal(xauCard(status("running", { ready: true, ready_reason: "ok" })).ready, true);
  assert.equal(xauCard(status("running", { ready: true, ready_reason: "ok" })).state, "running");
  assert.equal(xauCard(status("warming", { ready: false, ready_reason: "warming" })).state, "warming");
  assert.equal(xauCard(status("degraded", { ready: false, ready_reason: "dirty_basis" })).reason, "dirty_basis");
  assert.equal(xauCard(status("feed_unavailable", { ready_reason: "feed_unavailable" })).state, "feed_unavailable");
  assert.equal(xauCard({ lanes: { primary: "xau" } }).state, "unknown");
  assert.equal(xauCard(null).reason, "missing");
});

test("postponed is a lane state", () => {
  assert.equal(spotPostponed(status("running")), true);
  assert.equal(spotPostponed({ lanes: { primary: "xau", spot: { state: "active" } } }), false);
  assert.equal(spotPostponed(null), false);
});

test("market tape defaults to XAUUSD and says unavailable without a spot", () => {
  const live = xauTape(status("running", { spot_last: "4088.2", spot_at: at, ready_reason: "ok" }), now);
  assert.equal(live.symbol, "XAUUSD");
  assert.equal(live.last, "4088.2");
  assert.equal(live.fresh, true);
  assert.equal(live.source, "OANDA:XAUUSD via TradingView (spot)");
  const stale = xauTape(status("running", { spot_last: "4088.2", spot_at: at }), now + 120_000);
  assert.equal(stale.fresh, false);
  const missing = xauTape(status("feed_unavailable", { ready_reason: "feed" }), now);
  assert.equal(missing.last, null);
  assert.equal(missing.active, true);
  assert.equal(missing.reason, "feed");
});

test("the six desks read XAU roles", () => {
  const row = status("running", {
    ready: true, ready_reason: "ok", spot_last: "4088.2", basis: "0.40", feed: "binance",
    engine: "SMC", window: { name: "London KZ" },
    open_signal: { direction: "long", entry: "4080", sl: "4070", tp1: "4100", tp2: "4120", grade: "A+" },
    today: { signals_sent: 1, journal_only: 2, realized_r: "0.5" },
    last_signal_at: at,
  });
  const seats = xauAgents(row, now);
  assert.match(String(seats.SCAN.value), /4088.2/);
  assert.match(String(seats.ANALYST.value), /SMC/);
  assert.equal(seats.AEGIS.value, "pass");
  assert.match(String(seats.EXECUTOR.value), /long/);
  assert.match(String(seats.PORTFOLIO.value), /0.5/);
  assert.equal(seats.WATCHER.value, at);
  const life = roomLife({ agents: {}, status: row, events: [{ event: "XAU BUY LIMIT A+ #ABC123", ts: at }], now, radar: false });
  assert.equal(life.stations.SCAN.mood, "working");
  assert.equal(life.stations.EXECUTOR.bubble, "XAU long");
  assert.match(life.stations.WATCHER.bubble ?? "", /^XAU /);
});
