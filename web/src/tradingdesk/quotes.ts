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
