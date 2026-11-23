import { record, sourceInstant } from "./frames.ts";
import type { ObservedValue, ReadInput, ReadState } from "./types";

type Evidence = {
  source: ObservedValue<unknown>["source"];
  at?: unknown;
  ttl?: number;
  reason?: string | null;
  partial?: boolean;
  stale?: boolean;
  empty?: boolean;
  traceId?: unknown;
};

function observe<T>(read: ReadInput, value: T | null, evidence: Evidence, now: number): ObservedValue<T> {
  if (read.issue === "unauthorized") return {
    value: null, availability: "unavailable", freshness: "unknown", state: "unauthorized",
    source: evidence.source, sourceAt: null, receivedAt: null, reason: read.issue, traceId: null,
  };
  const time = sourceInstant(evidence.at, now);
  const sourceAt = time !== null ? String(evidence.at) : null;
  const freshness = evidence.stale ? "stale" : time === null || !evidence.ttl ? "unknown" :
    Math.max(0, now - time) > evidence.ttl * 1000 ? "stale" : "fresh";
  let state: ReadState;
  let reason = evidence.reason ?? null;
  if (read.issue === "awaiting_first_snapshot" || (record(read.data) && !Object.keys(read.data).length)) {
    state = "awaiting_first_snapshot";
    value = null;
  } else if (read.issue) {
    state = "error";
  } else if (read.data == null) {
    state = "loading";
  } else if (evidence.partial) {
    state = "partial";
  } else if (freshness === "stale") {
    state = "stale";
  } else if (value === null) {
    state = "unavailable";
  } else {
    state = evidence.empty ? "confirmed_empty" : "confirmed";
  }
  reason ??= read.issue ?? (value === null ? state : sourceAt === null ? "source_time_unavailable" : null);
  return {
    value, state, source: evidence.source, sourceAt,
    availability: state === "partial" || (state === "error" && value !== null) ? "partial" :
      value === null ? "unavailable" : "confirmed",
    freshness: read.issue && value !== null ? "stale" : evidence.partial && freshness === "fresh" ? "unknown" : freshness,
    receivedAt: read.received?.toISOString() ?? null, reason,
    traceId: typeof evidence.traceId === "string" ? evidence.traceId : null,
  };
}

function fields(read: ReadInput) {
  return record(read.data) ? read.data : {};
}

function decimal(value: unknown) {
  return typeof value === "string" && /^[+-]?\d+(\.\d+)?$/.test(value) && Number.isFinite(Number(value)) ? value : null;
}

function reason(value: unknown) {
  return typeof value === "string" && value.length ? value : null;
}

export type OverviewReads = Record<"status" | "bots" | "positions" | "accounts" | "book" | "events", ReadInput>;

/** Compose the portfolio only when both independent broker reads support it. */
function portfolioState(accounts: ReadInput, book: ReadInput, now: number) {
  for (const read of [accounts, book]) {
    if (read.issue === "unauthorized") return observe(read, null, { source: "threecommas" }, now);
  }
  for (const read of [accounts, book]) {
    if (read.data == null && read.issue == null) return observe(read, null, { source: "threecommas" }, now);
  }
  const balances = fields(accounts);
  const positions = fields(book);
  const profiles = balances.accounts;
  const rows = positions.positions;
  const balanceAt = !balances.error ? sourceInstant(balances.as_of, now) : null;
  const bookAt = sourceInstant(positions.as_of, now);
  const completeBalances = Array.isArray(profiles) && profiles.every(p => record(p) && !p.error) &&
    balanceAt !== null && !accounts.issue && !balances.error;
  const completeBook = Array.isArray(rows) && rows.every(record) && Array.isArray(positions.errors) &&
    positions.errors.length === 0 && !positions.error && !positions.stale && !book.issue && bookAt !== null;
  const stale = positions.stale === true || (balanceAt !== null && Math.max(0, now - balanceAt) > 90_000) ||
    (bookAt !== null && Math.max(0, now - bookAt) > 150_000);
  const partial = !(completeBalances && completeBook) && (completeBalances || completeBook ||
    (Array.isArray(rows) && rows.length > 0) || (Array.isArray(profiles) && profiles.length > 0) ||
    (Array.isArray(positions.errors) && positions.errors.length > 0));
  const input: ReadInput = {
    data: accounts.data ?? book.data,
    received: !accounts.received || (book.received && book.received > accounts.received) ? book.received : accounts.received,
    issue: partial ? null : accounts.issue ?? book.issue,
  };
  const empty = completeBalances && completeBook && profiles.length === 0 && rows.length === 0;
  const at = balanceAt !== null && bookAt !== null
    ? new Date(Math.min(balanceAt, bookAt)).toISOString() : null;
  const detail = reason(positions.error) ?? reason(balances.error) ?? book.issue ?? accounts.issue ??
    (Array.isArray(positions.errors) && positions.errors.length ? "incomplete_book" : null);
  const value = completeBalances && completeBook ? (empty ? "empty" : "reported") : null;
  // Both clocks were checked individually above. The oldest source stamp alone
  // cannot expire an account balance using the book's longer TTL.
  return observe(input, value, { source: "threecommas", at, ttl: 150, stale, partial, empty, reason: detail }, now);
}

export function overviewTelemetry(reads: OverviewReads, now: number) {
  const { status, bots, positions, book, events } = reads;
  const worker = fields(status);
  const publishedAt = sourceInstant(worker.published_at, now);
  const publicationStale = publishedAt === null || Math.max(0, now - publishedAt) > 900_000;
  const inventory = fields(bots).bots;
  const owned = Array.isArray(inventory) && inventory.every(bot => record(bot) && typeof bot.status === "string")
    ? inventory.filter(bot => bot.status === "confirmed" || bot.status === "active").length : null;
  const legacy = fields(positions);
  const legacyTime = sourceInstant(legacy.as_of, now);
  const legacyCurrent = legacyTime !== null && Math.max(0, now - legacyTime) <= 300_000;
  const count = legacyCurrent && !legacy.error && !positions.issue && Array.isArray(legacy.positions) &&
    legacy.positions.every(record) ? legacy.positions.length : null;
  const metrics = {
    equity: observe(status, decimal(worker.equity), { source: "worker", stale: publicationStale }, now),
    pnl: observe(status, decimal(worker.pnl_today), { source: "derived", stale: publicationStale }, now),
    activeBots: observe(bots, owned, { source: "derived", empty: owned === 0 }, now),
    open: observe(positions, count, {
      source: "threecommas", at: legacy.error ? null : legacy.as_of, ttl: 300, reason: reason(legacy.error), empty: count === 0,
    }, now),
  };

  // A worker publication describes the last evaluated state. It does not time a scan, model call or order.
  const pipeline = record(worker.pipeline) ? worker.pipeline : {};
  const projection: Evidence = { source: "worker", at: worker.published_at, ttl: 900 };
  function stage(name: string, allowed: string[]) {
    const row = pipeline[name];
    const value = record(row) && typeof row.state === "string" && allowed.includes(row.state) ? row.state : null;
    return observe(status, value, { ...projection, reason: record(row) ? reason(row.reason) : null }, now);
  }
  const gate = worker.mode === "observe" ? "observe" : worker.execution_permitted === false ? "blocked" :
    worker.execution_permitted === true ? "permitted" : null;
  const portfolio = portfolioState(reads.accounts, book, now);
  const log = fields(events);
  const list = log.events;
  const logTime = sourceInstant(log.as_of, now);
  const validLog = logTime !== null && Array.isArray(list) && list.every(event => record(event) && typeof event.event === "string");
  const last = validLog ? list.at(-1) : null;
  const agents = {
    SCAN: stage("screening", ["ready", "blocked"]),
    ANALYST: stage("analysis", ["ready", "pending"]),
    AEGIS: worker.halt === true ? observe(status, "halt", { ...projection, reason: "halt" }, now) : stage("risk", ["pass", "blocked"]),
    EXECUTOR: observe(status, gate, { ...projection, reason: reason(worker.reason) }, now),
    PORTFOLIO: portfolio,
    WATCHER: observe(events, validLog ? (last ? "reported" : "empty") : null, {
      source: "worker", at: log.as_of, ttl: 60, empty: validLog && !last, reason: last?.event, traceId: last?.trace_id,
    }, now),
  };
  return { metrics, agents };
}
