/**
 * Pure adapters for the Activity timeline and the Risk & Health view.
 *
 * Every item is derived from one real row of one real read-only snapshot. Nothing here
 * invents an id, a time, a severity or an outcome. A missing or unusable value stays null
 * and the view renders it as "Not reported".
 */
import { observe } from "./adapters.ts";
import { record, sourceInstant } from "./frames.ts";
import type { ObservedValue, ReadInput, ReadState } from "./types";

export const ACTIVITY_SOURCES = ["events", "alerts", "journal", "signals", "breakers"] as const;
export type ActivitySource = (typeof ACTIVITY_SOURCES)[number];
export type Severity = "info" | "warn" | "bad";
export type RefKind = "alert_id" | "client_id" | "strategy_id";

export const WORKER_PUBLICATION_TTL_S = 900;
export const EVENTS_TTL_S = 60;
export const STREAM_GRACE_MS = 5000;
export const NO_OUTCOME = "__none__";

export type ActivityItem = {
  source: ActivitySource;
  kind: string;
  /** Source time exactly as reported, only when it is a valid instant not beyond MAX_CLOCK_SKEW_MS. */
  at: string | null;
  atMs: number | null;
  receivedAt: string | null;
  outcome: string | null;
  severity: Severity | null;
  reason: string | null;
  refId: string | null;
  refKind: RefKind | null;
  /** A second real identifier on the same row (a submission's strategy_id). */
  related: { refKind: RefKind; refId: string } | null;
  subject: string | null;
  /** Read state of the source this row came from. */
  state: ReadState;
};

export type SourceSummary = {
  source: ActivitySource;
  state: ReadState;
  freshness: ObservedValue<unknown>["freshness"];
  /** Rows kept after validation and de-duplication. */
  count: number;
  /** Rows the source sent that could not be read. */
  invalid: number;
  sourceAt: string | null;
  reason: string | null;
};

export type ActivityModel = {
  timed: ActivityItem[];
  untimed: ActivityItem[];
  sources: Record<ActivitySource, SourceSummary>;
};

type Row = Record<string, unknown>;
type Draft = Omit<ActivityItem, "source" | "atMs" | "at" | "receivedAt" | "state"> & { rawAt: unknown };

function text(value: unknown) {
  return typeof value === "string" && value.trim().length ? value : null;
}

function ident(value: unknown) {
  if (typeof value === "number" && Number.isSafeInteger(value)) return String(value);
  return text(value);
}

function fields(read: ReadInput): Row {
  return record(read.data) ? read.data : {};
}

function first(...values: unknown[]) {
  for (const value of values) {
    const found = text(value);
    if (found !== null) return found;
  }
  return null;
}

/** Python emits microseconds. Keep ordering stable between rows inside one millisecond. */
function order(value: string, ms: number) {
  const fraction = /\.(\d+)/.exec(value)?.[1] ?? "";
  return ms * 1000 + Number(fraction.padEnd(6, "0").slice(3, 6));
}

const alertSeverity: Record<string, Severity> = { pending: "warn", delivered: "info", resolved: "info" };

const mappers: Record<Exclude<ActivitySource, "breakers">, { field: string; draft: (row: Row) => Draft | null }> = {
  events: {
    field: "events",
    draft: row => {
      const kind = text(row.event);
      if (kind === null) return null;
      const strategy = ident(row.strategy_id);
      return {
        kind, rawAt: row.ts, outcome: null, severity: null,
        reason: first(row.reason, row.error, row.detail),
        refId: strategy, refKind: strategy ? "strategy_id" : null, related: null, subject: text(row.symbol),
      };
    },
  },
  alerts: {
    field: "alerts",
    draft: row => {
      const kind = text(row.event);
      if (kind === null) return null;
      const id = ident(row.id);
      const state = text(row.state);
      return {
        kind, rawAt: row.created_at, outcome: state, severity: state ? alertSeverity[state] ?? null : null,
        reason: text(row.last_error), refId: id, refKind: id ? "alert_id" : null, related: null, subject: null,
      };
    },
  },
  journal: {
    field: "submissions",
    draft: row => {
      const client = ident(row.client_id);
      const strategy = ident(row.strategy_id);
      if (client === null && strategy === null && text(row.symbol) === null && text(row.state) === null) return null;
      return {
        kind: "submission", rawAt: row.created_at, outcome: text(row.state), severity: null, reason: null,
        refId: client, refKind: client ? "client_id" : null,
        related: strategy ? { refKind: "strategy_id", refId: strategy } : null, subject: text(row.symbol),
      };
    },
  },
  signals: {
    field: "decisions",
    draft: row => {
      const stage = text(row.stage);
      if (stage === null) return null;
      return {
        kind: stage, rawAt: row.recorded_at, outcome: text(row.detail), severity: null, reason: text(row.reason),
        refId: null, refKind: null, related: null, subject: text(row.symbol),
      };
    },
  },
};

function breakerDraft(row: Row): Draft | null {
  const halt = typeof row.halt === "boolean" ? row.halt : null;
  const permitted = typeof row.risk_permitted === "boolean" ? row.risk_permitted : null;
  if (halt === null && permitted === null) return null;
  const outcome = halt === true ? "halt" : permitted === false ? "risk_blocked" : permitted === true && halt === false ? "risk_permitted" : null;
  const severity: Severity | null = halt === true ? "bad" : permitted === false ? "warn" : outcome === "risk_permitted" ? "info" : null;
  // /api/breakers carries no timestamp. It is the current gate, not a past event.
  return { kind: "breaker", rawAt: null, outcome, severity, reason: text(row.reason), refId: null, refKind: null, related: null, subject: null };
}

type Ranked = ActivityItem & { rank: number | null; seq: number };

function sourceClock(status: ReadInput, now: number) {
  const published = sourceInstant(fields(status).published_at, now);
  const stale = status.issue !== null ||
    (published !== null && Math.max(0, now - published) > WORKER_PUBLICATION_TTL_S * 1000);
  return { at: published === null ? null : String(fields(status).published_at), stale };
}

function build(source: ActivitySource, read: ReadInput, status: ReadInput, now: number) {
  const body = fields(read);
  let rows: unknown[] | null;
  if (source === "breakers") rows = Object.keys(body).length ? [body] : null;
  else rows = Array.isArray(body[mappers[source].field]) ? (body[mappers[source].field] as unknown[]) : null;
  const receivedAt = read.received?.toISOString() ?? null;
  const ranked: Ranked[] = [];
  let invalid = 0;
  const seen = new Set<string>();
  rows?.forEach((row, index) => {
    const draft = record(row) ? (source === "breakers" ? breakerDraft(row) : mappers[source].draft(row)) : null;
    if (draft === null) {
      invalid += 1;
      return;
    }
    const { rawAt, ...rest } = draft;
    const ms = sourceInstant(rawAt, now);
    const at = ms === null ? null : String(rawAt);
    // Only a real identifier can prove two rows are the same row. Never merge across sources.
    if (rest.refId !== null) {
      const key = `${source}|${rest.refId}|${at}`;
      if (seen.has(key)) return;
      seen.add(key);
    }
    ranked.push({
      ...rest, source, at, atMs: ms, receivedAt, state: "confirmed",
      rank: ms === null ? null : order(String(rawAt), ms), seq: source === "events" ? index : -index,
    });
  });
  const worker = sourceClock(status, now);
  const clock = source === "events"
    ? { at: body.as_of, ttl: EVENTS_TTL_S, stale: false }
    : { at: worker.at, ttl: WORKER_PUBLICATION_TTL_S, stale: worker.stale };
  const usable = rows !== null && !(invalid > 0 && ranked.length === 0 && rows.length > 0);
  const observed = observe(read, usable ? ranked.length : null, {
    source: "worker", ...clock, partial: usable && invalid > 0,
    empty: usable && rows!.length === 0, reason: !usable && Object.keys(body).length ? "malformed_payload" : null,
  }, now);
  for (const item of ranked) item.state = observed.state;
  const summary: SourceSummary = {
    source, state: observed.state, freshness: observed.freshness, count: observed.value === null ? 0 : ranked.length,
    invalid, sourceAt: observed.sourceAt, reason: observed.reason,
  };
  return { ranked: observed.value === null ? [] : ranked, summary };
}

export function activityModel(reads: Record<ActivitySource, ReadInput>, status: ReadInput, now: number): ActivityModel {
  const sources = {} as Record<ActivitySource, SourceSummary>;
  const all: Ranked[] = [];
  for (const source of ACTIVITY_SOURCES) {
    const { ranked, summary } = build(source, reads[source], status, now);
    sources[source] = summary;
    all.push(...ranked);
  }
  const rankOf = (item: Ranked) => ACTIVITY_SOURCES.indexOf(item.source);
  const strip = ({ rank: _rank, seq: _seq, ...item }: Ranked): ActivityItem => item;
  const timed = all.filter(item => item.rank !== null)
    .sort((a, b) => b.rank! - a.rank! || rankOf(a) - rankOf(b) || b.seq - a.seq).map(strip);
  const untimed = all.filter(item => item.rank === null)
    .sort((a, b) => rankOf(a) - rankOf(b) || b.seq - a.seq).map(strip);
  return { timed, untimed, sources };
}

export type Filters = { source: ActivitySource | "all"; kind: string; outcome: string };
export const NO_FILTER: Filters = { source: "all", kind: "all", outcome: "all" };

export function applyFilters(items: ActivityItem[], filters: Filters) {
  return items.filter(item =>
    (filters.source === "all" || item.source === filters.source) &&
    (filters.kind === "all" || item.kind === filters.kind) &&
    (filters.outcome === "all" || (item.outcome ?? NO_OUTCOME) === filters.outcome));
}

function counted(values: string[]): [string, number][] {
  const map = new Map<string, number>();
  for (const value of values) map.set(value, (map.get(value) ?? 0) + 1);
  return [...map].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

/** Counts cover the loaded window only. */
export function facets(items: ActivityItem[]) {
  const sources = Object.fromEntries(ACTIVITY_SOURCES.map(source => [source, 0])) as Record<ActivitySource, number>;
  for (const item of items) sources[item.source] += 1;
  return {
    sources,
    kinds: counted(items.map(item => item.kind)),
    outcomes: counted(items.map(item => item.outcome ?? NO_OUTCOME)),
  };
}

export type StreamView = { connected: boolean; lastFrameAt: number | null; downSince: number | null };
export type ResumeAck = { at: number; publishedAt: string | null };
export type ResumeGate = "none" | "awaiting" | "fresh";

/**
 * A RESUME acknowledgement says the request was accepted. It says nothing about the worker.
 * The gate stays closed until a snapshot published after the ack replaces the one before it.
 */
export function resumeGate(ack: ResumeAck | null, publishedAt: unknown, now: number): ResumeGate {
  if (ack === null) return "none";
  const published = sourceInstant(publishedAt, now);
  return published !== null && published > ack.at && String(publishedAt) !== ack.publishedAt ? "fresh" : "awaiting";
}

let resumeAck: ResumeAck | null = null;
export function recordResumeAck(at: number, publishedAt: string | null) {
  resumeAck = { at, publishedAt };
}
export function currentResumeAck() {
  return resumeAck;
}
export function forgetResumeAck() {
  resumeAck = null;
}

export type PreferencesTab = "venues" | "rules" | "configuration" | "activity" | "risk";
let requestedTab: PreferencesTab | null = null;
/** The incident strip opens the Preferences page on one tab. Nav stays at six rail items. */
export function requestPreferencesTab(tab: PreferencesTab) {
  requestedTab = tab;
}
export function takeRequestedTab(): PreferencesTab | null {
  const tab = requestedTab;
  requestedTab = null;
  return tab;
}

export type IncidentId = "halt" | "risk" | "provider" | "stream" | "statusFailed" | "statusStale" | "xau";
export type Incident = { id: IncidentId; detail: string | null };
export type WorkerState = "loading" | "awaiting" | "failed" | "stale" | "current" | "unknown";
export type HealthLevel = "incident" | "awaiting" | "unverified" | "clear";
export type ProviderView = { name: string; state: string | null; incident: boolean };
export type RiskHealth = {
  level: HealthLevel;
  incidents: Incident[];
  worker: { state: WorkerState; publishedAt: string | null; ageMs: number | null };
  dependenciesHealthy: boolean | null;
  statusHalt: boolean | null;
  providers: ProviderView[];
  breaker: { read: ObservedValue<number> | null; halt: boolean | null; riskPermitted: boolean | null; reason: string | null };
  risk: {
    read: ObservedValue<number> | null; riskPermitted: boolean | null; executionPermitted: boolean | null;
    equity: string | null; equityReliable: boolean | null; reason: string | null;
  };
  stream: { state: "connected" | "fallback" | "disconnected"; lastFrameAt: number | null };
  resume: ResumeGate;
  /** OBSERVE mode blocks entries by design. Neutral, never an incident and never "clear". */
  observeGate: boolean;
};

const OBSERVE_REASONS = ["observe", "observe_without_account"];

function bool(value: unknown) {
  return typeof value === "boolean" ? value : null;
}

function decimal(value: unknown) {
  return typeof value === "string" && /^[+-]?\d+(\.\d+)?$/.test(value) && Number.isFinite(Number(value)) ? value : null;
}

export type RiskReads = { status: ReadInput; breakers?: ReadInput; risk?: ReadInput };

/**
 * One reading of worker, breaker, provider and stream health. The overall level is "clear"
 * only when every input was read, is current and says so. Anything else is an incident,
 * awaiting a fresh snapshot after RESUME, or unverified.
 */
export function riskHealth(reads: RiskReads, stream: StreamView, now: number, resume: ResumeGate = "none"): RiskHealth {
  const { status } = reads;
  const worker = fields(status);
  const published = sourceInstant(worker.published_at, now);
  const ageMs = published === null ? null : Math.max(0, now - published);
  let workerState: WorkerState;
  if (status.issue === "awaiting_first_snapshot") workerState = "awaiting";
  else if (status.issue) workerState = "failed";
  else if (status.data == null) workerState = "loading";
  else if (ageMs === null) workerState = "unknown";
  else workerState = ageMs > WORKER_PUBLICATION_TTL_S * 1000 ? "stale" : "current";

  const clock = sourceClock(status, now);
  const probe = (read: ReadInput | undefined) => read === undefined ? null : observe(read, Object.keys(fields(read)).length ? 1 : null, {
    source: "worker", at: clock.at ?? undefined, ttl: WORKER_PUBLICATION_TTL_S, stale: clock.stale,
  }, now);
  const breakerBody = reads.breakers ? fields(reads.breakers) : {};
  const riskBody = reads.risk ? fields(reads.risk) : {};
  const breakerRead = probe(reads.breakers);
  const riskRead = probe(reads.risk);

  const statusHalt = bool(worker.halt);
  const breakerHalt = bool(breakerBody.halt);
  const statusPermitted = bool(worker.risk_permitted);
  const breakerPermitted = bool(breakerBody.risk_permitted);
  const riskPermitted = bool(riskBody.risk_permitted);

  const providers: ProviderView[] = record(worker.providers) ? Object.entries(worker.providers).map(([name, value]) => {
    const row = record(value) ? value : {};
    const state = text(row.state);
    return { name, state, incident: state === "cooldown" || (state !== null && state !== "ok" && state !== "unconfigured" && state !== "postponed") };
  }) : [];

  const incidents: Incident[] = [];
  const halted = statusHalt === true || breakerHalt === true;
  if (halted) incidents.push({ id: "halt", detail: null });
  const blocked = [[statusPermitted, worker.reason], [breakerPermitted, breakerBody.reason], [riskPermitted, riskBody.reason]]
    .filter(([permitted]) => permitted === false).map(([, reason]) => text(reason))
    .filter(reason => reason !== "spot_postponed");
  const observeGate = !halted && text(worker.mode) === "observe" && blocked.length > 0 &&
    blocked.every(reason => reason !== null && OBSERVE_REASONS.includes(reason));
  if (!halted && blocked.length > 0 && !observeGate) {
    incidents.push({ id: "risk", detail: blocked[0] });
  }
  for (const provider of providers) {
    if (provider.incident) incidents.push({ id: "provider", detail: `${provider.name} · ${provider.state}` });
  }
  const xau = record(worker.xau) ? worker.xau : null;
  const xauState = text(xau?.state);
  const xauReason = text(xau?.ready_reason);
  const xauHalt = record(xau?.halt) ? xau?.halt as { global?: boolean; xau?: boolean } : null;
  if (!halted && (xauHalt?.global === true || xauHalt?.xau === true)) incidents.push({ id: "xau", detail: "halt" });
  else if (xauState === "feed_unavailable" || xauState === "stalled" || xauState === "config_error") incidents.push({ id: "xau", detail: xauState });
  else if (xauState !== "warming" && xauReason?.startsWith("basis_")) incidents.push({ id: "xau", detail: xauReason });
  const streamDown = !stream.connected && stream.downSince !== null && now - stream.downSince >= STREAM_GRACE_MS;
  if (streamDown) incidents.push({ id: "stream", detail: null });
  if (workerState === "failed") incidents.push({ id: "statusFailed", detail: status.issue });
  if (workerState === "stale") incidents.push({ id: "statusStale", detail: null });

  const readable = (read: ObservedValue<number> | null) => read === null || read.state === "confirmed";
  const clear = workerState === "current" && stream.connected && statusHalt === false && statusPermitted === true &&
    (reads.breakers === undefined || (readable(breakerRead) && breakerHalt === false && breakerPermitted === true)) &&
    (reads.risk === undefined || (readable(riskRead) && riskPermitted === true)) &&
    providers.every(provider => provider.state === "ok" || provider.state === "unconfigured" || provider.state === "postponed");
  const level: HealthLevel = incidents.length ? "incident" : resume === "awaiting" ? "awaiting" : clear ? "clear" : "unverified";

  return {
    level, incidents,
    worker: { state: workerState, publishedAt: published === null ? null : String(worker.published_at), ageMs },
    dependenciesHealthy: bool(worker.dependencies_healthy), statusHalt,
    providers,
    breaker: { read: breakerRead, halt: breakerHalt, riskPermitted: breakerPermitted, reason: text(breakerBody.reason) },
    risk: {
      read: riskRead, riskPermitted, executionPermitted: bool(riskBody.execution_permitted),
      equity: decimal(riskBody.equity), equityReliable: bool(riskBody.equity_reliable), reason: text(riskBody.reason),
    },
    stream: {
      state: stream.connected ? "connected" : status.data != null && status.issue === null ? "fallback" : "disconnected",
      lastFrameAt: stream.lastFrameAt,
    },
    resume, observeGate,
  };
}
