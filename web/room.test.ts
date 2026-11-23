import { test } from "node:test";
import assert from "node:assert/strict";
import { publishedCount, roleHistory } from "./src/tradingdesk/room.ts";
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
