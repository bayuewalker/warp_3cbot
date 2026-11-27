/**
 * Pure desk poses. Every pose, lamp, prop and number comes from a real field
 * or from a delta between two consecutive frames. No timers and no random choice.
 */
import { type Key } from "../../i18n.ts";
import { instant, record, sourceInstant } from "../frames.ts";
import { EVENT_WINDOW_MS, PUBLICATION_TTL_MS, type Station } from "../room.ts";
import type { ObservedValue } from "../types.ts";

export const DESKS = ["SCAN", "ANALYST", "AEGIS", "EXECUTOR", "PORTFOLIO", "XAU", "FORWARD", "WATCHER"] as const;
export type DeskId = (typeof DESKS)[number];
export type Pose = "idle" | "typing" | "thinking" | "coffee" | "alert" | "sleeping" | "warming" | "stamping" | "phone" | "away" | "unknown";
export type Lamp = "off" | "green" | "amber" | "red" | "blue";
export type Prop = "none" | "paper_ticket_observe" | "order_flag" | "signal_flag" | "alarm" | "scorecard";
export type Freshness = "fresh" | "stale" | "unknown";

export type FieldRow = { name: string; value: string };
export type DeskState = {
  pose: Pose;
  lamp: Lamp;
  prop: Prop;
  bubble: Key;
  freshness: Freshness;
  sourceAt: string | null;
  evidenceKey: string;
  present: boolean;
  stack: number;
  plate: "OBSERVE" | null;
  facts: Record<string, string>;
  fields: FieldRow[];
};

export type PriorFrame = {
  screeningKey: string | null;
  signalsSent: number | null;
  hadOpenSignal: boolean;
  xauSeen: boolean;
  scored: number | null;
  alertsSeen: boolean;
  maxAlertId: number | null;
  xauSignalUntil: number | null;
  alertUntil: number | null;
  alertKind: "phone" | null;
};

export type OfficeInput = {
  status: unknown;
  events: unknown;
  alerts: unknown;
  alertsIssue: string | null;
  forward: unknown;
  forwardIssue: string | null;
  breakers: unknown;
  breakersIssue: string | null;
  agents: Partial<Record<Station, ObservedValue<string | null>>> | null;
  stream: boolean;
  prior: PriorFrame | null;
};

export type OfficeModel = { desks: Record<DeskId, DeskState>; prior: PriorFrame };

const ACTIVE_WINDOW = new Set(["London KZ", "NY KZ", "London EMA", "NY EMA"]);
const CLOSED_WINDOW = new Set(["closed", "off"]);
const XAU_BAD = new Set(["degraded", "feed_unavailable", "config_error"]);
/** Matches MIN_SAMPLES in the XAU basis book. Shown only next to a real sample count. */
export const BASIS_READY = 10;
const BAR_FRESH_S = 180;

export function paperStack(pending: number): number {
  if (!Number.isFinite(pending) || pending <= 0) return 0;
  return Math.min(4, Math.floor(Math.log10(pending)) + 1);
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function field(name: string, value: unknown): FieldRow | null {
  if (value === undefined) return null;
  if (value === null) return { name, value: "null" };
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return { name, value: String(value) };
  return null;
}

function rows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(record) : [];
}

function agentOf(input: OfficeInput, role: Station): ObservedValue<string | null> | null {
  return input.agents?.[role] ?? null;
}

function publicationFreshness(stamp: string | null, now: number): Freshness {
  const time = sourceInstant(stamp, now);
  if (time == null) return "unknown";
  return Math.max(0, now - time) > PUBLICATION_TTL_MS ? "stale" : "fresh";
}

function combine(agent: ObservedValue<string | null> | null, publication: Freshness): Freshness {
  if (agent?.freshness === "stale" || agent?.state === "stale" || publication === "stale") return "stale";
  if (!agent) return publication;
  if (agent.state === "unauthorized" || agent.state === "error" || agent.state === "unavailable") return "unknown";
  if (agent.state === "loading" || agent.state === "awaiting_first_snapshot") return "unknown";
  if (agent.freshness === "fresh" && publication === "fresh") return "fresh";
  return "unknown";
}

function blank(partial: Pick<DeskState, "pose" | "lamp" | "prop" | "bubble" | "freshness" | "sourceAt" | "evidenceKey"> & Partial<DeskState>): DeskState {
  return { present: true, stack: 0, plate: null, facts: {}, fields: [], ...partial };
}

function neutral(kind: "stale" | "denied" | "loading" | "unknown", sourceAt: string | null, evidenceKey: string, fields: FieldRow[] = []): DeskState {
  const bubble: Key = kind === "stale" ? "readStale" : kind === "denied" ? "sessionExpired" : kind === "loading" ? "loading" : "unknown";
  const freshness: Freshness = kind === "stale" ? "stale" : "unknown";
  return blank({ pose: "unknown", lamp: "off", prop: "none", bubble, freshness, sourceAt, evidenceKey, fields });
}

function blocked(agent: ObservedValue<string | null> | null, freshness: Freshness, sourceAt: string | null, key: string, fields: FieldRow[]): DeskState | null {
  if (agent?.state === "unauthorized" || agent?.reason === "unauthorized") return neutral("denied", null, key + ".denied", fields);
  if (agent?.state === "loading" || agent?.state === "awaiting_first_snapshot") return neutral("loading", sourceAt, key + ".loading", fields);
  if (freshness === "stale" || agent?.state === "stale") return neutral("stale", sourceAt, key + ".stale", fields);
  if (freshness !== "fresh") return neutral("unknown", sourceAt, key + ".unknown", fields);
  return null;
}

function eventInWindow(events: unknown, name: string, now: number): { ts: string } | null {
  let best: { ts: string; time: number } | null = null;
  for (const row of rows(events)) {
    if (row.event !== name || typeof row.ts !== "string") continue;
    const time = sourceInstant(row.ts, now);
    if (time == null || Math.max(0, now - time) > EVENT_WINDOW_MS) continue;
    if (!best || time >= best.time) best = { ts: row.ts, time };
  }
  return best ? { ts: best.ts } : null;
}

function screeningKey(status: Record<string, unknown> | null): string | null {
  const pipeline = status && record(status.pipeline) ? status.pipeline : null;
  const screening = pipeline && record(pipeline.screening) ? pipeline.screening : null;
  if (!screening) return null;
  const state = text(screening.state) ?? "";
  const count = num(screening.count);
  const source = text(screening.source) ?? "";
  return state + "|" + (count == null ? "" : String(count)) + "|" + source;
}

function riskBubble(reason: string | null): { bubble: Key; facts: Record<string, string> } {
  if (reason === "insufficient_free_usdt") return { bubble: "officeFreeUsdt", facts: {} };
  if (reason === "mtm_unreliable") return { bubble: "officeMtm", facts: {} };
  if (reason === "abnormal_volatility") return { bubble: "officeVolatility", facts: {} };
  if (reason === "market_data") return { bubble: "officeMarketData", facts: {} };
  if (reason === "IntegrationError") return { bubble: "officeIntegration", facts: {} };
  if (reason && reason !== "ok") return { bubble: "officeRiskBlock", facts: { reason: reason.replaceAll("_", " ") } };
  return { bubble: "officeRiskBlock", facts: {} };
}

type AlertRow = { id: number; state: string; event: string; at: string | null; error: string | null; attempts: number | null };

const PHONE_STATE = new Set(["pending", "delivered", "resolved"]);

/** A failed row, or a pending row that already has an error and at least one attempt. */
export function alertFailed(row: Pick<AlertRow, "state" | "error" | "attempts">): boolean {
  if (row.state === "failed") return true;
  return row.state === "pending" && row.error != null && row.attempts != null && row.attempts >= 1;
}

function alertRows(payload: unknown): AlertRow[] {
  const body = record(payload) ? payload : null;
  const list = body ? rows(body.alerts) : rows(payload);
  const out: AlertRow[] = [];
  for (const row of list) {
    if (typeof row.id !== "number" || !Number.isFinite(row.id)) continue;
    out.push({
      id: row.id,
      state: text(row.state) ?? "",
      event: text(row.event) ?? "",
      at: text(row.delivered_at) ?? text(row.created_at),
      error: text(row.last_error),
      attempts: num(row.attempts),
    });
  }
  return out;
}

function maxAlertId(list: AlertRow[]): number | null {
  return list.reduce<number | null>((max, row) => max == null || row.id > max ? row.id : max, null);
}

export function officeState(input: OfficeInput, now: number): OfficeModel {
  const status = record(input.status) ? input.status : null;
  const published = text(status?.published_at);
  const age = status ? publicationFreshness(published, now) : "unknown";
  const priorIn = input.prior;
  const screenKey = screeningKey(status);
  const scanChanged = priorIn != null && priorIn.screeningKey != null && screenKey != null && priorIn.screeningKey !== screenKey;

  const xau = status && record(status.xau) ? status.xau : null;
  const today = xau && record(xau.today) ? xau.today : null;
  const sent = num(today?.signals_sent);
  const open = Boolean(xau && xau.open_signal != null);
  const sentUp = priorIn != null && priorIn.xauSeen && priorIn.signalsSent != null && sent != null && sent > priorIn.signalsSent;
  const opened = priorIn != null && priorIn.xauSeen && !priorIn.hadOpenSignal && open;
  let xauSignalUntil = priorIn?.xauSignalUntil ?? null;
  if (sentUp || opened) xauSignalUntil = now + EVENT_WINDOW_MS;
  if (xauSignalUntil != null && now >= xauSignalUntil) xauSignalUntil = null;

  const forward = record(input.forward) ? input.forward : null;
  const scored = num(forward?.scored);
  const pending = num(forward?.pending);
  const scoredDelta = priorIn != null && priorIn.scored != null && scored != null && scored > priorIn.scored;

  const alerts = alertRows(input.alerts);
  const sawAlerts = record(input.alerts) || Array.isArray(input.alerts);
  const alertMax = maxAlertId(alerts);
  let alertUntil = priorIn?.alertUntil ?? null;
  let alertKind = priorIn?.alertKind ?? null;
  if (priorIn?.alertsSeen) {
    const floor = priorIn.maxAlertId;
    let newest: AlertRow | null = null;
    for (const row of alerts) {
      if (floor != null && row.id <= floor) continue;
      if (!newest || row.id > newest.id) newest = row;
    }
    if (newest && PHONE_STATE.has(newest.state) && !alertFailed(newest)) {
      alertUntil = now + EVENT_WINDOW_MS;
      alertKind = "phone";
    }
  }
  if (alertUntil != null && now >= alertUntil) {
    alertUntil = null;
    alertKind = null;
  }

  const prior: PriorFrame = {
    screeningKey: screenKey,
    signalsSent: sent,
    hadOpenSignal: open,
    xauSeen: xau != null || priorIn?.xauSeen === true,
    scored,
    alertsSeen: sawAlerts || priorIn?.alertsSeen === true,
    maxAlertId: alertMax ?? priorIn?.maxAlertId ?? null,
    xauSignalUntil,
    alertUntil,
    alertKind,
  };

  const shared = { input, now, status, published, age, scanChanged, xau, sent, open, signal: xauSignalUntil != null, scored, pending, scoredDelta, alerts, phone: alertKind === "phone" && alertUntil != null };
  const desks: Record<DeskId, DeskState> = {
    SCAN: scanDesk(shared),
    ANALYST: analystDesk(shared),
    AEGIS: aegisDesk(shared),
    EXECUTOR: executorDesk(shared),
    PORTFOLIO: portfolioDesk(shared),
    XAU: xauDesk(shared),
    FORWARD: forwardDesk(shared),
    WATCHER: watcherDesk(shared),
  };
  if (text(status?.mode) === "observe") {
    for (const id of DESKS) {
      if (desks[id].prop === "order_flag") {
        desks[id] = { ...desks[id], prop: "none", evidenceKey: desks[id].evidenceKey + ".observe_guard" };
      }
    }
  }
  return { desks, prior };
}

type Shared = {
  input: OfficeInput;
  now: number;
  status: Record<string, unknown> | null;
  published: string | null;
  age: Freshness;
  scanChanged: boolean;
  xau: Record<string, unknown> | null;
  sent: number | null;
  open: boolean;
  signal: boolean;
  scored: number | null;
  pending: number | null;
  scoredDelta: boolean;
  alerts: AlertRow[];
  phone: boolean;
};

function scanDesk(ctx: Shared): DeskState {
  const agent = agentOf(ctx.input, "SCAN");
  const sourceAt = agent?.sourceAt ?? ctx.published;
  const fields = [field("market_data_fresh", ctx.status?.market_data_fresh), field("published_at", ctx.published)].filter((row): row is FieldRow => row != null);
  if (!ctx.status) return neutral(agent?.state === "unauthorized" ? "denied" : "loading", null, "scan.missing", fields);
  const freshness = combine(agent, ctx.age);
  const gate = blocked(agent, freshness, sourceAt, "scan", fields);
  if (gate) return gate;
  const pipeline = record(ctx.status.pipeline) ? ctx.status.pipeline : null;
  const screening = pipeline && record(pipeline.screening) ? pipeline.screening : null;
  const state = text(screening?.state);
  const blockedFeed = state === "blocked" || ctx.status.market_data_fresh === false;
  const ready = state === "ready" && ctx.status.market_data_fresh === true;
  const marketEvent = eventInWindow(ctx.input.events, "market_data_blocked", ctx.now);
  if (blockedFeed) {
    return blank({
      pose: "alert", lamp: "red", prop: "none", bubble: "officeScanBlocked", freshness, sourceAt: marketEvent?.ts ?? sourceAt,
      evidenceKey: "scan.blocked", fields, facts: marketEvent ? { route: "/api/events" } : {},
    });
  }
  if (ready) {
    return blank({
      pose: ctx.scanChanged ? "typing" : "idle", lamp: "green", prop: "none",
      bubble: ctx.scanChanged ? "officeScanNew" : "officeScanReady", freshness, sourceAt, fields,
      evidenceKey: ctx.scanChanged ? "scan.typing" : "scan.idle",
    });
  }
  return neutral("unknown", sourceAt, "scan.unknown", fields);
}

function analystDesk(ctx: Shared): DeskState {
  const agent = agentOf(ctx.input, "ANALYST");
  const sourceAt = agent?.sourceAt ?? ctx.published;
  const research = ctx.status && record(ctx.status.research) ? ctx.status.research : null;
  const fields = research ? ["state", "error_code", "next_attempt_at", "running", "consecutive_failures"].flatMap((name) => {
    const row = field(name, research[name]);
    return row ? [row] : [];
  }) : [];
  if (!ctx.status) return neutral(agent?.state === "unauthorized" ? "denied" : "loading", null, "analyst.missing", fields);
  const freshness = combine(agent, ctx.age);
  const analysis = ctx.status && record(ctx.status.pipeline) && record(ctx.status.pipeline.analysis) ? ctx.status.pipeline.analysis : null;
  const analysisBlocked = text(analysis?.state) === "blocked";
  const gate = blocked(agent, freshness, sourceAt, "analyst", fields);
  // analysis "blocked" is not a recognized agent value, so the observation is unavailable.
  // A fresh publication still carries the research state that blocked the stage.
  if (gate && !(analysisBlocked && gate.evidenceKey === "analyst.unknown" && ctx.age === "fresh")) return gate;
  if (!research) {
    if (analysisBlocked && ctx.age === "fresh") {
      return blank({ pose: "alert", lamp: "amber", prop: "none", bubble: "officeResearchBackoff", freshness: "fresh", sourceAt, evidenceKey: "analyst.analysis_blocked", fields });
    }
    return neutral("unknown", sourceAt, "analyst.no_research", fields);
  }
  const providers = record(ctx.status.providers) ? ctx.status.providers : null;
  const budget = providers && record(providers.budget) ? providers.budget : null;
  const used = num(budget?.calls_used);
  const cap = num(budget?.calls_cap);
  const budgetFacts: Record<string, string> = {};
  if (used != null) budgetFacts.calls_used = String(used);
  if (cap != null) budgetFacts.calls_cap = String(cap);
  if (research.running === true) {
    return blank({ pose: "thinking", lamp: "blue", prop: "none", bubble: "officeResearchRun", freshness, sourceAt, evidenceKey: "analyst.running", fields, facts: budgetFacts });
  }
  const state = text(research.state);
  const next = text(research.next_attempt_at);
  const nextAt = next ? instant(next) : null;
  if (state === "budget_wait" || state === "capped") {
    return blank({ pose: "coffee", lamp: "amber", prop: "none", bubble: "officeResearchBudget", freshness, sourceAt, evidenceKey: "analyst.budget", fields, facts: budgetFacts });
  }
  if (state === "backoff" || state === "failed") {
    const facts: Record<string, string> = {};
    const code = text(research.error_code);
    if (code) facts.error_code = code;
    if (next) facts.next_attempt_at = next;
    return blank({ pose: "alert", lamp: "red", prop: "none", bubble: "officeResearchBackoff", freshness, sourceAt, evidenceKey: "analyst.backoff", fields, facts });
  }
  if (analysisBlocked) {
    return blank({ pose: "alert", lamp: "amber", prop: "none", bubble: "officeResearchBackoff", freshness, sourceAt, evidenceKey: "analyst.analysis_blocked", fields, facts: budgetFacts });
  }
  if (state === "ok") {
    const waiting = nextAt != null && nextAt > ctx.now;
    return blank({
      pose: waiting ? "coffee" : "idle", lamp: "green", prop: "none",
      bubble: waiting ? "officeResearchWait" : "officeResearchIdle", freshness, sourceAt, fields,
      evidenceKey: waiting ? "analyst.wait" : "analyst.idle",
      facts: waiting && next ? { next_attempt_at: next } : {},
    });
  }
  return neutral("unknown", sourceAt, "analyst.unknown", fields);
}

function aegisDesk(ctx: Shared): DeskState {
  const agent = agentOf(ctx.input, "AEGIS");
  const sourceAt = agent?.sourceAt ?? ctx.published;
  const breakersOk = ctx.input.breakersIssue == null && record(ctx.input.breakers);
  const breakers = breakersOk && record(ctx.input.breakers) ? ctx.input.breakers : null;
  const pipeline = ctx.status && record(ctx.status.pipeline) ? ctx.status.pipeline : null;
  const risk = pipeline && record(pipeline.risk) ? pipeline.risk : null;
  const fields = [
    field("halt", ctx.status?.halt), field("risk_permitted", ctx.status?.risk_permitted), field("reason", ctx.status?.reason),
    field("risk.state", text(risk?.state)), field("breakers.halt", breakers?.halt),
  ].filter((row): row is FieldRow => row != null);
  if (!ctx.status) return neutral(agent?.state === "unauthorized" ? "denied" : "loading", null, "aegis.missing", fields);
  const freshness = combine(agent, ctx.age);
  const gate = blocked(agent, freshness, sourceAt, "aegis", fields);
  if (gate) return gate;
  const halted = ctx.status.halt === true || breakers?.halt === true;
  if (halted) {
    return blank({ pose: "alert", lamp: "red", prop: "alarm", bubble: "officeHalt", freshness, sourceAt, evidenceKey: "aegis.halt", fields });
  }
  const reason = text(ctx.status.reason) ?? text(risk?.reason) ?? text(breakers?.reason);
  const blockedRisk = ctx.status.risk_permitted === false || breakers?.risk_permitted === false || text(risk?.state) === "blocked";
  if (blockedRisk) {
    const mapped = riskBubble(reason);
    return blank({ pose: "alert", lamp: "red", prop: "none", bubble: mapped.bubble, freshness, sourceAt, evidenceKey: "aegis.blocked", fields, facts: mapped.facts });
  }
  if (text(risk?.state) === "pass" && ctx.status.risk_permitted === true) {
    return blank({ pose: "idle", lamp: "green", prop: "none", bubble: "officeRiskPass", freshness, sourceAt, evidenceKey: "aegis.pass", fields });
  }
  return neutral("unknown", sourceAt, "aegis.unknown", fields);
}

function executorDesk(ctx: Shared): DeskState {
  const agent = agentOf(ctx.input, "EXECUTOR");
  const sourceAt = agent?.sourceAt ?? ctx.published;
  const mode = text(ctx.status?.mode);
  const fields = [field("mode", mode), field("execution_permitted", ctx.status?.execution_permitted), field("gating", ctx.status?.gating)].filter((row): row is FieldRow => row != null);
  if (!ctx.status) return neutral(agent?.state === "unauthorized" ? "denied" : "loading", null, "executor.missing", fields);
  const freshness = combine(agent, ctx.age);
  const gate = blocked(agent, freshness, sourceAt, "executor", fields);
  if (gate) return gate;
  const gating = text(ctx.status.gating);
  if (gating === "halt") {
    return blank({ pose: "away", lamp: "red", prop: "none", bubble: "officeExecutorAway", freshness, sourceAt, evidenceKey: "executor.gating_halt", fields });
  }
  if (gating === "awaiting_worker") {
    return blank({ pose: "unknown", lamp: "off", prop: "none", bubble: "officeExecutorAway", freshness, sourceAt, evidenceKey: "executor.awaiting", fields });
  }
  const skipped = eventInWindow(ctx.input.events, "duplicate_skipped", ctx.now);
  const facts: Record<string, string> = skipped ? { duplicate: "1", route: "/api/events" } : {};
  if (mode === "observe") {
    const candidate = eventInWindow(ctx.input.events, "observe_candidate", ctx.now);
    if (candidate) {
      return blank({
        pose: "stamping", lamp: "blue", prop: "paper_ticket_observe", bubble: "officeCandidate", freshness,
        sourceAt: candidate.ts, evidenceKey: "executor.observe_candidate", fields, facts: { ...facts, route: "/api/events" }, plate: "OBSERVE",
      });
    }
    return blank({ pose: "idle", lamp: "blue", prop: "none", bubble: "officeObserveIdle", freshness, sourceAt, evidenceKey: "executor.observe", fields, facts, plate: "OBSERVE" });
  }
  if (mode === "paper" || mode === "live") {
    const confirmed = eventInWindow(ctx.input.events, "strategy_confirmed", ctx.now);
    if (confirmed) {
      return blank({
        pose: "stamping", lamp: "green", prop: "order_flag", bubble: "officeConfirmed", freshness,
        sourceAt: confirmed.ts, evidenceKey: "executor.order", fields, facts: { route: "/api/events" },
      });
    }
    const permitted = ctx.status.execution_permitted === true;
    return blank({
      pose: "idle", lamp: permitted ? "green" : "amber", prop: "none", bubble: "officeExecutorIdle", freshness, sourceAt,
      evidenceKey: "executor.idle", fields, facts,
    });
  }
  return neutral("unknown", sourceAt, "executor.unknown", fields);
}

function portfolioDesk(ctx: Shared): DeskState {
  const agent = agentOf(ctx.input, "PORTFOLIO");
  const sourceAt = agent?.sourceAt ?? null;
  const fields = [field("state", agent?.state ?? null), field("value", agent?.value ?? null)].filter((row): row is FieldRow => row != null);
  if (!agent || agent.state === "loading" || agent.state === "awaiting_first_snapshot") return neutral("loading", sourceAt, "portfolio.loading", fields);
  if (agent.state === "unauthorized") return neutral("denied", null, "portfolio.denied", fields);
  if (agent.state === "stale" || agent.freshness === "stale" || agent.state === "unavailable" || agent.state === "error") {
    return neutral(agent.state === "stale" || agent.freshness === "stale" ? "stale" : "unknown", sourceAt, "portfolio.unknown", fields);
  }
  if (agent.state === "partial" || agent.freshness !== "fresh") {
    const unknown = agent.freshness !== "fresh" && agent.state !== "partial";
    return blank({
      pose: unknown ? "unknown" : "idle", lamp: unknown ? "off" : "amber", prop: "none",
      bubble: agent.state === "partial" ? "readPartial" : "freshnessUnknown",
      freshness: agent.freshness === "fresh" ? "unknown" : agent.freshness, sourceAt,
      evidenceKey: agent.state === "partial" ? "portfolio.partial" : "portfolio.unknown", fields,
    });
  }
  const empty = agent.state === "confirmed_empty" || agent.value === "empty";
  return blank({
    pose: "idle", lamp: "green", prop: "none", bubble: empty ? "readEmpty" : "readConfirmed",
    freshness: "fresh", sourceAt, evidenceKey: "portfolio.fresh", fields,
  });
}

function xauDesk(ctx: Shared): DeskState {
  const sourceAt = ctx.published;
  const xau = ctx.xau;
  const window = xau && record(xau.window) ? xau.window : null;
  const halt = xau && record(xau.halt) ? xau.halt : null;
  const windowName = text(window?.name);
  const state = text(xau?.state);
  const fields = [
    field("enabled", xau?.enabled), field("state", state), field("window.name", windowName), field("engine", xau?.engine),
    field("last_bar_age_s", xau?.last_bar_age_s), field("basis_samples", xau?.basis_samples), field("warming", xau?.warming),
    field("last_error", xau?.last_error), field("halt.global", halt?.global), field("halt.xau", halt?.xau),
    field("today.signals_sent", ctx.sent), field("open_signal", ctx.open ? "present" : "null"),
  ].filter((row): row is FieldRow => row != null);
  if (!ctx.status) return neutral("loading", null, "xau.missing", fields);
  if (ctx.age !== "fresh") return neutral(ctx.age === "stale" ? "stale" : "unknown", sourceAt, "xau." + ctx.age, fields);
  if (!xau || xau.enabled !== true) {
    return blank({ pose: "unknown", lamp: "off", prop: "none", bubble: "officeXauOff", freshness: "fresh", sourceAt, evidenceKey: "xau.disabled", fields, present: false });
  }
  const error = text(xau.last_error);
  const facts: Record<string, string> = {};
  if (state) facts.state = state;
  if (windowName) facts.window = windowName;
  const engine = text(xau.engine);
  if (engine) facts.engine = engine;
  if (error) facts.last_error = error.length > 80 ? error.slice(0, 80) : error;
  const samples = num(xau.basis_samples);
  if (samples != null) facts.basis_samples = String(samples);
  let desk: DeskState;
  if (halt?.xau === true || halt?.global === true) {
    desk = blank({ pose: "alert", lamp: "red", prop: "alarm", bubble: "officeXauHalt", freshness: "fresh", sourceAt, evidenceKey: "xau.halt", fields, facts });
  } else if (state != null && XAU_BAD.has(state)) {
    desk = blank({ pose: "alert", lamp: "red", prop: "none", bubble: "officeXauBad", freshness: "fresh", sourceAt, evidenceKey: "xau.degraded", fields, facts });
  } else if (windowName != null && CLOSED_WINDOW.has(windowName)) {
    desk = blank({ pose: "sleeping", lamp: "off", prop: "none", bubble: "officeXauClosed", freshness: "fresh", sourceAt, evidenceKey: "xau.closed", fields, facts });
  } else if (xau.warming === true || state === "warming") {
    desk = blank({ pose: "warming", lamp: "amber", prop: "none", bubble: "officeWarming", freshness: "fresh", sourceAt, evidenceKey: "xau.warming", fields, facts });
  } else if (state === "running" && windowName != null && ACTIVE_WINDOW.has(windowName)) {
    const ageS = num(xau.last_bar_age_s);
    const typing = ageS != null && ageS <= BAR_FRESH_S;
    desk = blank({
      pose: typing ? "typing" : "idle", lamp: typing ? "green" : "amber", prop: "none", bubble: "officeXauLive",
      freshness: "fresh", sourceAt, evidenceKey: typing ? "xau.typing" : "xau.idle", fields, facts,
    });
  } else if (state === "running") {
    desk = blank({ pose: "idle", lamp: "green", prop: "none", bubble: "officeXauLive", freshness: "fresh", sourceAt, evidenceKey: "xau.running", fields, facts });
  } else {
    desk = neutral("unknown", sourceAt, "xau.unknown", fields);
    desk = { ...desk, facts };
  }
  if (ctx.signal && desk.pose !== "unknown" && desk.pose !== "alert" && desk.prop !== "alarm") {
    return { ...desk, prop: "signal_flag", facts: { ...desk.facts, signal: "1" }, evidenceKey: desk.evidenceKey + ".signal" };
  }
  return desk;
}

function forwardDesk(ctx: Shared): DeskState {
  const fields = [field("scored", ctx.scored), field("pending", ctx.pending)].filter((row): row is FieldRow => row != null);
  if (ctx.input.forwardIssue === "unauthorized") return neutral("denied", null, "forward.denied", fields);
  if (ctx.input.forwardIssue) return neutral("unknown", null, "forward.error", fields);
  if (!record(ctx.input.forward)) return neutral("loading", null, "forward.loading", fields);
  if (ctx.scored == null || ctx.pending == null) return neutral("unknown", null, "forward.unknown", fields);
  const facts: Record<string, string> = { pending: String(ctx.pending) };
  if (ctx.scoredDelta) facts.scored = String(ctx.scored);
  if (ctx.scoredDelta) {
    return blank({ pose: "stamping", lamp: "blue", prop: "scorecard", bubble: "officeScored", freshness: "unknown", sourceAt: null, evidenceKey: "forward.delta", fields, facts });
  }
  if (ctx.pending > 0) {
    return blank({
      pose: "idle", lamp: "green", prop: "none", bubble: "officePending", freshness: "unknown", sourceAt: null,
      evidenceKey: "forward.pending", fields, facts, stack: paperStack(ctx.pending),
    });
  }
  return blank({ pose: "coffee", lamp: "green", prop: "none", bubble: "officeForwardClear", freshness: "unknown", sourceAt: null, evidenceKey: "forward.clear", fields, facts });
}

function watcherDesk(ctx: Shared): DeskState {
  const agent = agentOf(ctx.input, "WATCHER");
  const sourceAt = agent?.sourceAt ?? null;
  const failed = ctx.alerts.find((row) => alertFailed(row)) ?? null;
  const fields = ctx.alerts.slice(0, 3).map((row) => ({ name: "alert." + row.id, value: row.state || "unknown" }));
  if (!agent || agent.state === "loading" || agent.state === "awaiting_first_snapshot") return neutral("loading", sourceAt, "watcher.loading", fields);
  if (agent.state === "unauthorized" || ctx.input.alertsIssue === "unauthorized") return neutral("denied", null, "watcher.denied", fields);
  if (agent.state === "stale" || agent.freshness === "stale") return neutral("stale", sourceAt, "watcher.stale", fields);
  if (agent.freshness !== "fresh" || (agent.state !== "confirmed" && agent.state !== "confirmed_empty" && agent.state !== "partial")) {
    return neutral("unknown", sourceAt, "watcher.unknown", fields);
  }
  if (ctx.input.alertsIssue && ctx.alerts.length === 0) return neutral("unknown", sourceAt, "watcher.alerts_error", fields);
  if (failed) {
    const facts: Record<string, string> = {};
    if (failed.event) facts.state = failed.event;
    if (failed.error) facts.last_error = failed.error.length > 80 ? failed.error.slice(0, 80) : failed.error;
    return blank({
      pose: "alert", lamp: "red", prop: "none", bubble: "officeAlertFailed", freshness: "fresh",
      sourceAt: failed.at ?? sourceAt, evidenceKey: "watcher.failed", fields, facts,
    });
  }
  if (!ctx.input.stream) {
    return blank({ pose: "away", lamp: "amber", prop: "none", bubble: "reconnecting", freshness: "fresh", sourceAt, evidenceKey: "watcher.stream", fields });
  }
  if (ctx.phone) {
    const delivered = [...ctx.alerts].reverse().find((row) => PHONE_STATE.has(row.state) && !alertFailed(row));
    return blank({
      pose: "phone", lamp: "blue", prop: "none", bubble: "officePhone", freshness: "fresh",
      sourceAt: delivered?.at ?? sourceAt, evidenceKey: "watcher.phone", fields,
      facts: delivered?.event ? { state: delivered.event } : {},
    });
  }
  return blank({ pose: "idle", lamp: "green", prop: "none", bubble: "officeWatcherIdle", freshness: "fresh", sourceAt, evidenceKey: "watcher.idle", fields });
}
