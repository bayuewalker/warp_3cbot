import { test } from "node:test";
import assert from "node:assert/strict";
import { ambient, currentPublication, publishedCount, roleHistory, sweep } from "./src/tradingdesk/room.ts";
import type { ObservedValue } from "./src/tradingdesk/types.ts";

const ts = "2026-11-22T03:00:00Z";
const now = Date.parse(ts);
test("event history keeps independent outcomes, ignores invalid times and unrelated roles", () => {
  const list = [
    { event: "research_blocked", ts }, { event: "observe_candidate", ts },
    { event: "strategy_confirmed", ts: "2026-11-22T02:58:00Z", trace_id: "receipt-1" },
    { event: "strategy_confirmed", ts: "2026-11-22T03:01:00Z" }, { event: "alert_failed", ts: null },
  ];
  const history = roleHistory("EXECUTOR", list, now);
  assert.equal(history.latest?.event, "observe_candidate");
  assert.equal(history.success?.traceId, "receipt-1");
  assert.equal(history.failure, null);
  assert.equal(roleHistory("ANALYST", list, now).failure?.ts, ts);
  assert.equal(roleHistory("PORTFOLIO", list, now).latest, null);
  assert.equal(roleHistory("WATCHER", list, now).success, null);
});

test("only a fresh confirmed observation can carry a published integer count", () => {
  const observation: ObservedValue<string> = { value: "ready", state: "confirmed", availability: "confirmed",
    freshness: "fresh", source: "worker", sourceAt: ts, receivedAt: ts, reason: null, traceId: null };
  assert.equal(publishedCount(observation, 0), 0);
  assert.equal(publishedCount(observation, 3), 3);
  for (const value of [null, undefined, -1, 0.2, "0"]) assert.equal(publishedCount(observation, value), null);
  assert.equal(publishedCount({ ...observation, freshness: "unknown" }, 3), null);
  assert.equal(publishedCount({ ...observation, state: "stale" }, 3), null);
});

test("ambient requires a recognized, confirmed, fresh role and current connected worker", () => {
  const item: ObservedValue<string> = { value: "ready", state: "confirmed", availability: "confirmed",
    freshness: "fresh", source: "worker", sourceAt: ts, receivedAt: ts, reason: null, traceId: null };
  assert.equal(ambient("SCAN", item, true, currentPublication(ts, now)), true);
  for (const state of ["stale", "unauthorized", "partial", "loading", "confirmed_empty"] as const) {
    assert.equal(ambient("SCAN", { ...item, state }, true, true), false);
  }
  for (const freshness of ["unknown", "stale"] as const) {
    assert.equal(ambient("SCAN", { ...item, freshness }, true, true), false);
  }
  for (const value of [null, "blocked", "unknown", ""]) {
    assert.equal(ambient("SCAN", { ...item, value }, true, true), false);
  }
  assert.equal(ambient("SCAN", item, false, true), false);
  const at = (offset: number) => new Date(now + offset).toISOString();
  for (const stamp of [null, at(-900001), at(5001)]) {
    assert.equal(ambient("SCAN", item, true, currentPublication(stamp, now)), false);
  }
  for (const stamp of [at(-900000), at(5000)]) {
    assert.equal(ambient("SCAN", item, true, currentPublication(stamp, now)), true);
  }
  assert.equal(ambient("EXECUTOR", { ...item, value: "observe" }, true, true), false);
});

test("only a recent EXECUTOR success in paper or live can sweep", () => {
  const event = { event: "strategy_confirmed", ts, traceId: null };
  for (const mode of ["observe", "paper", "live"]) {
    for (const name of ["observe", "observe_candidate", "duplicate_skipped"]) {
      assert.equal(sweep("EXECUTOR", { ...event, event: name }, mode, now), false);
    }
    assert.equal(sweep("EXECUTOR", event, mode, now + 12000), mode !== "observe");
  }
  assert.equal(sweep("EXECUTOR", event, undefined, now), false);
  assert.equal(sweep("EXECUTOR", event, "paper", now + 12001), false);
  assert.equal(sweep("SCAN", null, "observe", now), false);
  assert.equal(sweep("SCAN", event, "observe", now), false);
  assert.equal(sweep("SCAN", { ...event, event: "market_data_blocked" }, "observe", now), false);
});
