import assert from "node:assert/strict";
import { test } from "node:test";
import { failureVisible, liveReadDecision, liveSessionAfterLogout } from "./src/live-policy.ts";

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

test("logout closes the stream and drops cached snapshots", () => {
  const session = liveSessionAfterLogout();
  assert.equal(session.connected, false);
  assert.equal(session.reconnect, false);
  assert.equal(session.cachedRoutes, 0);
});

test("an editable form ignores streamed snapshots and still accepts HTTP", () => {
  const decision = liveReadDecision(true, true, false);
  assert.equal(decision.startPoll, false);
  assert.equal(decision.acceptHttp, true);
  assert.equal(decision.applyCachedFrame, false);
});

test("a failed editable read is visible when the cached frame was not applied", () => {
  assert.equal(failureVisible(true, true, false, false, false), true);
});

test("a failed read is visible when the stream is down and the cached frame is not applied", () => {
  assert.equal(failureVisible(false, true, true, false, false), true);
});

test("a failed monitoring read stays hidden when the live frame is on screen", () => {
  assert.equal(failureVisible(true, true, true, false, false), false);
});

test("a failed read stays hidden once the panel is already showing data", () => {
  assert.equal(failureVisible(true, true, false, false, true), false);
});

test("an aborted read does not surface an HTTP failure", () => {
  assert.equal(failureVisible(true, false, false, true, false), false);
});
