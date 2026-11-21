import assert from "node:assert/strict";
import { test } from "node:test";
import { liveReadDecision } from "./src/live-policy.ts";

test("a dropped stream polls and accepts HTTP even when a frame is cached", () => {
  const decision = liveReadDecision(false, true, true);
  assert.equal(decision.startPoll, true);
  assert.equal(decision.acceptHttp, true);
  assert.equal(decision.applyCachedFrame, false);
});

test("a live stream keeps the cached frame and does not overwrite it with HTTP", () => {
  const decision = liveReadDecision(true, true, true);
  assert.equal(decision.startPoll, false);
  assert.equal(decision.acceptHttp, false);
  assert.equal(decision.applyCachedFrame, true);
});

test("the first read still uses HTTP before any frame arrives", () => {
  const decision = liveReadDecision(true, false, true);
  assert.equal(decision.acceptHttp, true);
  assert.equal(decision.applyCachedFrame, false);
});
