import { record, sourceInstant } from "./frames.ts";
import type { ObservedValue } from "./types";

export const stations = {
  SCAN: { title: "scanRole", source: "/api/status", route: "/api/screener", list: "hits", fields: ["symbol", "recommendation"] },
  ANALYST: { title: "analystRole", source: "/api/status", route: "/api/opportunities", list: "cards", fields: ["symbol", "model_action"] },
  AEGIS: { title: "aegisRole", source: "/api/status", route: "/api/decisions", list: "decisions", fields: ["symbol", "status"] },
  EXECUTOR: { title: "executorRole", source: "/api/status", route: "/api/journal", list: "submissions", fields: ["symbol", "state"] },
  PORTFOLIO: { title: "portfolioRole", source: "/api/accounts + /api/account-positions", route: "/api/account-positions", list: "positions", fields: ["pair", "kind"] },
  WATCHER: { title: "watcherRole", source: "/api/events", route: "/api/alerts", list: "alerts", fields: ["event", "state"] },
} as const;
export type Station = keyof typeof stations;
export const stationNames = Object.keys(stations) as Station[];

type RoleEvent = { event: string; ts: string; traceId: string | null };
const eventKind: Record<string, [Station, "success" | "failure" | "event"]> = {
  market_data_blocked: ["SCAN", "failure"], research_blocked: ["ANALYST", "failure"],
  llm_provider_unhealthy: ["ANALYST", "failure"], halt: ["AEGIS", "event"], entries_paused: ["AEGIS", "event"],
  observe: ["EXECUTOR", "event"], observe_candidate: ["EXECUTOR", "event"], duplicate_skipped: ["EXECUTOR", "event"],
  strategy_confirmed: ["EXECUTOR", "success"], alert_failed: ["WATCHER", "failure"], cycle_blocked: ["WATCHER", "failure"],
};

export function roleHistory(role: Station, rows: unknown, now: number) {
  const history: Record<"latest" | "success" | "failure", RoleEvent | null> = { latest: null, success: null, failure: null };
  if (!Array.isArray(rows)) return history;
  for (const row of rows) {
    if (!record(row) || typeof row.event !== "string" || typeof row.ts !== "string") continue;
    const time = sourceInstant(row.ts, now);
    if (time === null) continue;
    const kind = Object.hasOwn(eventKind, row.event) ? eventKind[row.event] : null;
    if (role !== "WATCHER" && kind?.[0] !== role) continue;
    const entry = { event: row.event, ts: row.ts, traceId: typeof row.trace_id === "string" ? row.trace_id : null };
    if (!history.latest || time >= Date.parse(history.latest.ts)) history.latest = entry;
    if (kind?.[0] === role && kind[1] !== "event") {
      const previous = history[kind[1]];
      if (!previous || time >= Date.parse(previous.ts)) history[kind[1]] = entry;
    }
  }
  return history;
}

const ambientValue: Record<Station, string> = {
  SCAN: "ready", ANALYST: "ready", AEGIS: "pass", EXECUTOR: "permitted", PORTFOLIO: "reported", WATCHER: "reported",
};

export const PUBLICATION_TTL_MS = 900_000;
export const EVENT_WINDOW_MS = 12_000;

export function currentPublication(stamp: unknown, now: number) {
  const time = sourceInstant(stamp, now);
  return time !== null && Math.max(0, now - time) <= PUBLICATION_TTL_MS;
}

export function ambient(role: Station, item: ObservedValue<string | null>, stream: boolean, publicationFresh: boolean) {
  return stream && publicationFresh && item.state === "confirmed" && item.freshness === "fresh" &&
    item.value === ambientValue[role];
}

/** Use the latest mapped event, never an older success superseded by a skip or a failure. */
export function sweep(role: Station, event: RoleEvent | null, mode: string | undefined, now: number) {
  if (!event) return false;
  const time = sourceInstant(event.ts, now);
  if (time === null || Math.max(0, now - time) > EVENT_WINDOW_MS) return false;
  if (role === "WATCHER") return true;
  const kind = Object.hasOwn(eventKind, event.event) ? eventKind[event.event] : null;
  if (!kind || kind[0] !== role) return false;
  if (role === "EXECUTOR") return kind[1] === "success" && (mode === "paper" || mode === "live");
  return kind[1] !== "failure";
}

export function publishedCount(observation: ObservedValue<string | null>, value: unknown) {
  return observation.state === "confirmed" && observation.freshness === "fresh" &&
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

export type LaneStatus = {
  published_at?: string | null;
  lanes?: { primary?: string; spot?: { state?: string; flag?: string; since?: string } };
  xau?: {
    enabled?: boolean;
    state?: string;
    ready?: boolean;
    ready_reason?: string;
    feed?: string | null;
    basis?: string | null;
    basis_std?: string | null;
    spot_last?: string | null;
    spot_at?: string | null;
    cycle_seconds?: number;
    window?: { name?: string; start_wib?: string };
    engine?: string | null;
    next_window?: { name?: string; start_wib?: string } | null;
    open_signal?: {
      direction?: string; entry?: string; sl?: string; tp1?: string; tp2?: string | null;
      grade?: string; valid_until?: string | null;
    } | null;
    today?: { signals_sent?: number; journal_only?: number; realized_r?: string };
    funnel?: { recent?: { reason?: string; wib?: string }[] };
    last_signal_at?: string | null;
    heartbeat_at?: string | null;
  } | null;
};

export function xauPrimary(status: LaneStatus | null | undefined) {
  return status?.lanes?.primary === "xau";
}

export function spotPostponed(status: LaneStatus | null | undefined) {
  return status?.lanes?.spot?.state === "postponed";
}

function known(value: unknown) {
  return value == null || value === "" ? "unknown" : String(value);
}

export function xauCard(status: LaneStatus | null | undefined) {
  const xau = status?.xau;
  if (!xau || typeof xau !== "object") {
    return { state: "unknown", reason: "missing", ready: false, window: "unknown", engine: "unknown",
      next: "unknown", spot: "unknown", spotAt: null, basis: "unknown", signal: "unknown",
      today: "unknown", funnel: "unknown", source: "OANDA:XAUUSD via TradingView (spot)" };
  }
  const open = xau.open_signal;
  const signal = open
    ? [open.direction, open.entry, open.sl, open.tp1, open.tp2, open.grade, open.valid_until].map(known).join(" ")
    : "unknown";
  const today = xau.today
    ? `${known(xau.today.signals_sent)} ${known(xau.today.journal_only)} ${known(xau.today.realized_r)}`
    : "unknown";
  const funnel = (xau.funnel?.recent ?? []).map(row => known(row.reason)).filter(reason => reason !== "unknown").slice(0, 5).join(", ");
  return {
    state: known(xau.state),
    reason: known(xau.ready_reason),
    ready: xau.ready === true,
    window: known(xau.window?.name),
    engine: known(xau.engine),
    next: xau.next_window?.name ? `${xau.next_window.name} ${known(xau.next_window.start_wib)}` : "unknown",
    spot: known(xau.spot_last),
    spotAt: xau.spot_at ?? null,
    basis: known(xau.basis),
    signal,
    today,
    funnel: funnel || "unknown",
    source: "OANDA:XAUUSD via TradingView (spot)",
  };
}

function seat(value: string, at: string | null, fresh: boolean): ObservedValue<string | null> {
  const missing = value === "unknown";
  return {
    value: missing ? null : value,
    availability: missing ? "unavailable" : "confirmed",
    freshness: missing ? "unknown" : fresh ? "fresh" : "stale",
    state: missing ? "unavailable" : "confirmed",
    source: "worker",
    sourceAt: at,
    receivedAt: at,
    reason: missing ? "unknown" : null,
    traceId: null,
  };
}

export function xauAgents(status: LaneStatus | null | undefined, now: number): Record<Station, ObservedValue<string | null>> {
  const card = xauCard(status);
  const at = status?.xau?.spot_at ?? status?.xau?.heartbeat_at ?? status?.published_at ?? null;
  const time = at ? Date.parse(at) : NaN;
  const cycle = status?.xau?.cycle_seconds;
  const limit = (typeof cycle === "number" && cycle > 0 ? cycle : 30) * 2 * 1000;
  const fresh = Number.isFinite(time) && Math.max(0, now - time) <= limit;
  return {
    SCAN: seat(card.spot, at, fresh),
    ANALYST: seat(card.engine + " " + card.window, at, fresh),
    AEGIS: seat(card.ready ? "pass" : card.reason, at, fresh),
    EXECUTOR: seat(card.signal === "unknown" ? "signal only" : card.signal, at, fresh),
    PORTFOLIO: seat(card.today, at, fresh),
    WATCHER: seat(known(status?.xau?.last_signal_at ?? status?.xau?.state), at, fresh),
  };
}
