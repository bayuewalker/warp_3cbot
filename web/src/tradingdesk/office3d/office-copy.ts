import { t, type Lang } from "../../i18n.ts";
import { BASIS_READY, type DeskId, type DeskState } from "./office-state.ts";

export const DESK_ROUTE: Record<DeskId, string> = {
  SCAN: "/api/status",
  ANALYST: "/api/status",
  AEGIS: "/api/status",
  EXECUTOR: "/api/status",
  PORTFOLIO: "/api/accounts",
  XAU: "/api/status",
  FORWARD: "/api/forward",
  WATCHER: "/api/alerts",
};

const ROLE = {
  SCAN: "scanRole",
  ANALYST: "analystRole",
  AEGIS: "aegisRole",
  EXECUTOR: "executorRole",
  PORTFOLIO: "portfolioRole",
  WATCHER: "watcherRole",
  XAU: "officeXauRole",
  FORWARD: "officeScorerRole",
} as const;

export function deskLabel(lang: Lang, id: DeskId) {
  return t(lang, ROLE[id]);
}

export function fmtWib(value: string | null, lang: Lang): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const clock = new Intl.DateTimeFormat(lang === "id" ? "id-ID" : "en-GB", {
    timeZone: "Asia/Jakarta",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(date);
  return clock + " WIB";
}

/** Bubble copy is the mapping sentence plus only the facts that sentence names. */
export function bubbleText(lang: Lang, id: DeskId, desk: DeskState) {
  const parts = [t(lang, desk.bubble)];
  const facts = desk.facts;
  if (desk.bubble === "officeResearchBudget" && facts.calls_used && facts.calls_cap) {
    parts.push(facts.calls_used + "/" + facts.calls_cap);
  }
  if (facts.error_code) parts.push(facts.error_code);
  if (facts.next_attempt_at) {
    const when = fmtWib(facts.next_attempt_at, lang);
    if (when) parts.push(when);
  }
  if (desk.bubble === "officeXauLive" || desk.bubble === "officeXauClosed") {
    if (facts.window) parts.push(facts.window);
    if (facts.engine) parts.push(facts.engine);
  }
  if (desk.bubble === "officeWarming" && facts.basis_samples) parts.push(facts.basis_samples + "/" + BASIS_READY);
  if (facts.pending) parts.push(facts.pending);
  if (facts.scored) parts.push(facts.scored);
  if (facts.state && (desk.bubble === "officeXauBad" || desk.bubble === "officeAlertFailed" || desk.bubble === "officePhone")) {
    parts.push(facts.state);
  }
  if (facts.last_error) parts.push(facts.last_error);
  if (facts.reason) parts.push(facts.reason);
  if (facts.signal) parts.push(t(lang, "officeXauSignal"));
  if (facts.duplicate) parts.push(t(lang, "officeDuplicate"));
  const route = facts.route ?? DESK_ROUTE[id];
  const sourceAt = fmtWib(desk.sourceAt, lang);
  return parts.join(" · ") + "\n" + route + (sourceAt ? " · " + sourceAt : "");
}

export function eventLines(events: unknown): { ts: string; label: string }[] {
  if (!Array.isArray(events)) return [];
  const lines: { ts: string; label: string }[] = [];
  for (const row of events) {
    if (row == null || typeof row !== "object" || Array.isArray(row)) continue;
    const record = row as Record<string, unknown>;
    if (typeof record.event !== "string") continue;
    const bits = [record.event];
    for (const key of ["symbol", "reason", "error", "detail"]) {
      const value = record[key];
      if (typeof value === "string" && value) bits.push(value.slice(0, 60));
    }
    lines.push({ ts: typeof record.ts === "string" ? record.ts : "", label: bits.join(" ") });
  }
  return lines.reverse();
}
