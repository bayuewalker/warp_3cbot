import { record, sourceInstant } from "./frames.ts";
import { EVENT_WINDOW_MS, currentPublication, sweep, type Station } from "./room.ts";
import type { ObservedValue } from "./types.ts";

/** Moods drawn on the classic room. There is no XAU seat on this art. */
export type Mood = "working" | "idle" | "alert" | "sleep";

export type LifeCue = {
  mood: Mood;
  bubble: string | null;
  time: string | null;
  stamp: boolean;
  radar: boolean;
};

export type RoomLife = {
  halt: boolean;
  stations: Record<Station, LifeCue>;
};

export type ScanMemory = { key: string | null; until: number };

export type LifeStatus = {
  halt?: boolean;
  reason?: string;
  risk_permitted?: boolean;
  mode?: string;
  published_at?: string | null;
  pipeline?: {
    screening?: { state?: string; source?: string; count?: number };
    risk?: { state?: string; reason?: string };
  };
} | null;

const ROLE_EVENT: Record<string, Station> = {
  market_data_blocked: "SCAN", research_blocked: "ANALYST", llm_provider_unhealthy: "ANALYST",
  halt: "AEGIS", entries_paused: "AEGIS", observe: "EXECUTOR", observe_candidate: "EXECUTOR",
  duplicate_skipped: "EXECUTOR", strategy_confirmed: "EXECUTOR", alert_failed: "WATCHER",
  cycle_blocked: "WATCHER",
};

const ALERT_EVENT = new Set([
  "market_data_blocked", "research_blocked", "llm_provider_unhealthy", "halt", "entries_paused",
  "alert_failed", "cycle_blocked",
]);

const SLEEP_STATE = new Set(["stale", "unavailable", "error", "awaiting_first_snapshot"]);

const EVENT_COPY: Record<string, string> = {
  market_data_blocked: "Market data blocked",
  research_blocked: "Research blocked",
  llm_provider_unhealthy: "Provider unhealthy",
  halt: "Halted",
  entries_paused: "Entries paused",
  observe: "Observing",
  observe_candidate: "Candidate observed",
  duplicate_skipped: "Duplicate skipped",
  strategy_confirmed: "Strategy confirmed",
  alert_failed: "Alert failed",
  cycle_blocked: "Cycle blocked",
  blocked: "Blocked",
};
const ROLES: Station[] = ["SCAN", "ANALYST", "AEGIS", "EXECUTOR", "PORTFOLIO", "WATCHER"];

export function clipBubble(text: string, limit = 28) {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= limit) return clean;
  return clean.slice(0, Math.max(0, limit - 1)).trimEnd() + "…";
}

export function wibClock(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(date) + " WIB";
}

/** Width of a used/cap bar. 15 samples against a cap of 10 stays at 100. */
export function clampPercent(used: number, cap: number) {
  if (!Number.isFinite(used) || !Number.isFinite(cap) || cap <= 0) return 0;
  return Math.min(100, Math.max(0, (used / cap) * 100));
}

export function alivePlayback(flags: {
  reduced: boolean; hidden: boolean; offscreen: boolean; panel: boolean; text: boolean; motion: boolean;
}) {
  return flags.motion && !flags.reduced && !flags.hidden && !flags.offscreen && !flags.panel && !flags.text;
}

export function screeningKey(status: LifeStatus) {
  const screening = status?.pipeline?.screening;
  if (!screening || typeof screening.state !== "string" || !screening.state) return null;
  const count = typeof screening.count === "number" && Number.isFinite(screening.count) ? String(screening.count) : "";
  const source = typeof screening.source === "string" ? screening.source : "";
  return screening.state + "|" + count + "|" + source;
}

/** A radar starts only when a ready screening key replaces a different key. The first sample does not sweep. */
export function nextScan(prior: ScanMemory | null, key: string | null, now: number): ScanMemory & { radar: boolean } {
  if (key == null) return { key: null, until: 0, radar: false };
  const ready = key.split("|")[0] === "ready";
  if (prior == null || prior.key == null) return { key, until: 0, radar: false };
  if (key !== prior.key) return ready ? { key, until: now + EVENT_WINDOW_MS, radar: true } : { key, until: 0, radar: false };
  return { key, until: prior.until, radar: ready && now < prior.until };
}

function named(value: unknown) {
  return typeof value === "string" && value.trim() !== "" && value.trim().toLowerCase() !== "ok" ? value.trim() : null;
}

function human(text: string) {
  return EVENT_COPY[text] ?? text;
}

function sentence(row: Record<string, unknown>) {
  const alert = row.event === "alert_failed" || row.event === "cycle_blocked" ? named(row.alert_event) : null;
  return human(named(row.reason) ?? alert ?? String(row.event));
}

function asleep(item: ObservedValue<string | null> | undefined) {
  if (!item) return true;
  return item.freshness === "stale" || SLEEP_STATE.has(item.state);
}

function latest(role: Station, events: unknown, now: number) {
  if (!Array.isArray(events)) return null;
  let best: { event: string; ts: string; time: number; row: Record<string, unknown> } | null = null;
  for (const row of events) {
    if (!record(row) || typeof row.event !== "string" || typeof row.ts !== "string") continue;
    if (ROLE_EVENT[row.event] !== role) continue;
    const time = sourceInstant(row.ts, now);
    if (time === null || Math.max(0, now - time) > EVENT_WINDOW_MS) continue;
    if (!best || time >= best.time) best = { event: row.event, ts: row.ts, time, row };
  }
  return best;
}

function cue(mood: Mood, bubble: string | null, time: string | null, stamp = false): LifeCue {
  return { mood, bubble, time, stamp, radar: false };
}

function xauText(value: unknown) {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

function xauRoom(input: { status: LifeStatus; events: unknown; now: number }): RoomLife {
  const status = input.status as (LifeStatus & { xau?: Record<string, unknown> }) | null;
  const xau = status?.xau && typeof status.xau === "object" ? status.xau : null;
  const at = xauText(xau?.spot_at) ?? xauText(xau?.heartbeat_at) ?? status?.published_at ?? null;
  const clock = at ? wibClock(at) : null;
  const state = xauText(xau?.state);
  const feed = xauText(xau?.feed);
  const engine = xauText(xau?.engine);
  const windowName = xau && typeof xau.window === "object" && xau.window ? xauText((xau.window as { name?: string }).name) : null;
  const ready = xau?.ready === true;
  const reason = xauText(xau?.ready_reason);
  const open = xau && typeof xau.open_signal === "object" && xau.open_signal
    ? xauText((xau.open_signal as { direction?: string }).direction) : null;
  const today = xau && typeof xau.today === "object" && xau.today
    ? xauText(String((xau.today as { realized_r?: string }).realized_r ?? "")) : null;
  const event = xauEvent(input.events, input.now);
  const stations = {
    SCAN: cue(feed ? "working" : "sleep", feed ? clipBubble(feed) : "unknown", clock),
    ANALYST: cue(engine ? "working" : "idle", clipBubble([engine, windowName].filter(Boolean).join(" ") || "unknown"), clock),
    AEGIS: cue(ready ? "working" : state === "feed_unavailable" || reason ? "alert" : "idle", clipBubble(reason ?? state ?? "unknown"), clock),
    EXECUTOR: cue(open ? "working" : "idle", clipBubble(open ? "XAU " + open : "signal only"), clock, Boolean(open)),
    PORTFOLIO: cue(today ? "working" : "idle", today ? clipBubble(today + "R") : null, clock),
    WATCHER: event
      ? cue("working", clipBubble(event.event), wibClock(event.ts))
      : cue(state ? "idle" : "sleep", state ? clipBubble("XAU " + state) : "unknown", clock),
  } as Record<Station, LifeCue>;
  return { halt: status?.halt === true, stations };
}

function xauEvent(events: unknown, now: number) {
  if (!Array.isArray(events)) return null;
  let best: { event: string; ts: string; time: number } | null = null;
  for (const row of events) {
    if (!record(row) || typeof row.event !== "string" || !row.event.startsWith("XAU ") || typeof row.ts !== "string") continue;
    const time = sourceInstant(row.ts, now);
    if (time === null || Math.max(0, now - time) > EVENT_WINDOW_MS) continue;
    if (!best || time >= best.time) best = { event: row.event, ts: row.ts, time };
  }
  return best;
}

export function roomLife(input: {
  agents: Partial<Record<Station, ObservedValue<string | null>>>;
  status: LifeStatus;
  events: unknown;
  now: number;
  radar: boolean;
}): RoomLife {
  const lanes = input.status as (LifeStatus & { lanes?: { primary?: string } }) | null;
  if (lanes?.lanes?.primary === "xau") return xauRoom(input);
  const { status, now } = input;
  const published = currentPublication(status?.published_at, now);
  const halt = published && status?.halt === true;
  const risk = status?.pipeline?.risk;
  const blocked = published && (status?.risk_permitted === false || risk?.state === "blocked");
  const stations = {} as Record<Station, LifeCue>;
  for (const role of ROLES) {
    const event = latest(role, input.events, now);
    if (event && ALERT_EVENT.has(event.event)) {
      stations[role] = cue("alert", clipBubble(sentence(event.row)), wibClock(event.ts));
    } else if (event) {
      const stamp = role === "EXECUTOR" && event.event === "strategy_confirmed" &&
        sweep("EXECUTOR", { event: event.event, ts: event.ts, traceId: null }, status?.mode, now);
      stations[role] = cue("working", clipBubble(sentence(event.row)), wibClock(event.ts), stamp);
    } else if (role === "AEGIS" && (halt || blocked)) {
      const text = named(status?.reason) ?? named(risk?.reason) ?? (halt ? "halt" : "blocked");
      stations[role] = cue("alert", clipBubble(human(text)), status?.published_at ? wibClock(status.published_at) : null);
    } else if (asleep(input.agents[role])) {
      stations[role] = cue("sleep", "zzz", null);
    } else {
      stations[role] = cue("idle", null, null);
    }
    if (role === "SCAN" && input.radar && (stations[role].mood === "idle" || stations[role].mood === "working")) {
      stations[role].radar = true;
    }
  }
  return { halt, stations };
}
