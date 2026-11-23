import assert from "node:assert/strict";
import test from "node:test";
import { overviewTelemetry } from "./src/tradingdesk/adapters.ts";
import { acceptFrame, instant } from "./src/tradingdesk/frames.ts";
import { payloadIssue } from "./src/tradingdesk/validate.ts";
import type { ReadInput, ReadIssue } from "./src/tradingdesk/types.ts";

const stamp = "2026-11-22T00:00:00.000Z";
const now = Date.parse(stamp) + 1000;
const prior = "2026-11-21T23:00:00Z";
const read = (data: unknown, issue: ReadIssue | null = null): ReadInput => ({ data, received: new Date(now), issue });
const status = read({ mode: "observe", published_at: stamp, equity: "0", pnl_today: null, execution_permitted: false,
  pipeline: { screening: { state: "ready" }, analysis: { state: "pending" }, risk: { state: "blocked", reason: "budget" } } });
const base = { status, accounts: read({ accounts: [], as_of: stamp }), bots: read({ bots: [] }), positions: read({ positions: [], as_of: stamp }),
  book: read({ positions: [], as_of: stamp, errors: [] }), events: read({ events: [], as_of: stamp }) };

test("decimal zero, missing PnL and untimed bot inventory have independent provenance", () => {
  const { metrics } = overviewTelemetry(base, now);
  assert.equal(metrics.equity.value, "0");
  assert.equal(metrics.equity.availability, "confirmed");
  assert.equal(metrics.equity.sourceAt, null);
  assert.equal(metrics.equity.freshness, "unknown");
  assert.equal(metrics.pnl.value, null);
  assert.equal(metrics.pnl.state, "unavailable");
  assert.equal(metrics.activeBots.state, "confirmed_empty");
  assert.equal(metrics.activeBots.freshness, "unknown");
  assert.equal(metrics.open.value, 0);
  assert.equal(metrics.open.state, "confirmed_empty");
});

test("broker TTL and errors take precedence over fresh browser and worker timestamps", () => {
  const { metrics, agents } = overviewTelemetry({ ...base,
    positions: read({ positions: [], as_of: prior }),
    book: read({ positions: [], as_of: prior, errors: ["position_read_cap"], stale: true }),
  }, now);
  assert.equal(metrics.open.value, null);
  assert.equal(metrics.open.state, "stale");
  assert.equal(agents.PORTFOLIO.state, "partial");
  assert.equal(agents.PORTFOLIO.freshness, "stale");
  assert.equal(agents.PORTFOLIO.value, null);
  assert.equal(agents.PORTFOLIO.sourceAt, new Date(prior).toISOString());
  assert.equal(agents.PORTFOLIO.receivedAt, new Date(now).toISOString());
  for (const at of [null, "bad", "2035-01-01T00:00:00Z"]) {
    const m = overviewTelemetry({ ...base, positions: read({ positions: [], as_of: at }) }, now);
    assert.equal(m.metrics.open.value, null);
    assert.equal(m.metrics.open.freshness, "unknown");
  }
  const error = overviewTelemetry({ ...base, positions: read({ positions: [], as_of: stamp, error: "HTTP 503" }) }, now);
  assert.equal(error.metrics.open.sourceAt, null);
  assert.equal(error.metrics.open.reason, "HTTP 503");
});

test("all six roles report worker outcomes or observations without inferring lifecycle activity", () => {
  const { agents } = overviewTelemetry(base, now);
  assert.deepEqual(Object.values(agents).map(a => a.value), ["ready", "pending", "blocked", "observe", "empty", "empty"]);
  const model = overviewTelemetry({ ...base, status: read({ mode: "paper", halt: true, published_at: prior }),
    events: read({ events: [{ event: "worker_started", trace_id: "fixture-trace" }], as_of: stamp }),
  }, now);
  assert.equal(model.agents.AEGIS.state, "stale");
  assert.equal(model.agents.AEGIS.value, "halt");
  assert.equal(model.agents.EXECUTOR.value, null);
  assert.equal(model.agents.WATCHER.traceId, "fixture-trace");
  assert.equal(agents.WATCHER.traceId, null);
});

test("transport states survive adaptation including authorization loss and first publication", () => {
  const inputs = [
    [read(null), "loading"], [read({}), "awaiting_first_snapshot"], [read(null, "awaiting_first_snapshot"), "awaiting_first_snapshot"],
    [read(null, "malformed_payload"), "error"], [read(null, "request_failed"), "error"], [read(status.data, "unauthorized"), "unauthorized"],
  ] as const;
  for (const [input, expected] of inputs) {
    const { metrics } = overviewTelemetry({ ...base, status: input }, now);
    assert.equal(metrics.equity.state, expected);
    assert.equal(metrics.equity.value, null);
  }
  const retained = overviewTelemetry({ ...base, status: read(status.data, "request_failed") }, now).metrics.equity;
  assert.equal(retained.value, "0");
  assert.equal(retained.availability, "partial");
  assert.equal(retained.freshness, "stale");
  assert.equal(payloadIssue("/api/status", {}), "awaiting_first_snapshot");
  assert.equal(payloadIssue("/api/positions", { positions: [null] }), "malformed_payload");
  assert.equal(payloadIssue("/api/positions", { positions: [] }), null);
  assert.equal(instant("2026-11-22"), null);
  assert.equal(instant("2026-02-30T00:00:00Z"), null);
});

test("publication and evidence have independent watermarks and microsecond ordering", () => {
  const path = "/api/account-positions";
  const first = { as_of: "2026-11-22T00:00:00.000210Z", positions: [] };
  const f = acceptFrame(undefined, path, first, now, stamp);
  assert.equal(acceptFrame(f, path, first, now + 1000, stamp), f);
  assert.equal(acceptFrame(f, path, { ...first, as_of: "2026-11-22T00:00:00.000209Z" }, now), f);
  assert.equal(acceptFrame(f, path, { ...first, errors: ["old snapshot"] }, now, prior), f);
  const degraded = acceptFrame(f, path, { positions: [], stale: true, as_of: prior, errors: ["timeout"] }, now + 1);
  assert.notEqual(degraded, f);
  assert.equal(degraded.evidence, f.evidence);
  assert.equal(acceptFrame(degraded, path, { positions: [], as_of: prior }, now + 2), degraded);
  assert.deepEqual(acceptFrame(degraded, path, first, now + 3).data, first);
});

test("same-source-time changes such as HALT pass through; untimed dedicated frames use delivery order", () => {
  const f = acceptFrame(undefined, "/api/status", { published_at: stamp, halt: false }, now);
  assert.deepEqual(acceptFrame(f, "/api/status", { published_at: stamp, halt: true }, now + 1).data,
    { published_at: stamp, halt: true });
  const accounts = acceptFrame(undefined, "/api/accounts", { accounts: [], as_of: stamp }, now);
  assert.equal(acceptFrame(accounts, "/api/accounts", { as_of: prior, error: "old attempt" }, now), accounts);
  const empty = acceptFrame(accounts, "/api/accounts", {}, now + 1);
  assert.deepEqual(empty.data, {});
  assert.equal(empty.at, now + 1);
});

test("a valid position book cannot confirm failed balances; the two TTLs expire independently", () => {
  const portfolio = (accounts: ReadInput) => overviewTelemetry({ ...base, accounts }, now).agents.PORTFOLIO;
  assert.equal(portfolio(read({ accounts: [], as_of: stamp, error: "HTTP 429" })).state, "partial");
  assert.equal(portfolio(read({ accounts: [], as_of: stamp, error: "HTTP 429" })).sourceAt, null);
  const delayed = portfolio(read({ accounts: [], as_of: new Date(now - 100_000).toISOString() }));
  assert.equal(delayed.state, "stale");
  assert.equal(portfolio(read(null, "unauthorized")).value, null);
});

function readsAt(time: number) {
  const at = new Date(time).toISOString();
  return { ...base,
    status: read({ published_at: at, equity: "18.25", pnl_today: "-0.50", pipeline: { screening: { state: "ready" } } }),
    accounts: read({ accounts: [], as_of: at }), book: read({ positions: [], errors: [], as_of: at }),
    positions: read({ positions: [], as_of: at }), events: read({ events: [], as_of: at }),
  };
}

test("equity and PnL preserve untimed values when their worker publication expires", () => {
  for (const at of [new Date(now - 7_200_000).toISOString(), null, "invalid", new Date(now + 60_000).toISOString()]) {
    const { metrics } = overviewTelemetry({ ...base, status: read({ published_at: at, equity: "18.25", pnl_today: "-0.50" }) }, now);
    for (const metric of [metrics.equity, metrics.pnl]) {
      assert.equal(metric.state, "stale");
      assert.equal(metric.sourceAt, null);
    }
    assert.equal(metrics.equity.value, "18.25");
    assert.equal(metrics.pnl.value, "-0.50");
  }
  for (const metric of [overviewTelemetry(base, now).metrics.equity, overviewTelemetry(readsAt(now - 900_000), now).metrics.pnl]) {
    assert.equal(metric.reason, "source_time_unavailable");
    assert.equal(metric.freshness, "unknown");
  }
});

test("small forward clock skew permits confirmed empty reads and a fresh projection", () => {
  for (const offset of [2_000, 5_000]) {
    const { metrics, agents } = overviewTelemetry(readsAt(now + offset), now);
    assert.equal(metrics.open.value, 0);
    for (const observation of [metrics.open, agents.PORTFOLIO, agents.WATCHER]) {
      assert.equal(observation.state, "confirmed_empty");
      assert.equal(observation.freshness, "fresh");
    }
    assert.equal(agents.SCAN.freshness, "fresh");
    assert.equal(metrics.equity.freshness, "unknown");
  }
});

test("source clocks beyond the skew allowance remain unknown", () => {
  for (const offset of [5_001, 60_000]) {
    const { metrics, agents } = overviewTelemetry(readsAt(now + offset), now);
    for (const observation of [metrics.open, agents.PORTFOLIO, agents.WATCHER, agents.SCAN]) {
      assert.equal(observation.sourceAt, null);
      assert.equal(observation.freshness, "unknown");
    }
    assert.equal(metrics.open.value, null);
  }
});

test("slightly advanced evidence and publication clocks order frames independently", () => {
  const earlier = new Date(now + 1_000).toISOString();
  const later = new Date(now + 2_000).toISOString();
  const path = "/api/positions";
  const first = acceptFrame(undefined, path, { as_of: earlier }, now, earlier);
  const latest = acceptFrame(first, path, { as_of: later }, now, later);
  assert.deepEqual(latest.data, { as_of: later });
  assert.equal(acceptFrame(latest, path, { as_of: earlier }, now, later), latest);
  assert.equal(acceptFrame(latest, path, { as_of: later, positions: [] }, now, earlier), latest);
});

test("portfolio is loading while either broker read awaits its first response", () => {
  for (const reads of [{ ...base, accounts: read(null) }, { ...base, book: read(null) }]) {
    const { PORTFOLIO } = overviewTelemetry(reads, now).agents;
    assert.equal(PORTFOLIO.state, "loading");
    assert.equal(PORTFOLIO.value, null);
  }
  assert.equal(overviewTelemetry({ ...base, accounts: read(null), book: read(null, "unauthorized") }, now).agents.PORTFOLIO.state, "unauthorized");
});
