/**
 * Cues taken only from officeState. The render clock is not an input.
 */
import { record } from "../frames.ts";
import { EVENT_WINDOW_MS } from "../room.ts";
import { alertFailed, BASIS_READY, DESKS, type DeskId, type DeskState } from "./office-state.ts";

export const CHIEF: DeskId = "EXECUTOR";

/** Sprite origin in the live-office bitmap. The chief seat is the delivery target. */
export const SEAT: Record<DeskId, { x: number; y: number }> = {
  SCAN: { x: 44, y: 184 },
  ANALYST: { x: 148, y: 184 },
  EXECUTOR: { x: 250, y: 176 },
  PORTFOLIO: { x: 44, y: 286 },
  XAU: { x: 148, y: 286 },
  WATCHER: { x: 250, y: 286 },
  AEGIS: { x: 52, y: 386 },
  FORWARD: { x: 210, y: 386 },
};

export type DeliveryKind = "signal" | "proposal" | "order";
export type Delivery = { from: DeskId; kind: DeliveryKind; sourceAt: string | null };

export type OfficeCues = {
  phone: boolean;
  alarm: boolean;
  sleep: boolean;
  stamp: boolean;
  typing: DeskId[];
  deliveries: Delivery[];
};

export function officeCues(desks: Record<DeskId, DeskState>): OfficeCues {
  const deliveries: Delivery[] = [];
  if (desks.XAU.prop === "signal_flag") deliveries.push({ from: "XAU", kind: "signal", sourceAt: desks.XAU.sourceAt });
  if (desks.EXECUTOR.prop === "paper_ticket_observe") deliveries.push({ from: "EXECUTOR", kind: "proposal", sourceAt: desks.EXECUTOR.sourceAt });
  if (desks.EXECUTOR.prop === "order_flag") deliveries.push({ from: "EXECUTOR", kind: "order", sourceAt: desks.EXECUTOR.sourceAt });
  return {
    phone: desks.WATCHER.pose === "phone",
    alarm: desks.AEGIS.pose === "alert" || desks.AEGIS.prop === "alarm",
    sleep: desks.XAU.pose === "sleeping",
    stamp: desks.FORWARD.pose === "stamping",
    typing: DESKS.filter((id) => desks[id].pose === "typing" || desks[id].pose === "thinking"),
    deliveries,
  };
}

/** 0 at the event time, 1 at the end of the event window. Missing time stays put. */
export function deliveryProgress(sourceAt: string | null, now: number): number {
  const time = sourceAt ? Date.parse(sourceAt) : Number.NaN;
  if (!Number.isFinite(time)) return 1;
  return Math.min(1, Math.max(0, (now - time) / EVENT_WINDOW_MS));
}

function smooth(progress: number): number {
  const t = Math.min(1, Math.max(0, progress));
  return t * t * (3 - 2 * t);
}

/** Tile path from a seat to the chief seat. The chief does not walk to itself. */
export function walkPosition(from: DeskId, progress: number): { x: number; y: number } {
  const start = SEAT[from];
  const end = SEAT[CHIEF];
  if (from === CHIEF) return { x: end.x, y: end.y };
  const t = smooth(progress);
  if (t < 0.5) {
    const u = t * 2;
    return { x: start.x + (end.x - start.x) * u, y: start.y };
  }
  const u = (t - 0.5) * 2;
  return { x: end.x, y: start.y + (end.y - start.y) * u };
}

function fieldValue(desk: DeskState, name: string): string | null {
  const row = desk.fields.find((item) => item.name === name);
  if (!row || row.value === "null" || row.value === "undefined") return null;
  return row.value;
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export type WatcherCardRow = { id: number; state: string; event: string; error: string | null; attempts: number | null };

/**
 * Failed-alert count when any row failed, otherwise the newest event, otherwise a dash.
 * The length of the alerts window is not a card value.
 */
export function watcherCardValue(rows: WatcherCardRow[] | null): string {
  if (!rows) return "—";
  const failed = rows.filter((row) => alertFailed(row)).length;
  if (failed > 0) return String(failed);
  let newest: WatcherCardRow | null = null;
  for (const row of rows) if (!newest || row.id > newest.id) newest = row;
  return newest?.event ? newest.event : "—";
}

/** Both ends of a real ratio, or null. The bar is omitted when either number is missing. */
export function cardRatio(id: DeskId, desk: DeskState, status: Record<string, unknown> | null): { used: number; cap: number } | null {
  if (desk.freshness === "stale" || desk.pose === "unknown") return null;
  if (id === "ANALYST") {
    const providers = status && record(status.providers) ? status.providers : null;
    const budget = providers && record(providers.budget) ? providers.budget : null;
    const used = finite(budget?.calls_used);
    const cap = finite(budget?.calls_cap);
    if (used == null || cap == null || cap <= 0) return null;
    return { used, cap };
  }
  if (id === "XAU") {
    const xau = status && record(status.xau) ? status.xau : null;
    const samples = finite(xau?.basis_samples);
    if (samples == null) return null;
    return { used: samples, cap: BASIS_READY };
  }
  return null;
}

/** A real number or token, or a dash. Never a guessed metric. */
export function cardValue(id: DeskId, desk: DeskState, status: Record<string, unknown> | null, pending: number | null, alerts: WatcherCardRow[] | null): string {
  if (id === "FORWARD") return pending == null ? "—" : String(pending);
  if (desk.freshness === "stale" || desk.pose === "unknown") return "—";
  if (id === "SCAN") {
    const pipeline = status && record(status.pipeline) ? status.pipeline : null;
    const screening = pipeline && record(pipeline.screening) ? pipeline.screening : null;
    const count = finite(screening?.count);
    return count == null ? "—" : String(count);
  }
  if (id === "ANALYST") return fieldValue(desk, "state") ?? "—";
  if (id === "AEGIS") {
    if (fieldValue(desk, "halt") === "true") return "HALT";
    const reason = fieldValue(desk, "reason");
    return reason ?? "—";
  }
  if (id === "EXECUTOR") return fieldValue(desk, "mode") ?? "—";
  if (id === "XAU") return desk.facts.window ?? desk.facts.state ?? "—";
  if (id === "WATCHER") return watcherCardValue(alerts);
  if (id === "PORTFOLIO") return fieldValue(desk, "value") ?? fieldValue(desk, "state") ?? "—";
  return "—";
}
