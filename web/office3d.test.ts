import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { wibLight } from "./src/tradingdesk/office3d/lighting.ts";
import { bubbleText } from "./src/tradingdesk/office3d/office-copy.ts";
import { officeState, paperStack, type DeskId, type OfficeInput } from "./src/tradingdesk/office3d/office-state.ts";
import type { ObservedValue } from "./src/tradingdesk/types.ts";

const NOW = Date.parse("2026-11-26T02:00:00.000Z");
const published = new Date(NOW - 1000).toISOString();

function agent(value: string | null, over: Partial<ObservedValue<string | null>> = {}): ObservedValue<string | null> {
  return {
    value, state: "confirmed", availability: "confirmed", freshness: "fresh", source: "worker",
    sourceAt: published, receivedAt: new Date(NOW).toISOString(), reason: null, traceId: null, ...over,
  };
}

function agents(over: Partial<OfficeInput["agents"]> = {}) {
  return {
    SCAN: agent("ready"), ANALYST: agent("ready"), AEGIS: agent("pass"), EXECUTOR: agent("observe"),
    PORTFOLIO: agent("reported", { source: "threecommas" }), WATCHER: agent("reported"), ...over,
  };
}

function status(over: Record<string, unknown> = {}) {
  return {
    mode: "observe", reason: "ok", market_data_fresh: true, risk_permitted: true, execution_permitted: false,
    halt: false, gating: "current", published_at: published,
    pipeline: {
      screening: { state: "ready", source: "binance_spot_public", count: 12 },
      analysis: { state: "ready", count: 1, mode: "auto" },
      risk: { state: "pass", reason: "ok" },
      opportunities: { state: "ready", count: 1 },
    },
    research: {
      state: "ok", error_code: null, last_ok_at: null, last_failed_at: null,
      next_attempt_at: new Date(NOW + 60_000).toISOString(), consecutive_failures: 0, running: false,
    },
    providers: { budget: { calls_used: 4, calls_cap: 48 } },
    xau: { enabled: false },
    ...over,
  };
}

function input(over: Partial<OfficeInput> = {}): OfficeInput {
  return {
    status: status(), events: [], alerts: { alerts: [] }, alertsIssue: null,
    forward: { scored: 0, pending: 0 }, forwardIssue: null,
    breakers: { halt: false, risk_permitted: true, reason: "ok" }, breakersIssue: null,
    agents: agents(), stream: true, prior: null, ...over,
  };
}

function at(offset: number) {
  return new Date(NOW + offset).toISOString();
}

function props(model: ReturnType<typeof officeState>, id: DeskId) {
  return model.desks[id].prop;
}

test("loading and unauthorized stay neutral", () => {
  const loading = officeState(input({
    status: null,
    agents: agents({
      SCAN: agent(null, { state: "loading", freshness: "unknown", value: null }),
      ANALYST: agent(null, { state: "loading", freshness: "unknown", value: null }),
      AEGIS: agent(null, { state: "loading", freshness: "unknown", value: null }),
      EXECUTOR: agent(null, { state: "loading", freshness: "unknown", value: null }),
      PORTFOLIO: agent(null, { state: "loading", freshness: "unknown", value: null }),
      WATCHER: agent(null, { state: "loading", freshness: "unknown", value: null }),
    }),
  }), NOW);
  assert.equal(loading.desks.SCAN.pose, "unknown");
  assert.equal(loading.desks.SCAN.evidenceKey, "scan.missing");
  const denied = officeState(input({
    status: null,
    agents: agents({
      SCAN: agent(null, { state: "unauthorized", freshness: "unknown", value: null }),
      ANALYST: agent(null, { state: "unauthorized", freshness: "unknown", value: null }),
      AEGIS: agent(null, { state: "unauthorized", freshness: "unknown", value: null }),
      EXECUTOR: agent(null, { state: "unauthorized", freshness: "unknown", value: null }),
      PORTFOLIO: agent(null, { state: "unauthorized", freshness: "unknown", value: null }),
      WATCHER: agent(null, { state: "unauthorized", freshness: "unknown", value: null }),
    }),
  }), NOW);
  assert.equal(denied.desks.SCAN.bubble, "sessionExpired");
  assert.equal(denied.desks.PORTFOLIO.lamp, "off");
});

test("a stale publication beats an otherwise ready pose", () => {
  const model = officeState(input({ status: status({ published_at: at(-16 * 60_000) }) }), NOW);
  assert.equal(model.desks.SCAN.pose, "unknown");
  assert.equal(model.desks.SCAN.lamp, "off");
  assert.equal(model.desks.SCAN.evidenceKey, "scan.stale");
  assert.equal(model.desks.ANALYST.pose, "unknown");
  assert.equal(model.desks.PORTFOLIO.pose, "idle");
});

test("scanner types only when the screening block changes", () => {
  const first = officeState(input(), NOW);
  assert.equal(first.desks.SCAN.pose, "idle");
  assert.equal(first.desks.SCAN.evidenceKey, "scan.idle");
  const same = officeState(input({ prior: first.prior }), NOW);
  assert.equal(same.desks.SCAN.pose, "idle");
  const next = officeState(input({
    prior: same.prior,
    status: status({ pipeline: { ...status().pipeline, screening: { state: "ready", source: "binance_spot_public", count: 13 } } }),
  }), NOW);
  assert.equal(next.desks.SCAN.pose, "typing");
  assert.equal(next.desks.SCAN.lamp, "green");
  const blocked = officeState(input({ status: status({ market_data_fresh: false }) }), NOW);
  assert.equal(blocked.desks.SCAN.pose, "alert");
  assert.equal(blocked.desks.SCAN.lamp, "red");
});

test("research poses follow running, the wait, the budget and backoff", () => {
  const waiting = officeState(input(), NOW);
  assert.equal(waiting.desks.ANALYST.pose, "coffee");
  assert.equal(waiting.desks.ANALYST.lamp, "green");
  assert.equal(waiting.desks.ANALYST.evidenceKey, "analyst.wait");
  const running = officeState(input({
    status: status({ research: { ...status().research as object, running: true, state: "ok" } }),
  }), NOW);
  assert.equal(running.desks.ANALYST.pose, "thinking");
  assert.equal(running.desks.ANALYST.lamp, "blue");
  const idle = officeState(input({
    status: status({ research: { ...status().research as object, running: false, next_attempt_at: at(-1000) } }),
  }), NOW);
  assert.equal(idle.desks.ANALYST.pose, "idle");
  const budget = officeState(input({
    status: status({
      research: { ...status().research as object, state: "budget_wait", running: false },
      providers: { budget: { calls_used: 40, calls_cap: 48 } },
    }),
  }), NOW);
  assert.equal(budget.desks.ANALYST.pose, "coffee");
  assert.equal(budget.desks.ANALYST.lamp, "amber");
  assert.equal(budget.desks.ANALYST.facts.calls_used, "40");
  assert.equal(budget.desks.ANALYST.facts.calls_cap, "48");
  assert.match(bubbleText("en", "ANALYST", budget.desks.ANALYST), /40\/48/);
  const backoff = officeState(input({
    status: status({
      research: { state: "backoff", running: false, error_code: "providers_failed", next_attempt_at: at(120_000), consecutive_failures: 2 },
    }),
  }), NOW);
  assert.equal(backoff.desks.ANALYST.pose, "alert");
  assert.equal(backoff.desks.ANALYST.lamp, "red");
  assert.equal(backoff.desks.ANALYST.facts.error_code, "providers_failed");
  const missing = officeState(input({ status: status({ research: undefined }) }), NOW);
  assert.equal(missing.desks.ANALYST.evidenceKey, "analyst.no_research");
});

test("halt and insufficient free USDT are alerts, and a pass is idle", () => {
  const halted = officeState(input({ status: status({ halt: true, reason: "halt" }) }), NOW);
  assert.equal(halted.desks.AEGIS.pose, "alert");
  assert.equal(halted.desks.AEGIS.prop, "alarm");
  assert.equal(halted.desks.AEGIS.lamp, "red");
  const sleeve = officeState(input({
    status: status({ risk_permitted: false, reason: "insufficient_free_usdt", pipeline: { ...status().pipeline, risk: { state: "blocked", reason: "insufficient_free_usdt" } } }),
  }), NOW);
  assert.equal(sleeve.desks.AEGIS.pose, "alert");
  assert.equal(sleeve.desks.AEGIS.bubble, "officeFreeUsdt");
  assert.equal(sleeve.desks.AEGIS.prop, "none");
  assert.deepEqual(sleeve.desks.AEGIS.facts, {});
  const pass = officeState(input(), NOW);
  assert.equal(pass.desks.AEGIS.pose, "idle");
  assert.equal(pass.desks.AEGIS.lamp, "green");
});

test("observe never stamps an order, including a confirmed row and an empty window", () => {
  const quiet = officeState(input(), NOW);
  assert.equal(quiet.desks.EXECUTOR.pose, "idle");
  assert.equal(quiet.desks.EXECUTOR.lamp, "blue");
  assert.equal(quiet.desks.EXECUTOR.plate, "OBSERVE");
  assert.equal(quiet.desks.EXECUTOR.prop, "none");
  const candidate = officeState(input({ events: [{ event: "observe_candidate", ts: at(-1000) }] }), NOW);
  assert.equal(candidate.desks.EXECUTOR.pose, "stamping");
  assert.equal(candidate.desks.EXECUTOR.prop, "paper_ticket_observe");
  const confirmedWhileObserving = officeState(input({
    events: [{ event: "strategy_confirmed", ts: at(-1000) }, { event: "observe_candidate", ts: at(-2000) }],
  }), NOW);
  assert.notEqual(confirmedWhileObserving.desks.EXECUTOR.prop, "order_flag");
  const paper = officeState(input({
    status: status({ mode: "paper", execution_permitted: true }),
    events: [{ event: "strategy_confirmed", ts: at(-1000) }],
    agents: agents({ EXECUTOR: agent("permitted") }),
  }), NOW);
  assert.equal(paper.desks.EXECUTOR.prop, "order_flag");
  assert.equal(paper.desks.EXECUTOR.pose, "stamping");
  const observeFixtures = [quiet, candidate, confirmedWhileObserving];
  for (const model of observeFixtures) {
    for (const id of Object.keys(model.desks) as DeskId[]) assert.notEqual(props(model, id), "order_flag");
  }
});

test("portfolio uses the account observation and nothing invented", () => {
  const fresh = officeState(input(), NOW);
  assert.equal(fresh.desks.PORTFOLIO.pose, "idle");
  assert.equal(fresh.desks.PORTFOLIO.lamp, "green");
  assert.equal(fresh.desks.PORTFOLIO.bubble, "readConfirmed");
  const partial = officeState(input({
    agents: agents({ PORTFOLIO: agent(null, { state: "partial", freshness: "unknown", availability: "partial" }) }),
  }), NOW);
  assert.equal(partial.desks.PORTFOLIO.lamp, "amber");
  assert.equal(partial.desks.PORTFOLIO.bubble, "readPartial");
  const stale = officeState(input({
    agents: agents({ PORTFOLIO: agent(null, { state: "stale", freshness: "stale", value: null }) }),
  }), NOW);
  assert.equal(stale.desks.PORTFOLIO.pose, "unknown");
  assert.equal(stale.desks.PORTFOLIO.lamp, "off");
});

function xau(over: Record<string, unknown> = {}) {
  return {
    enabled: true, state: "running", warming: false, window: { name: "London KZ" }, engine: "SMC",
    last_bar_age_s: 40, basis_samples: 10, last_error: null, halt: { global: false, xau: false },
    today: { signals_sent: 1 }, open_signal: null, ...over,
  };
}

test("XAU desk follows the window, the warm-up, a fault and a real signal delta", () => {
  const off = officeState(input(), NOW);
  assert.equal(off.desks.XAU.present, false);
  assert.equal(off.desks.XAU.lamp, "off");
  const closed = officeState(input({ status: status({ xau: xau({ window: { name: "closed" } }) }) }), NOW);
  assert.equal(closed.desks.XAU.pose, "sleeping");
  assert.equal(closed.desks.XAU.lamp, "off");
  const warming = officeState(input({ status: status({ xau: xau({ warming: true, state: "warming", basis_samples: 3 }) }) }), NOW);
  assert.equal(warming.desks.XAU.pose, "warming");
  assert.equal(warming.desks.XAU.lamp, "amber");
  assert.match(bubbleText("en", "XAU", warming.desks.XAU), /3\/10/);
  const degraded = officeState(input({ status: status({ xau: xau({ state: "degraded", last_error: "socket reset" }) }) }), NOW);
  assert.equal(degraded.desks.XAU.pose, "alert");
  assert.equal(degraded.desks.XAU.lamp, "red");
  assert.equal(degraded.desks.XAU.facts.last_error, "socket reset");
  const live = officeState(input({ status: status({ xau: xau() }) }), NOW);
  assert.equal(live.desks.XAU.pose, "typing");
  assert.equal(live.desks.XAU.prop, "none");
  const again = officeState(input({ status: status({ xau: xau({ today: { signals_sent: 2 } }) }), prior: live.prior }), NOW);
  assert.equal(again.desks.XAU.prop, "signal_flag");
  const held = officeState(input({ status: status({ xau: xau({ today: { signals_sent: 2 } }) }), prior: again.prior }), NOW + 4000);
  assert.equal(held.desks.XAU.prop, "signal_flag");
  const expired = officeState(input({ status: status({ xau: xau({ today: { signals_sent: 2 } }) }), prior: held.prior }), NOW + 13_000);
  assert.equal(expired.desks.XAU.prop, "none");
});

test("forward stamping is a scored delta, otherwise pending or coffee", () => {
  const clear = officeState(input(), NOW);
  assert.equal(clear.desks.FORWARD.pose, "coffee");
  assert.equal(clear.desks.FORWARD.evidenceKey, "forward.clear");
  const pending = officeState(input({ forward: { scored: 9, pending: 25 } }), NOW);
  assert.equal(pending.desks.FORWARD.pose, "idle");
  assert.equal(pending.desks.FORWARD.prop, "none");
  assert.equal(pending.desks.FORWARD.stack, paperStack(25));
  assert.match(bubbleText("en", "FORWARD", pending.desks.FORWARD), /25/);
  const scored = officeState(input({ forward: { scored: 10, pending: 25 }, prior: pending.prior }), NOW);
  assert.equal(scored.desks.FORWARD.pose, "stamping");
  assert.equal(scored.desks.FORWARD.prop, "scorecard");
  const failed = officeState(input({ forward: null, forwardIssue: "request_failed" }), NOW);
  assert.equal(failed.desks.FORWARD.pose, "unknown");
});

test("alerts phone on a new delivered id, failed rows stay red, and a down stream steps away", () => {
  const first = officeState(input({ alerts: { alerts: [{ id: 4, event: "halt", state: "delivered", created_at: published }] } }), NOW);
  assert.equal(first.desks.WATCHER.pose, "idle");
  const delivered = officeState(input({
    alerts: { alerts: [{ id: 5, event: "halt", state: "delivered", created_at: published }] },
    prior: first.prior,
  }), NOW);
  assert.equal(delivered.desks.WATCHER.pose, "phone");
  assert.equal(delivered.desks.WATCHER.lamp, "blue");
  const failed = officeState(input({
    alerts: { alerts: [{ id: 6, event: "alert_failed", state: "failed", last_error: "no channel", created_at: published }] },
  }), NOW);
  assert.equal(failed.desks.WATCHER.pose, "alert");
  assert.equal(failed.desks.WATCHER.lamp, "red");
  const loading = officeState(input({ alerts: null }), NOW);
  const firstPayload = officeState(input({
    alerts: { alerts: [{ id: 7, event: "halt", state: "delivered", created_at: published }] },
    prior: loading.prior,
  }), NOW);
  assert.equal(firstPayload.desks.WATCHER.pose, "idle");
  const down = officeState(input({ stream: false }), NOW);
  assert.equal(down.desks.WATCHER.pose, "away");
  assert.equal(down.desks.WATCHER.lamp, "amber");
  assert.equal(down.desks.WATCHER.bubble, "reconnecting");
});

test("the first frame does not invent delta motion", () => {
  const model = officeState(input({
    forward: { scored: 9, pending: 4 },
    alerts: { alerts: [{ id: 3, event: "halt", state: "delivered", created_at: published }] },
    status: status({ xau: xau({ today: { signals_sent: 5 }, open_signal: { id: "row" } }) }),
  }), NOW);
  assert.equal(model.desks.SCAN.pose, "idle");
  assert.equal(model.desks.XAU.prop, "none");
  assert.equal(model.desks.FORWARD.pose, "idle");
  assert.equal(model.desks.WATCHER.pose, "idle");
  assert.equal(model.prior.signalsSent, 5);
  assert.equal(model.prior.scored, 9);
  assert.equal(model.prior.maxAlertId, 3);
});

test("WIB light is day at 09:00, night at 22:00, and half-blended at the shoulders", () => {
  assert.equal(wibLight(Date.parse("2026-11-26T02:00:00Z")).phase, "day");
  assert.equal(wibLight(Date.parse("2026-11-26T15:00:00Z")).phase, "night");
  const dawn = wibLight(Date.parse("2026-11-25T22:45:00Z"));
  assert.equal(dawn.phase, "dawn");
  assert.equal(dawn.day, 0.5);
  const dusk = wibLight(Date.parse("2026-11-26T11:15:00Z"));
  assert.equal(dusk.phase, "dusk");
  assert.equal(dusk.day, 0.5);
});

test("office3d source does not pick poses with Math.random or setInterval", () => {
  const dir = fileURLToPath(new URL("./src/tradingdesk/office3d/", import.meta.url));
  const files = readdirSync(dir).filter((name) => name.endsWith(".ts") || name.endsWith(".tsx"));
  assert.ok(files.length > 5);
  for (const name of files) {
    const source = readFileSync(dir + name, "utf8");
    assert.equal(source.includes("Math.random"), false, name);
    assert.equal(source.includes("setInterval"), false, name);
  }
});

test("paper stack is a capped log of the real pending count", () => {
  assert.equal(paperStack(0), 0);
  assert.equal(paperStack(1), 1);
  assert.equal(paperStack(25), 2);
  assert.equal(paperStack(1000), 4);
});
