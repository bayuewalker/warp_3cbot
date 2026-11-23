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

export function publishedCount(observation: ObservedValue<string | null>, value: unknown) {
  return observation.state === "confirmed" && observation.freshness === "fresh" &&
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}
