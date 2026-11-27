import { test } from "node:test";
import assert from "node:assert/strict";
import {
  alivePlayback, clampPercent, clipBubble, nextScan, roomLife, screeningKey, wibClock, type LifeStatus,
} from "./src/tradingdesk/room-life.ts";
import type { ObservedValue } from "./src/tradingdesk/types.ts";

const now = Date.parse("2026-11-22T03:00:00Z");
const ts = new Date(now).toISOString();

function item(overrides: Partial<ObservedValue<string | null>> = {}): ObservedValue<string | null> {
  return {
    value: "ready", state: "confirmed", availability: "confirmed", freshness: "fresh", source: "worker",
    sourceAt: ts, receivedAt: ts, reason: null, traceId: null, ...overrides,
  };
}

function agents() {
  return {
    SCAN: item({ value: "ready" }),
    ANALYST: item({ value: "ready" }),
    AEGIS: item({ value: "pass" }),
    EXECUTOR: item({ value: "observe" }),
    PORTFOLIO: item({ value: "reported" }),
    WATCHER: item({ value: "reported" }),
  };
}

function withAgents(patch: Partial<Record<string, ObservedValue<string | null>>>) {
  return { ...agents(), ...patch };
}

const status: LifeStatus = {
  halt: false, reason: "ok", risk_permitted: true, mode: "observe", published_at: ts,
  pipeline: {
    screening: { state: "ready", source: "binance", count: 12 },
    risk: { state: "pass", reason: "ok" },
  },
};

test("fresh desks with no in-window event are idle", () => {
  const life = roomLife({ agents: agents(), status, events: [{ event: "worker_started", ts }], now, radar: false });
  for (const role of Object.values(life.stations)) {
    assert.equal(role.mood, "idle");
    assert.equal(role.bubble, null);
    assert.equal(role.stamp, false);
    assert.equal(role.radar, false);
  }
  assert.equal(life.halt, false);
});

test("a fresh executor event is working, and observe never stamps", () => {
  const events = [
    { event: "strategy_confirmed", ts: new Date(now - 4000).toISOString() },
    { event: "observe_candidate", ts, reason: "BTCUSDT crossed the book" },
  ];
  const life = roomLife({ agents: agents(), status, events, now, radar: false });
  const executor = life.stations.EXECUTOR;
  assert.equal(executor.mood, "working");
  assert.equal(executor.bubble, "BTCUSDT crossed the book");
  assert.equal(executor.time, "10:00 WIB");
  assert.equal(executor.stamp, false);
  assert.equal(life.stations.WATCHER.mood, "idle");
});

test("newest timestamp wins even when it is the first row", () => {
  const events = [
    { event: "observe_candidate", ts },
    { event: "strategy_confirmed", ts: new Date(now - 5000).toISOString() },
  ];
  const life = roomLife({ agents: agents(), status: { ...status, mode: "paper" }, events, now, radar: false });
  assert.equal(life.stations.EXECUTOR.mood, "working");
  assert.equal(life.stations.EXECUTOR.bubble, "Candidate observed");
  assert.equal(life.stations.EXECUTOR.stamp, false);
});

test("a paper fill stamps, and an equal-time later row replaces the earlier one", () => {
  const events = [
    { event: "observe_candidate", ts },
    { event: "strategy_confirmed", ts, alert_event: "ignored on a fill" },
  ];
  const life = roomLife({ agents: agents(), status: { ...status, mode: "paper" }, events, now, radar: false });
  assert.equal(life.stations.EXECUTOR.bubble, "Strategy confirmed");
  assert.equal(life.stations.EXECUTOR.stamp, true);
  const live = roomLife({ agents: agents(), status: { ...status, mode: "live" }, events, now, radar: false });
  assert.equal(live.stations.EXECUTOR.stamp, true);
  const observe = roomLife({ agents: agents(), status, events, now, radar: false });
  assert.equal(observe.stations.EXECUTOR.stamp, false);
});

test("failed and blocked events alert only their role", () => {
  const events = [
    { event: "alert_failed", ts, alert_event: "telegram delivery rejected now" },
    { event: "research_blocked", ts, reason: "provider down" },
    { event: "market_data_blocked", ts: new Date(now - 13000).toISOString() },
  ];
  const life = roomLife({ agents: agents(), status, events, now, radar: false });
  assert.equal(life.stations.WATCHER.mood, "alert");
  assert.equal(life.stations.WATCHER.bubble, "telegram delivery rejected…");
  assert.ok((life.stations.WATCHER.bubble ?? "").length <= 28);
  assert.equal(life.stations.ANALYST.mood, "alert");
  assert.equal(life.stations.ANALYST.bubble, "provider down");
  assert.equal(life.stations.SCAN.mood, "idle");
});

test("a fresh halt tints the room and a risk block alerts AEGIS", () => {
  const halted = roomLife({
    agents: agents(), status: { ...status, halt: true, reason: "daily loss" }, events: [], now, radar: false,
  });
  assert.equal(halted.halt, true);
  assert.equal(halted.stations.AEGIS.mood, "alert");
  assert.equal(halted.stations.AEGIS.bubble, "daily loss");
  assert.equal(halted.stations.AEGIS.time, "10:00 WIB");
  assert.equal(halted.stations.EXECUTOR.mood, "idle");
  const expired = roomLife({
    agents: agents(),
    status: { ...status, halt: true, reason: "daily loss", published_at: new Date(now - 900_001).toISOString() },
    events: [], now, radar: false,
  });
  assert.equal(expired.halt, false);
  assert.equal(expired.stations.AEGIS.mood, "idle");
  const blocked = roomLife({
    agents: agents(),
    status: { ...status, risk_permitted: false, pipeline: { ...status.pipeline, risk: { state: "blocked", reason: "budget" } } },
    events: [], now, radar: false,
  });
  assert.equal(blocked.halt, false);
  assert.equal(blocked.stations.AEGIS.mood, "alert");
  assert.equal(blocked.stations.AEGIS.bubble, "budget");
});

test("stale and missing desks sleep, loading stays idle, and a denial stays idle", () => {
  const life = roomLife({
    agents: withAgents({
      SCAN: item({ freshness: "stale", state: "stale" }),
      ANALYST: item({ state: "loading", freshness: "unknown" }),
      WATCHER: item({ state: "unauthorized", freshness: "unknown", value: null }),
    }),
    status, events: [], now, radar: false,
  });
  assert.equal(life.stations.SCAN.mood, "sleep");
  assert.equal(life.stations.SCAN.bubble, "zzz");
  assert.equal(life.stations.ANALYST.mood, "idle");
  assert.equal(life.stations.ANALYST.bubble, null);
  assert.equal(life.stations.PORTFOLIO.mood, "idle");
  assert.equal(life.stations.WATCHER.mood, "idle");
  const missing = roomLife({ agents: {}, status, events: [], now, radar: false });
  assert.equal(missing.stations.EXECUTOR.mood, "sleep");
});

test("a fresh event beats a stale observation", () => {
  const life = roomLife({
    agents: withAgents({ EXECUTOR: item({ freshness: "stale", state: "stale", value: "observe" }) }),
    status, events: [{ event: "duplicate_skipped", ts }], now, radar: false,
  });
  assert.equal(life.stations.EXECUTOR.mood, "working");
  assert.equal(life.stations.EXECUTOR.bubble, "Duplicate skipped");
});

test("bubble text stops at 28 characters and clocks are WIB", () => {
  assert.equal(clipBubble("short"), "short");
  assert.equal(clipBubble("x".repeat(28)), "x".repeat(28));
  assert.equal(clipBubble("x".repeat(29)), "x".repeat(27) + "…");
  assert.equal(wibClock(ts), "10:00 WIB");
  assert.equal(wibClock("not-a-time"), null);
});

test("ratio bars clamp to 0–100", () => {
  assert.equal(clampPercent(15, 10), 100);
  assert.equal(clampPercent(4, 10), 40);
  assert.equal(clampPercent(0, 10), 0);
  assert.equal(clampPercent(-3, 10), 0);
  assert.equal(clampPercent(Number.NaN, 10), 0);
  assert.equal(clampPercent(4, 0), 0);
});

test("playback is static when reduced, hidden, offscreen, paneled, text or motion is off", () => {
  const on = { reduced: false, hidden: false, offscreen: false, panel: false, text: false, motion: true };
  assert.equal(alivePlayback(on), true);
  for (const flag of ["reduced", "hidden", "offscreen", "panel", "text"] as const) {
    assert.equal(alivePlayback({ ...on, [flag]: true }), false);
  }
  assert.equal(alivePlayback({ ...on, motion: false }), false);
});

test("a scan radar needs a ready key that replaced a different key", () => {
  assert.equal(screeningKey(status), "ready|12|binance");
  assert.equal(screeningKey(null), null);
  const first = nextScan(null, "ready|12|binance", now);
  assert.equal(first.radar, false);
  const again = nextScan(first, "ready|12|binance", now + 1000);
  assert.equal(again.radar, false);
  const next = nextScan(first, "ready|13|binance", now + 1000);
  assert.equal(next.radar, true);
  assert.equal(nextScan(next, "ready|13|binance", now + 1000 + 12_000).radar, false);
  assert.equal(nextScan(first, "blocked|0|binance", now).radar, false);
  assert.equal(nextScan(next, null, now).radar, false);
  const life = roomLife({ agents: agents(), status, events: [], now, radar: true });
  assert.equal(life.stations.SCAN.radar, true);
  assert.equal(life.stations.SCAN.mood, "idle");
  const asleep = roomLife({
    agents: withAgents({ SCAN: item({ freshness: "stale", state: "stale" }) }),
    status, events: [], now, radar: true,
  });
  assert.equal(asleep.stations.SCAN.radar, false);
});
