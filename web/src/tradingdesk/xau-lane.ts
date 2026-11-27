import type { Lang } from "../i18n";
import type { ObservedValue } from "./types";

type XauStatus = {
  lanes?: { primary?: string };
  published_at?: string | null;
  xau?: {
    state?: string;
    ready?: boolean;
    ready_reason?: string | null;
    feed?: string | null;
    basis?: string | null;
    basis_samples?: number | null;
    spot_last?: string | null;
    spot_at?: string | null;
    spot_state?: string | null;
    cycle_seconds?: number;
    window?: { name?: string };
    engine?: string | null;
    next_window?: { name?: string; start_wib?: string } | null;
    open_signal?: { direction?: string; entry?: string; sl?: string; tp1?: string; tp2?: string | null; grade?: string } | null;
    today?: { signals_sent?: number; journal_only?: number; realized_r?: string };
    funnel?: { recent?: { reason?: string; count?: number; wib?: string }[] };
    last_signal_at?: string | null;
    heartbeat_at?: string | null;
  } | null;
} | null | undefined;

export type StationName = "SCAN" | "ANALYST" | "AEGIS" | "EXECUTOR" | "PORTFOLIO" | "WATCHER";

const text = (value: unknown) => value == null || value === "" ? "unknown" : String(value);

const REASONS: Record<string, [string, string]> = {
  warming_up: ["Warming up", "Pemanasan"],
  warming: ["Warming up", "Pemanasan"],
  market_closed: ["Market closed", "Pasar tutup"],
  dirty_basis: ["Basis not ready", "Basis belum siap"],
  dirty_gaps: ["Candle gaps", "Celah candle"],
  stale_m5: ["Stale 5-minute candles", "Candle 5 menit basi"],
  news_block: ["News block", "Blokir berita"],
  halt: ["Halted", "Dihentikan"],
  no_direction: ["No direction", "Tanpa arah"],
  engine_none: ["No engine", "Tanpa mesin"],
  entry_closed: ["Entry window closed", "Jendela entri tutup"],
  no_setup: ["No setup", "Tanpa setup"],
  feed_unavailable: ["Feed unavailable", "Feed tidak tersedia"],
  stalled: ["Feed stalled", "Feed macet"],
  config_error: ["Configuration error", "Kesalahan konfigurasi"],
  few_samples: ["Warming up", "Pemanasan"],
  basis_wide: ["Basis too wide", "Basis terlalu lebar"],
  basis_noisy: ["Basis too noisy", "Basis terlalu berisik"],
  ok: ["Ready", "Siap"],
};

export function xauLabels(lang: Lang) {
  const id = lang === "id";
  return {
    desk: "XAUUSD",
    source: "OANDA:XAUUSD via TradingView (spot)",
    unavailable: id ? "XAUUSD tidak tersedia" : "XAUUSD unavailable",
    next: id ? "Jendela berikut" : "Next window",
    today: id ? "Hari ini" : "Today",
    funnel: id ? "Tanpa sinyal" : "No signal",
    closed: id ? "Pasar tutup" : "Market closed",
    last: id ? "terakhir" : "last",
    sent: id ? "Terkirim" : "Sent",
    journal: id ? "Jurnal" : "Journal",
    none: id ? "Tidak ada sinyal terbuka" : "No open signal",
    basis: "Basis",
  };
}

export function xauReason(lang: Lang, code: string) {
  const row = REASONS[code];
  if (row) return row[lang === "id" ? 1 : 0];
  if (code.startsWith("gate_fail:")) return (lang === "id" ? "Gerbang " : "Gate ") + code.slice("gate_fail:".length);
  if (code.startsWith("risk_block:")) return (lang === "id" ? "Risiko " : "Risk ") + code.slice("risk_block:".length);
  return code.replaceAll("_", " ");
}

export function xauCard(status: XauStatus) {
  const xau = status?.xau;
  if (!xau || typeof xau !== "object") {
    return {
      state: "unknown", reason: "missing", ready: false, window: "unknown", engine: "unknown",
      next: "unknown", spot: "unknown", spotAt: null as string | null, spotState: "unavailable",
      basis: "unknown", basisSamples: null as number | null, signal: "unknown", open: false,
      sent: "unknown", journal: "unknown", r: "unknown",
      funnel: [] as { reason: string; count: number }[],
      updated: null as string | null,
    };
  }
  const open = xau.open_signal;
  const samples = typeof xau.basis_samples === "number" && Number.isFinite(xau.basis_samples) ? xau.basis_samples : null;
  return {
    state: text(xau.state),
    reason: text(xau.ready_reason),
    ready: xau.ready === true,
    window: text(xau.window?.name),
    engine: text(xau.engine),
    next: xau.next_window?.name ? `${xau.next_window.name} ${text(xau.next_window.start_wib)}` : "unknown",
    spot: text(xau.spot_last),
    spotAt: xau.spot_at ?? null,
    spotState: xau.spot_state ?? (xau.spot_last ? "live" : "unavailable"),
    basis: text(xau.basis),
    basisSamples: samples,
    signal: open ? [open.direction, open.entry, open.sl, open.tp1, open.grade].map(text).join(" ") : "unknown",
    open: Boolean(open),
    sent: text(xau.today?.signals_sent),
    journal: text(xau.today?.journal_only),
    r: text(xau.today?.realized_r),
    funnel: (xau.funnel?.recent ?? []).flatMap(row => {
      const reason = typeof row.reason === "string" ? row.reason : "";
      if (!reason) return [];
      const count = typeof row.count === "number" && row.count > 0 ? row.count : 1;
      return [{ reason, count }];
    }).slice(0, 5),
    updated: xau.spot_at ?? xau.heartbeat_at ?? status?.published_at ?? null,
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

export function xauAgents(status: XauStatus, now: number): Record<StationName, ObservedValue<string | null>> {
  const card = xauCard(status);
  const at = card.updated;
  const time = at ? Date.parse(at) : NaN;
  const cycle = status?.xau?.cycle_seconds;
  const limit = (typeof cycle === "number" && cycle > 0 ? cycle : 30) * 2 * 1000;
  const fresh = card.spotState !== "market_closed" && Number.isFinite(time) && Math.max(0, now - time) <= limit;
  const book = card.sent === "unknown" ? "unknown" : `${card.sent} ${card.journal} ${card.r}`;
  return {
    SCAN: seat(card.spot, at, fresh),
    ANALYST: seat(card.engine + " " + card.window, at, fresh),
    AEGIS: seat(card.ready ? "pass" : card.reason, at, fresh),
    EXECUTOR: seat(card.open ? card.signal : "unknown", at, fresh),
    PORTFOLIO: seat(book, at, fresh),
    WATCHER: seat(text(status?.xau?.last_signal_at ?? status?.xau?.state), at, fresh),
  };
}

export function xauTape(status: XauStatus, now: number) {
  const xau = status?.xau;
  const last = xau && typeof xau.spot_last === "string" && Number(xau.spot_last) > 0 ? xau.spot_last : null;
  const at = xau?.spot_at ?? null;
  const time = at ? Date.parse(at) : NaN;
  const cycle = xau?.cycle_seconds;
  const limit = (typeof cycle === "number" && cycle > 0 ? cycle : 30) * 2 * 1000;
  const closed = xau?.spot_state === "market_closed";
  const recent = last !== null && Number.isFinite(time) && Math.max(0, now - time) <= limit;
  const fresh = !closed && recent;
  const quoteState = !last ? "unavailable" : closed ? "market_closed" : fresh ? "fresh" : "stale";
  return {
    symbol: "XAUUSD",
    last,
    at,
    fresh,
    state: quoteState,
    reason: xau?.ready_reason || xau?.state || "missing",
    source: "OANDA:XAUUSD via TradingView (spot)",
    active: status?.lanes?.primary === "xau" || last !== null,
  };
}
