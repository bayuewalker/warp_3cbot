import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  ACTIVITY_SOURCES, NO_FILTER, NO_OUTCOME, STREAM_GRACE_MS, activityModel, applyFilters, facets,
  recordResumeAck, requestPreferencesTab, resumeGate, riskHealth, takeRequestedTab, currentResumeAck, forgetResumeAck,
  type ActivitySource, type StreamView,
} from "./src/tradingdesk/activity-model.ts";
import { MAX_CLOCK_SKEW_MS } from "./src/tradingdesk/frames.ts";
import type { ReadInput, ReadIssue } from "./src/tradingdesk/types.ts";

const stamp = "2026-11-24T10:00:00.000Z";
const now = Date.parse(stamp) + 1000;
const iso = (offsetMs: number) => new Date(now + offsetMs).toISOString();
const read = (data: unknown, issue: ReadIssue | null = null): ReadInput => ({ data, received: new Date(now), issue });
const status = read({ mode: "observe", published_at: iso(-2000), halt: false, risk_permitted: true, reason: "ok", providers: {} });

function reads(over: Partial<Record<ActivitySource, ReadInput>> = {}) {
  return {
    events: read({ events: [], as_of: iso(-1000) }),
    alerts: read({ alerts: [] }),
    journal: read({ submissions: [] }),
    signals: read({ decisions: [] }),
    breakers: read({ reason: "ok", halt: false, risk_permitted: true }),
    ...over,
  };
}
const connected: StreamView = { connected: true, lastFrameAt: now - 1000, downSince: null };

test("sources that are empty but confirmed report confirmed_empty and add no rows", () => {
  const model = activityModel(reads(), status, now);
  for (const source of ["events", "alerts", "journal", "signals"] as const) {
    assert.equal(model.sources[source].state, "confirmed_empty", source);
    assert.equal(model.sources[source].count, 0);
  }
  assert.equal(model.sources.breakers.state, "confirmed");
  assert.deepEqual(model.timed, []);
  assert.equal(model.untimed.length, 1);
  assert.equal(model.untimed[0].source, "breakers");
});

test("rows without a usable time go to the untimed group and are never given a time", () => {
  const model = activityModel(reads({
    events: read({ events: [{ event: "a" }, { event: "b", ts: null }, { event: "c", ts: "yesterday" }, { event: "d", ts: "2026-11-24 09:59:00" }], as_of: iso(-1000) }),
    journal: read({ submissions: [{ client_id: "c1", symbol: "BTCUSDT", state: "unknown", strategy_id: null, created_at: "2026-11-24 09:59:00" }] }),
  }), status, now);
  assert.deepEqual(model.timed, []);
  const rows = model.untimed.filter(item => item.source !== "breakers");
  assert.equal(rows.length, 5);
  for (const item of rows) {
    assert.equal(item.at, null);
    assert.equal(item.atMs, null);
  }
});

test("future times beyond MAX_CLOCK_SKEW_MS and impossible dates are rejected, small skew is kept", () => {
  const model = activityModel(reads({
    alerts: read({ alerts: [
      { id: 1, event: "ok_now", state: "pending", created_at: iso(MAX_CLOCK_SKEW_MS - 1) },
      { id: 2, event: "future", state: "pending", created_at: iso(MAX_CLOCK_SKEW_MS + 60_000) },
      { id: 3, event: "impossible", state: "pending", created_at: "2026-02-31T00:00:00Z" },
      { id: 4, event: "no_zone", state: "pending", created_at: "2026-11-24T09:00:00" },
    ] }),
  }), status, now);
  assert.deepEqual(model.timed.map(item => item.kind), ["ok_now"]);
  assert.deepEqual(model.untimed.filter(item => item.source === "alerts").map(item => item.kind), ["future", "impossible", "no_zone"]);
});

test("unknown event kinds are kept as reported with no outcome or severity", () => {
  const model = activityModel(reads({
    events: read({ events: [{ ts: iso(-5000), event: "never_seen_before_kind", note: "x", reason: "because" }], as_of: iso(-1000) }),
  }), status, now);
  const [item] = model.timed;
  assert.equal(item.kind, "never_seen_before_kind");
  assert.equal(item.outcome, null);
  assert.equal(item.severity, null);
  assert.equal(item.reason, "because");
  assert.equal(item.refId, null);
  assert.equal(item.refKind, null);
});

test("alerts keep last_error as the reason and map severity only from the documented state", () => {
  const model = activityModel(reads({
    alerts: read({ alerts: [
      { id: 7, event: "worker_stalled", state: "pending", attempts: 3, last_error: "HTTP 500", created_at: iso(-3000), delivered_at: null },
      { id: 6, event: "halt", state: "delivered", attempts: 1, last_error: null, created_at: iso(-4000), delivered_at: iso(-3500) },
      { id: 5, event: "odd", state: "mystery", created_at: iso(-6000) },
    ] }),
  }), status, now);
  const [pending, delivered, odd] = model.timed;
  assert.equal(pending.reason, "HTTP 500");
  assert.equal(pending.severity, "warn");
  assert.equal(pending.refId, "7");
  assert.equal(pending.refKind, "alert_id");
  assert.equal(delivered.reason, null);
  assert.equal(delivered.severity, "info");
  assert.equal(odd.outcome, "mystery");
  assert.equal(odd.severity, null);
});

test("a submission without strategy_id shows its client_id and invents nothing", () => {
  const model = activityModel(reads({
    journal: read({ submissions: [
      { client_id: "warp-abc", symbol: "ETHUSDT", state: "unknown", strategy_id: null, created_at: iso(-1000) },
      { client_id: "warp-def", symbol: "BTCUSDT", state: "confirmed", strategy_id: "strat-9", created_at: iso(-2000) },
    ] }),
  }), status, now);
  const [open, confirmed] = model.timed;
  assert.equal(open.refId, "warp-abc");
  assert.equal(open.refKind, "client_id");
  assert.equal(open.related, null);
  assert.deepEqual(confirmed.related, { refKind: "strategy_id", refId: "strat-9" });
  assert.equal(open.severity, null);
});

test("rows without any real identifier never receive a generated one", () => {
  const model = activityModel(reads({
    alerts: read({ alerts: [{ event: "no_id", state: "pending", created_at: iso(-1000) }] }),
    signals: read({ decisions: [{ recorded_at: iso(-2000), symbol: "*", stage: "universe", reason: "no_eligible_market", detail: "fail_closed" }] }),
  }), status, now);
  for (const item of model.timed) {
    assert.equal(item.refId, null);
    assert.equal(item.refKind, null);
  }
  const signal = model.timed.find(item => item.source === "signals")!;
  assert.equal(signal.outcome, "fail_closed");
  assert.equal(signal.reason, "no_eligible_market");
  assert.equal(signal.subject, "*");
});

test("mixed sources sort newest first by source time and tie-break deterministically", () => {
  const model = activityModel(reads({
    events: read({ events: [{ ts: iso(-9000), event: "old" }, { ts: iso(-1000), event: "newest" }], as_of: iso(-500) }),
    alerts: read({ alerts: [{ id: 2, event: "mid", state: "pending", created_at: iso(-5000) }] }),
    signals: read({ decisions: [{ recorded_at: iso(-5000), symbol: "BTCUSDT", stage: "broker", reason: "observe", detail: "not_submitted" }] }),
  }), status, now);
  assert.deepEqual(model.timed.map(item => `${item.source}:${item.kind}`),
    ["events:newest", "alerts:mid", "signals:broker", "events:old"]);
});

test("identical (source, refId, at) collapses inside one source and never across sources", () => {
  const at = iso(-1000);
  const model = activityModel(reads({
    alerts: read({ alerts: [
      { id: 1, event: "twice", state: "pending", created_at: at },
      { id: 1, event: "twice", state: "pending", created_at: at },
      { id: 1, event: "other_time", state: "pending", created_at: iso(-2000) },
    ] }),
    journal: read({ submissions: [{ client_id: "1", symbol: "X", state: "unknown", created_at: at }] }),
    events: read({ events: [{ ts: at, event: "e", strategy_id: "1" }, { ts: at, event: "e2", strategy_id: "1" }], as_of: iso(-500) }),
  }), status, now);
  assert.equal(model.timed.filter(item => item.source === "alerts").length, 2);
  assert.equal(model.timed.filter(item => item.source === "journal").length, 1);
  // Two events with the same strategy_id and instant are one identifier at one time.
  assert.equal(model.timed.filter(item => item.source === "events").length, 1);
});

test("rows with no identifier are never de-duplicated", () => {
  const at = iso(-1000);
  const model = activityModel(reads({
    events: read({ events: [{ ts: at, event: "x" }, { ts: at, event: "x" }], as_of: iso(-500) }),
  }), status, now);
  assert.equal(model.timed.length, 2);
});

test("malformed rows make a source partial and are counted, not guessed", () => {
  const model = activityModel(reads({
    alerts: read({ alerts: [{ id: 1, event: "ok", state: "pending", created_at: iso(-1000) }, null, "text", { id: 2 }] }),
  }), status, now);
  assert.equal(model.sources.alerts.state, "partial");
  assert.equal(model.sources.alerts.invalid, 3);
  assert.equal(model.sources.alerts.count, 1);
  const wrong = activityModel(reads({ alerts: read({ alerts: "nope" }) }), status, now);
  assert.equal(wrong.sources.alerts.state, "unavailable");
  assert.equal(wrong.sources.alerts.reason, "malformed_payload");
  assert.equal(wrong.timed.length, 0);
});

test("a failed or stale source downgrades its state instead of staying confirmed", () => {
  const retained = { alerts: [{ id: 1, event: "kept", state: "pending", created_at: iso(-1000) }] };
  const failed = activityModel(reads({ alerts: read(retained, "request_failed") }), status, now);
  assert.equal(failed.sources.alerts.state, "error");
  assert.equal(failed.timed[0].state, "error");
  const none = activityModel(reads({ alerts: read(null, "request_failed") }), status, now);
  assert.equal(none.sources.alerts.state, "error");
  assert.equal(none.timed.length, 0);
  const unauthorized = activityModel(reads({ journal: read(null, "unauthorized") }), status, now);
  assert.equal(unauthorized.sources.journal.state, "unauthorized");
  const oldEvents = activityModel(reads({ events: read({ events: [{ ts: iso(-300_000), event: "e" }], as_of: iso(-300_000) }) }), status, now);
  assert.equal(oldEvents.sources.events.state, "stale");
  assert.equal(oldEvents.sources.events.freshness, "stale");
  const staleWorker = read({ ...(status.data as object), published_at: iso(-20 * 60_000) });
  const downgraded = activityModel(reads({ alerts: read(retained) }), staleWorker, now);
  assert.equal(downgraded.sources.alerts.state, "stale");
  assert.equal(downgraded.sources.journal.state, "stale");
  const noStatus = activityModel(reads({ alerts: read(retained) }), read(null, "request_failed"), now);
  assert.equal(noStatus.sources.alerts.state, "stale");
  const awaiting = activityModel({ ...reads(), signals: read({}) }, status, now);
  assert.equal(awaiting.sources.signals.state, "awaiting_first_snapshot");
});

test("the breaker snapshot is the current gate, carries no time and maps severity 1:1", () => {
  const cases: [object, string | null, string | null][] = [
    [{ halt: true, risk_permitted: false, reason: "halt" }, "halt", "bad"],
    [{ halt: false, risk_permitted: false, reason: "budget" }, "risk_blocked", "warn"],
    [{ halt: false, risk_permitted: true, reason: "ok" }, "risk_permitted", "info"],
    [{ halt: false, reason: "?" }, null, null],
  ];
  for (const [body, outcome, severity] of cases) {
    const item = activityModel(reads({ breakers: read(body) }), status, now).untimed.find(row => row.source === "breakers")!;
    assert.equal(item.at, null);
    assert.equal(item.outcome, outcome);
    assert.equal(item.severity, severity);
  }
  assert.equal(activityModel(reads({ breakers: read({ reason: "x" }) }), status, now).sources.breakers.state, "unavailable");
});

test("filters and facets count the loaded window only", () => {
  const model = activityModel(reads({
    events: read({ events: [{ ts: iso(-1000), event: "halt" }, { ts: iso(-2000), event: "cycle_blocked", error: "X" }], as_of: iso(-500) }),
    alerts: read({ alerts: [{ id: 1, event: "halt", state: "pending", created_at: iso(-3000) }] }),
  }), status, now);
  const items = [...model.timed, ...model.untimed];
  const counts = facets(items);
  assert.equal(counts.sources.events, 2);
  assert.equal(counts.sources.alerts, 1);
  assert.deepEqual(counts.kinds.find(([kind]) => kind === "halt"), ["halt", 2]);
  assert.ok(counts.outcomes.some(([outcome]) => outcome === NO_OUTCOME));
  assert.equal(applyFilters(items, { ...NO_FILTER, source: "alerts" }).length, 1);
  assert.equal(applyFilters(items, { ...NO_FILTER, kind: "halt" }).length, 2);
  assert.equal(applyFilters(items, { ...NO_FILTER, outcome: "pending" }).length, 1);
  assert.equal(applyFilters(items, { source: "events", kind: "halt", outcome: "pending" }).length, 0);
  assert.equal(ACTIVITY_SOURCES.length, 5);
});

test("RESUME ack stays awaiting until a snapshot published after the ack replaces the earlier one", () => {
  const ackAt = now;
  const before = iso(-10_000);
  assert.equal(resumeGate(null, before, now), "none");
  const ack = { at: ackAt, publishedAt: before };
  assert.equal(resumeGate(ack, before, now + 1000), "awaiting");
  assert.equal(resumeGate(ack, null, now + 1000), "awaiting");
  assert.equal(resumeGate(ack, "garbage", now + 1000), "awaiting");
  assert.equal(resumeGate(ack, new Date(ackAt - 1).toISOString(), now + 1000), "awaiting");
  assert.equal(resumeGate(ack, new Date(ackAt).toISOString(), now + 1000), "awaiting");
  assert.equal(resumeGate(ack, new Date(ackAt + 2000).toISOString(), now + 3000), "fresh");
  // A future-dated publication is not evidence of anything.
  assert.equal(resumeGate(ack, new Date(ackAt + 60_000).toISOString(), now + 1000), "awaiting");
  forgetResumeAck();
  recordResumeAck(5, "p");
  assert.deepEqual(currentResumeAck(), { at: 5, publishedAt: "p" });
  forgetResumeAck();
  assert.equal(currentResumeAck(), null);
});

test("health is never clear after an ack, a stream drop, a stale snapshot or a failed read", () => {
  const good = { status, breakers: read({ reason: "ok", halt: false, risk_permitted: true }),
    risk: read({ reason: "ok", risk_permitted: true, execution_permitted: false, equity: "10.5", equity_reliable: true }) };
  const clear = riskHealth(good, connected, now);
  assert.equal(clear.level, "clear");
  assert.equal(clear.risk.equity, "10.5");
  assert.equal(clear.risk.equityReliable, true);

  assert.equal(riskHealth(good, connected, now, "awaiting").level, "awaiting");
  assert.equal(riskHealth(good, connected, now, "fresh").level, "clear");

  const dropped: StreamView = { connected: false, lastFrameAt: now - 60_000, downSince: now - STREAM_GRACE_MS };
  const drop = riskHealth(good, dropped, now);
  assert.equal(drop.level, "incident");
  assert.deepEqual(drop.incidents.map(item => item.id), ["stream"]);
  assert.equal(drop.stream.state, "fallback");
  const justDropped = riskHealth(good, { ...dropped, downSince: now - 1000 }, now);
  assert.equal(justDropped.incidents.length, 0);
  assert.notEqual(justDropped.level, "clear");

  const failed = riskHealth({ ...good, status: read(status.data, "request_failed") }, connected, now);
  assert.equal(failed.level, "incident");
  assert.equal(failed.worker.state, "failed");
  assert.ok(failed.incidents.some(item => item.id === "statusFailed"));
  assert.equal(riskHealth({ ...good, status: read(null, "request_failed") }, dropped, now).stream.state, "disconnected");

  const stale = riskHealth({ ...good, status: read({ ...(status.data as object), published_at: iso(-16 * 60_000) }) }, connected, now);
  assert.equal(stale.level, "incident");
  assert.ok(stale.incidents.some(item => item.id === "statusStale"));

  const untimed = riskHealth({ ...good, status: read({ ...(status.data as object), published_at: null }) }, connected, now);
  assert.equal(untimed.worker.state, "unknown");
  assert.equal(untimed.level, "unverified");

  assert.equal(riskHealth({ ...good, breakers: read(null, "request_failed") }, connected, now).level, "unverified");
  assert.equal(riskHealth({ status: read(null) }, connected, now).level, "unverified");
});

test("halt, risk gate and provider states raise incidents from their own fields", () => {
  const halted = riskHealth({ status: read({ ...(status.data as object), halt: true, risk_permitted: false }) }, connected, now);
  assert.deepEqual(halted.incidents.map(item => item.id), ["halt"]);
  const viaBreaker = riskHealth({ status, breakers: read({ halt: true, risk_permitted: false, reason: "halt" }) }, connected, now);
  assert.deepEqual(viaBreaker.incidents.map(item => item.id), ["halt"]);
  const gate = riskHealth({ status: read({ ...(status.data as object), risk_permitted: false, reason: "budget" }) }, connected, now);
  assert.deepEqual(gate.incidents, [{ id: "risk", detail: "budget" }]);
  const providers = riskHealth({ status: read({ ...(status.data as object), providers: {
    nvidia: { state: "cooldown", model: "m", detail: "HTTP 429", until: iso(60_000) },
    openrouter: { state: "unconfigured", model: "n" },
    third: { state: "ok" },
    broken: { state: "error" },
    unreported: {},
  } }) }, connected, now);
  assert.deepEqual(providers.incidents.map(item => item.detail), ["nvidia · cooldown", "broken · error"]);
  assert.equal(providers.providers[0].state, "cooldown");
  assert.equal(providers.providers[4].state, null);
  assert.equal(providers.level, "incident");
  const unconfigured = riskHealth({ status: read({ ...(status.data as object), providers: { nvidia: { state: "unconfigured" } } }) }, connected, now);
  assert.equal(unconfigured.incidents.length, 0);
  const postponed = riskHealth({ status: read({
    ...(status.data as object), risk_permitted: false, reason: "spot_postponed",
    providers: { threecommas: { state: "postponed" } },
    xau: { state: "warming", ready_reason: "warming" },
  }) }, connected, now);
  assert.equal(postponed.incidents.length, 0);
  const xauDown = riskHealth({ status: read({
    ...(status.data as object), risk_permitted: false, reason: "spot_postponed",
    providers: { threecommas: { state: "postponed" } },
    xau: { state: "feed_unavailable", ready_reason: "feed_unavailable" },
  }) }, connected, now);
  assert.deepEqual(xauDown.incidents, [{ id: "xau", detail: "feed_unavailable" }]);
});

test("OBSERVE mode blocking entries by design is a neutral gate, not an incident, and never clear", () => {
  const base = status.data as object;
  for (const reason of ["observe", "observe_without_account"]) {
    const observe = riskHealth({
      status: read({ ...base, mode: "observe", risk_permitted: false, reason }),
      breakers: read({ halt: false, risk_permitted: false, reason }),
      risk: read({ risk_permitted: false, execution_permitted: false, reason, equity: null, equity_reliable: false }),
    }, connected, now);
    assert.equal(observe.observeGate, true, reason);
    assert.deepEqual(observe.incidents, [], reason);
    assert.notEqual(observe.level, "clear", reason);
    assert.equal(observe.level, "unverified", reason);
  }
  const mixed = riskHealth({
    status: read({ ...base, mode: "observe", risk_permitted: false, reason: "observe_without_account" }),
    breakers: read({ halt: false, risk_permitted: false, reason: "daily_loss" }),
  }, connected, now);
  assert.equal(mixed.observeGate, false);
  assert.deepEqual(mixed.incidents, [{ id: "risk", detail: "observe_without_account" }]);
  const halted = riskHealth({ status: read({ ...base, mode: "observe", halt: true, risk_permitted: false, reason: "observe" }) }, connected, now);
  assert.deepEqual(halted.incidents.map(item => item.id), ["halt"]);
  assert.equal(halted.observeGate, false);
  const unreasoned = riskHealth({ status: read({ ...base, mode: "observe", risk_permitted: false, reason: null }) }, connected, now);
  assert.equal(unreasoned.observeGate, false);
  assert.deepEqual(unreasoned.incidents, [{ id: "risk", detail: null }]);
});

test("risk_permitted false in paper mode, or with an observe reason outside observe, stays an incident", () => {
  const base = status.data as object;
  for (const reason of ["observe", "observe_without_account", "budget"]) {
    const paper = riskHealth({ status: read({ ...base, mode: "paper", risk_permitted: false, reason }) }, connected, now);
    assert.equal(paper.observeGate, false, reason);
    assert.deepEqual(paper.incidents, [{ id: "risk", detail: reason }], reason);
    assert.equal(paper.level, "incident", reason);
  }
  const unknownMode = riskHealth({ status: read({ ...base, mode: null, risk_permitted: false, reason: "observe" }) }, connected, now);
  assert.equal(unknownMode.observeGate, false);
  assert.equal(unknownMode.incidents.length, 1);
});

test("opening a tab from the incident strip is consumed exactly once", () => {
  assert.equal(takeRequestedTab(), null);
  requestPreferencesTab("risk");
  assert.equal(takeRequestedTab(), "risk");
  assert.equal(takeRequestedTab(), null);
});

test("EN and ID dictionaries carry exactly the same keys", () => {
  const source = readFileSync(new URL("./src/i18n.ts", import.meta.url), "utf8");
  const keys = (block: string) => [...block.matchAll(/^\s{4}(\w+):/gm)].map(match => match[1]).sort();
  const en = source.slice(source.indexOf("  en: {"), source.indexOf("  id: {"));
  const id = source.slice(source.indexOf("  id: {"), source.indexOf("} as const"));
  assert.deepEqual(keys(en), keys(id));
  assert.equal(new Set(keys(en)).size, keys(en).length);
});
