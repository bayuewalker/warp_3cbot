/**
 * Cues taken only from officeState. The render clock is not an input.
 */
import { record } from "../frames.ts";
import { EVENT_WINDOW_MS } from "../room.ts";
import { DESKS, type DeskId, type DeskState } from "./office-state.ts";

export const CHIEF: DeskId = "EXECUTOR";

/** Seat pixels in the live-office bitmap. The chief seat is the delivery target. */
export const SEAT: Record<DeskId, { x: number; y: number }> = {
  SCAN: { x: 22, y: 104 },
  ANALYST: { x: 74, y: 104 },
  EXECUTOR: { x: 132, y: 100 },
  PORTFOLIO: { x: 22, y: 154 },
  XAU: { x: 74, y: 154 },
  WATCHER: { x: 132, y: 154 },
  AEGIS: { x: 22, y: 204 },
  FORWARD: { x: 132, y: 204 },
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

/** A real number or token, or a dash. Never a guessed metric. */
export function cardValue(id: DeskId, desk: DeskState, status: Record<string, unknown> | null, pending: number | null, alertCount: number | null): string {
  if (id === "FORWARD") return pending == null ? "—" : String(pending);
  if (desk.freshness === "stale" || desk.pose === "unknown") return "—";
  if (id === "SCAN") {
    const pipeline = status && record(status.pipeline) ? status.pipeline : null;
    const screening = pipeline && record(pipeline.screening) ? pipeline.screening : null;
    const count = screening && typeof screening.count === "number" && Number.isFinite(screening.count) ? screening.count : null;
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
  if (id === "WATCHER") return alertCount == null ? "—" : String(alertCount);
  return "—";
}
