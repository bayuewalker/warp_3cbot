import { observe } from "./adapters.ts";
import { record, sourceInstant } from "./frames.ts";
import type { ReadInput } from "./types";

const decimal = (value: unknown) => typeof value === "string" &&
  /^[+-]?\d+(\.\d+)?$/.test(value) && Number.isFinite(Number(value)) ? value : null;

export function deskPrices(read: ReadInput, now: number) {
  const data = record(read.data) ? read.data : {};
  const symbols = record(data.symbols) ? data.symbols : null;
  const items = symbols ? Object.entries(symbols).flatMap(([symbol, row]) => {
    if (!/^[A-Z0-9]{2,30}$/.test(symbol) || !record(row)) return [];
    const last = decimal(row.last);
    return last !== null && Number(last) > 0 ? [{ symbol, last, change: decimal(row.change_pct) }] : [];
  }) : null;
  const validTime = sourceInstant(data.as_of, now) !== null;
  const ttl = typeof data.limit_seconds === "number" && Number.isFinite(data.limit_seconds) && data.limit_seconds > 0
    ? data.limit_seconds : 30;
  const value = items?.length === 0 && (data.error || !validTime) ? null : items;
  return observe(read, value, {
    source: "binance", at: data.as_of, ttl, stale: Boolean(data.error && items?.length),
    partial: !data.error && (items?.length ?? 0) > 0 && (!validTime || items?.length !== Object.keys(symbols ?? {}).length),
    empty: items?.length === 0 && validTime && !data.error,
    reason: data.error ? "request_failed" : null,
  }, now);
}

export type QuotePoint = { time: number; last: number };
export type QuoteHistory = Record<string, QuotePoint[]>;

export function retainQuotes(history: QuoteHistory, prices: ReturnType<typeof deskPrices>): QuoteHistory {
  if (prices.state !== "confirmed" || prices.freshness !== "fresh" || !prices.sourceAt) return history;
  const time = Date.parse(prices.sourceAt);
  return Object.fromEntries((prices.value ?? []).map(quote => {
    const entries = history[quote.symbol] ?? [];
    const latest = entries.at(-1);
    const last = Number(quote.last);
    if (latest && time <= latest.time) return [quote.symbol, entries];
    return [quote.symbol, [...entries, { time, last }].slice(-60)];
  }));
}

export function xauTape(status: {
  lanes?: { primary?: string };
  xau?: { spot_last?: string | null; spot_at?: string | null; cycle_seconds?: number; ready_reason?: string | null; state?: string } | null;
} | null | undefined, now: number) {
  const xau = status?.xau;
  const last = xau && typeof xau.spot_last === "string" && Number(xau.spot_last) > 0 ? xau.spot_last : null;
  const at = xau?.spot_at ?? null;
  const time = at ? Date.parse(at) : NaN;
  const cycle = xau?.cycle_seconds;
  const limit = (typeof cycle === "number" && cycle > 0 ? cycle : 30) * 2 * 1000;
  const fresh = last !== null && Number.isFinite(time) && Math.max(0, now - time) <= limit;
  const reason = xau?.ready_reason || xau?.state || "missing";
  return {
    symbol: "XAUUSD",
    last,
    at,
    fresh,
    reason,
    source: "OANDA:XAUUSD via TradingView (spot)",
    active: status?.lanes?.primary === "xau" || last !== null,
  };
}

/** The wall and the miniature PC displays share one time-scaled line. */
export function quoteTrace(points: QuotePoint[]) {
  const first = points[0], last = points.at(-1);
  if (!first || !last || last.time <= first.time) return null;
  const low = Math.min(...points.map(point => point.last));
  const high = Math.max(...points.map(point => point.last));
  const line = points.map(point => {
    const x = (point.time - first.time) / (last.time - first.time) * 480 + 10;
    const y = low === high ? 40 : 72 - (point.last - low) / (high - low) * 64;
    return x + "," + y;
  }).join(" ");
  return { line, low, high, start: first.time, end: last.time };
}
