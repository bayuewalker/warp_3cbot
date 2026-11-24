import { fmtTime } from "../format.ts";
import { t, type Key, type Lang } from "../i18n.ts";
import { observe } from "./adapters.ts";
import { record, sourceInstant } from "./frames.ts";
import type { ObservedValue, ReadInput } from "./types";

/** Typed, read-only view models over /api/accounts, /api/account-positions, /api/warp-bots and /api/positions.
 *  Nothing here derives a balance or a position size. A value is shown as reported, or as a labelled derivation. */

export const UNASSIGNED = "__unassigned__";
/** Binance tape limit (QUOTE_FRESH_SECONDS in live.py, also /api/prices limit_seconds). */
export const MARK_LIMIT_SECONDS = 30;
const ACCOUNT_CADENCE = 30;
const BOOK_CADENCE = 60;
const ACCOUNT_TTL_FACTOR = 3;
const BOOK_TTL_FACTOR = 2.5;
const OPEN_BOT = new Set(["pending", "unknown", "confirmed", "active", "paused"]);
const STABLE = new Set(["USDT", "USDC", "USD"]);

export type Source = "3commas" | "binance" | "computed" | "worker";
export type FactState =
  | "confirmed" | "confirmed_empty" | "partial" | "stale" | "loading" | "unauthorized" | "not_reported";
export type Fact<T> = {
  value: T | null;
  state: FactState;
  source: Source | null;
  sourceAt: string | null;
  receivedAt: string | null;
  reason: string | null;
};

type Row = Record<string, unknown>;
const object = (value: unknown): Row => (record(value) ? value : {});
const text = (value: unknown) => (typeof value === "string" && value.trim() ? value.trim() : null);
const DECIMAL = /^-?\d+(\.\d+)?$/;
const decimal = (value: unknown) =>
  typeof value === "string" && DECIMAL.test(value) && Number.isFinite(Number(value)) ? value : null;
const nonNegative = (value: unknown) => {
  const found = decimal(value);
  return found !== null && Number(found) >= 0 && !found.startsWith("-") ? found : null;
};
const positive = (value: unknown) => {
  const found = decimal(value);
  return found !== null && Number(found) > 0 ? found : null;
};
const count = (value: unknown) => (typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null);
const cadence = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;

type Pick<T> = { value: T | null; rejected: boolean };
function pick(raw: unknown, accept: (value: unknown) => string | null): Pick<string> {
  if (raw === null || raw === undefined || raw === "") return { value: null, rejected: false };
  const value = accept(raw);
  return { value, rejected: value === null };
}

const OBSERVED: Record<Source, ObservedValue<unknown>["source"]> = {
  "3commas": "threecommas", binance: "binance", computed: "derived", worker: "worker",
};

function stateOf(observed: ObservedValue<unknown>): FactState {
  switch (observed.state) {
    case "loading": case "awaiting_first_snapshot": return "loading";
    case "unauthorized": return "unauthorized";
    case "partial": case "stale": case "confirmed_empty": case "confirmed": return observed.state;
    case "error": return observed.value === null ? "not_reported" : "stale";
    default: return "not_reported";
  }
}

type Evidence = {
  source: Source | null;
  at?: unknown;
  ttl?: number;
  stale?: boolean;
  partial?: boolean;
  empty?: boolean;
  reason?: string | null;
};

/** One value with its P1 state. A missing value is never reported as stale, partial or confirmed. */
export function fact<T>(read: ReadInput, value: T | null, evidence: Evidence, now: number): Fact<T> {
  const present = value !== null;
  const observed = observe(read, value, {
    source: OBSERVED[evidence.source ?? "3commas"],
    at: present ? evidence.at : undefined,
    ttl: evidence.ttl,
    stale: present ? evidence.stale : undefined,
    partial: present ? evidence.partial : undefined,
    empty: evidence.empty,
    reason: evidence.reason,
  }, now);
  const state = stateOf(observed);
  const reported = observed.value !== null;
  return {
    value: observed.value, state,
    source: reported ? evidence.source : null,
    sourceAt: reported ? observed.sourceAt : null,
    receivedAt: observed.receivedAt,
    reason: reported && state === "confirmed" && observed.sourceAt === null ? "source_time_unavailable" : observed.reason,
  };
}

export const SOURCE_KEY: Record<Source, Key> = {
  "3commas": "srcThreeCommas", binance: "srcBinance", computed: "srcComputed", worker: "srcWorker",
};
export const sourceKey = (source: Source | null): Key | null => (source ? SOURCE_KEY[source] : null);

/** Text for a value. Missing or rejected values always read "Not reported", never zero. */
export function shown<T>(item: Fact<T>, lang: Lang, format: (value: T) => string | null | undefined): string {
  if (item.value === null) return t(lang, "unknown");
  return format(item.value) ?? t(lang, "unknown");
}

/** Source and time for a value: the source's own clock, or an explicit "time not reported" with browser receipt. */
export function provenance(item: Fact<unknown>, lang: Lang): string {
  const source = sourceKey(item.source);
  if (!source) return t(lang, "unknown");
  const at = fmtTime(item.sourceAt, lang, true);
  if (at) return `${t(lang, source)} · ${at}`;
  const received = fmtTime(item.receivedAt, lang, true);
  return `${t(lang, source)} · ${t(lang, "sourceTimeMissing")}${received ? ` · ${t(lang, "clientReceipt")} ${received}` : ""}`;
}

export type BrokerReason =
  "feed_stale" | "book_error" | "partial_read" | "no_source_time" | "old_as_of" | "refresh_failed";
const STALE_REASONS: BrokerReason[] = ["feed_stale", "no_source_time", "old_as_of", "refresh_failed"];
export type AccountReason = "accounts_error" | "no_source_time" | "old_as_of" | "refresh_failed";

export type BookError = {
  raw: string;
  kind: "incomplete_rows" | "read_cap" | "strategies" | "list_failed" | "read_failed" | "other";
  botKind: "dca" | "grid" | null;
  botCode: string | null;
};

export function classifyBookError(raw: string): BookError {
  const incomplete = /^(dca|grid) (.+): incomplete position rows$/.exec(raw);
  if (incomplete) return { raw, kind: "incomplete_rows", botKind: incomplete[1] as "dca" | "grid", botCode: incomplete[2] };
  if (raw === "position_read_cap") return { raw, kind: "read_cap", botKind: null, botCode: null };
  if (raw.startsWith("strategies:")) return { raw, kind: "strategies", botKind: null, botCode: null };
  const listing = /^(dca|grid)_bots:/.exec(raw);
  if (listing) return { raw, kind: "list_failed", botKind: listing[1] as "dca" | "grid", botCode: null };
  const failed = /^(dca|grid) .+: /.exec(raw);
  if (failed) return { raw, kind: "read_failed", botKind: failed[1] as "dca" | "grid", botCode: null };
  return { raw, kind: "other", botKind: null, botCode: null };
}

export type MarkState = { stale: boolean; reason: "no_mark_source" | "mark_source_missing" | null };

export type PositionView = {
  key: string;
  id: string | null;
  accountCode: string | null;
  accountName: string | null;
  kind: "dca" | "grid" | "strategy" | null;
  owner: "warp" | "external" | null;
  botName: string | null;
  botCode: string | null;
  pair: string | null;
  symbol: string | null;
  baseAsset: string | null;
  quoteAsset: string | null;
  side: "long" | "short" | null;
  status: string | null;
  averagePrice: Fact<string>;
  size: Fact<string>;
  cost: Fact<string>;
  mark: Fact<string>;
  markState: MarkState;
  pnlUsd: Fact<string>;
  pnlPct: Fact<string>;
  safety: Fact<{ used: number | null; max: number | null }>;
  takeProfits: Fact<string[]>;
  stop: Fact<string>;
  openedAt: Fact<string>;
  /** Strategy rows from the last good read after a failed refresh. */
  retained: boolean;
  /** The bot's rows were reported with a mismatching count, identity or shape. */
  incomplete: boolean;
  rejected: string[];
  state: "confirmed" | "partial" | "stale";
};

export type BookView = {
  read: Fact<PositionView[]>;
  positions: PositionView[];
  known: boolean;
  malformedRows: number;
  duplicateRows: number;
  asOf: string | null;
  cadenceSeconds: number;
  byAccount: Record<string, number> | null;
  errors: BookError[];
  error: string | null;
  awaiting: boolean;
  /** The feed is old or its refresh failed: feed_stale, no_source_time, old_as_of or refresh_failed. */
  brokerStale: boolean;
  /** The feed is current but incomplete: partial_read or book_error. Independent of brokerStale. */
  brokerPartial: boolean;
  brokerReasons: BrokerReason[];
  complete: boolean;
};

type Context = { read: ReadInput; now: number; at: string | null; ttl: number; stale: boolean; incomplete: Set<string> };

function owner(value: unknown): PositionView["owner"] {
  return value === "warp" || value === "external" ? value : null;
}

function position(raw: Row, index: number, context: Context): PositionView {
  const rejected: string[] = [];
  const retained = raw.stale === true;
  const stale = context.stale || retained;
  const make = <T>(name: string, picked: Pick<T>, source: Source | null, at: string | null = context.at, reason: string | null = null): Fact<T> => {
    if (picked.rejected) rejected.push(name);
    return fact(context.read, picked.value, {
      source: picked.value === null ? null : source, at, ttl: context.ttl, stale,
      reason: picked.rejected ? reason ?? "unexpected_value" : null,
    }, context.now);
  };
  const id = text(raw.id);
  const kind: PositionView["kind"] = raw.kind === "dca" || raw.kind === "grid" || raw.kind === "strategy" ? raw.kind : null;
  const botCode = text(raw.bot_code);

  const price = pick(raw.current_price, positive);
  const markSource = raw.current_price_source === "3commas" || raw.current_price_source === "binance"
    ? raw.current_price_source : null;
  const orphanPrice = price.value !== null && markSource === null;
  const mark = make("current_price", { value: markSource ? price.value : null, rejected: price.rejected || orphanPrice },
    markSource, markSource === "3commas" ? context.at : null, orphanPrice ? "mark_source_missing" : null);
  const markState: MarkState = {
    stale: mark.value === null,
    reason: mark.value !== null ? null : orphanPrice ? "mark_source_missing" : "no_mark_source",
  };

  // Computed PnL is only valid on top of a reported mark. A claim without one is rejected, not displayed.
  const pnl = (name: string, sourceName: string) => {
    const picked = pick(raw[name], decimal);
    const claimed = raw[sourceName];
    const source = claimed === "3commas" || claimed === "computed" ? claimed : null;
    const unverified = picked.value !== null && (source === null || (source === "computed" && mark.value === null));
    const at = source === "3commas" || mark.source === "3commas" ? context.at : null;
    return make(name, { value: unverified ? null : picked.value, rejected: picked.rejected || unverified }, source, at, "pnl_source_unverified");
  };

  const safetyUsed = count(raw.safety_used);
  const safetyMax = count(raw.safety_max);
  const safetyValue = safetyUsed === null && safetyMax === null ? null : { used: safetyUsed, max: safetyMax };

  let takeProfits: Pick<string[]> = { value: null, rejected: false };
  if (raw.take_profits !== undefined && raw.take_profits !== null) {
    if (Array.isArray(raw.take_profits)) {
      const valid = raw.take_profits.map(positive).filter((item): item is string => item !== null);
      takeProfits = { value: valid.length ? valid : null, rejected: valid.length !== raw.take_profits.length };
    } else {
      takeProfits = { value: null, rejected: true };
    }
  }
  const opened = pick(raw.opened_at, (value) => (sourceInstant(value, context.now) !== null ? String(value) : null));

  const view = {
    key: `${id ?? "row"}:${index}`,
    id,
    accountCode: text(raw.account_code),
    accountName: text(raw.account_name),
    kind,
    owner: owner(raw.owner),
    botName: text(raw.bot_name),
    botCode,
    pair: text(raw.pair),
    symbol: text(raw.symbol),
    baseAsset: text(raw.base_asset),
    quoteAsset: text(raw.quote_asset),
    side: (raw.side === "long" || raw.side === "short" ? raw.side : null) as PositionView["side"],
    status: text(raw.status),
    averagePrice: make("average_price", pick(raw.average_price, positive), "3commas"),
    size: make("size", pick(raw.size, nonNegative), "3commas"),
    cost: make("cost", pick(raw.cost, nonNegative), "3commas"),
    mark,
    markState,
    pnlUsd: pnl("pnl_usd", "pnl_usd_source"),
    pnlPct: pnl("pnl_pct", "pnl_pct_source"),
    safety: make("safety", { value: safetyValue, rejected: false }, "3commas"),
    takeProfits: make("take_profits", takeProfits, "3commas"),
    stop: make("stop_price", pick(raw.stop_price, positive), "3commas"),
    openedAt: make("opened_at", opened, "3commas"),
  };
  const incomplete = kind !== null && botCode !== null && context.incomplete.has(`${kind} ${botCode}`);
  if (id === null) rejected.push("id");
  return {
    ...view, retained, incomplete, rejected,
    state: rejected.length || incomplete ? "partial" : stale ? "stale" : "confirmed",
  };
}

export function bookView(read: ReadInput, now: number): BookView {
  const data = object(read.data);
  const loaded = read.data != null && Object.keys(data).length > 0;
  const error = text(data.error);
  const awaiting = error === "not_loaded";
  const raw = Array.isArray(data.positions) ? data.positions : null;
  const errors = Array.isArray(data.errors) ? data.errors.filter((item): item is string => typeof item === "string") : null;
  const classified = (errors ?? []).map(classifyBookError);
  const incomplete = new Set(classified.flatMap((item) =>
    item.kind === "incomplete_rows" ? [`${item.botKind} ${item.botCode}`] : []));
  const cadenceSeconds = cadence(data.cadence_seconds, BOOK_CADENCE);
  const ttl = cadenceSeconds * BOOK_TTL_FACTOR;
  const asOf = text(data.as_of);
  const time = sourceInstant(data.as_of, now);

  const reasons: BrokerReason[] = [];
  if (loaded && !awaiting) {
    if (data.stale === true) reasons.push("feed_stale");
    if (error) reasons.push("book_error");
    if (errors === null || errors.length > 0) reasons.push("partial_read");
    if (time === null) reasons.push("no_source_time");
    else if (now - time > ttl * 1000) reasons.push("old_as_of");
    if (read.issue && read.issue !== "unauthorized" && read.issue !== "awaiting_first_snapshot") reasons.push("refresh_failed");
  }
  const brokerStale = reasons.some((reason) => STALE_REASONS.includes(reason));
  const brokerPartial = reasons.some((reason) => reason === "partial_read" || reason === "book_error");
  const context: Context = { read, now, at: asOf, ttl, stale: brokerStale, incomplete };

  const positions: PositionView[] = [];
  const seen = new Set<string>();
  let malformedRows = 0;
  let duplicateRows = 0;
  (raw ?? []).forEach((item, index) => {
    if (!record(item)) {
      malformedRows += 1;
      return;
    }
    const view = position(item, index, context);
    // The same broker id can never be two positions. Collapse a repeat instead of counting it twice.
    const identity = view.id && view.kind ? `${view.kind}:${view.id}` : null;
    if (identity && seen.has(identity)) {
      duplicateRows += 1;
      return;
    }
    if (identity) seen.add(identity);
    positions.push(view);
  });

  const known = raw !== null && !awaiting;
  const tally = object(data.by_account);
  const byAccount = record(data.by_account) && Object.values(tally).every((value) => count(value) !== null)
    ? (tally as Record<string, number>) : null;
  const partial = known && (malformedRows > 0 || duplicateRows > 0 || errors === null || errors.length > 0 || Boolean(error));
  const value = known ? positions : null;
  return {
    read: fact(read, value, {
      source: "3commas", at: asOf, ttl, stale: data.stale === true, partial,
      empty: value !== null && value.length === 0,
      reason: error ?? (errors && errors.length ? "incomplete_book" : null),
    }, now),
    positions: known ? positions : [],
    known, malformedRows, duplicateRows, asOf, cadenceSeconds, byAccount, errors: classified, error, awaiting,
    brokerStale, brokerPartial, brokerReasons: reasons,
    complete: known && malformedRows === 0 && duplicateRows === 0 && errors !== null && !brokerStale && !brokerPartial,
  };
}

export type AssetView = {
  asset: string;
  total: Fact<string>;
  usd: Fact<string>;
  usdReason: string | null;
  markAgeSeconds: number | null;
  markStale: boolean;
};

export type AccountView = {
  code: string;
  name: string;
  demo: boolean | null;
  badge: "DEMO" | "REAL" | null;
  status: string | null;
  exchange: string | null;
  market: string | null;
  quote: string | null;
  selectable: boolean;
  reason: string | null;
  error: string | null;
  total: Fact<string>;
  free: Fact<string>;
  used: Fact<string>;
  assets: AssetView[];
  stale: boolean;
  rejected: string[];
};

export type AccountsView = {
  input: ReadInput;
  read: Fact<AccountView[]>;
  accounts: AccountView[];
  known: boolean;
  selectedCode: string | null;
  asOf: string | null;
  cadenceSeconds: number;
  error: string | null;
  stale: boolean;
  reasons: AccountReason[];
  /** The profile list is a complete, in-date read that may attribute positions to accounts. */
  current: boolean;
  sleeve: { eligible: Fact<string>; reason: string | null };
};

/** DEMO or REAL is shown only when the boolean and the badge agree. A conflict is "Not reported", never a guess. */
function badgeOf(demo: unknown, badge: unknown): "DEMO" | "REAL" | null {
  if (typeof demo !== "boolean") return null;
  if (badge === undefined || badge === null) return demo ? "DEMO" : "REAL";
  const expected = demo ? "DEMO" : "REAL";
  return badge === expected ? expected : null;
}

export function accountsView(read: ReadInput, now: number): AccountsView {
  const data = object(read.data);
  const loaded = read.data != null && Object.keys(data).length > 0;
  const raw = Array.isArray(data.accounts) ? data.accounts : null;
  const error = text(data.error);
  const cadenceSeconds = cadence(data.cadence_seconds, ACCOUNT_CADENCE);
  const ttl = cadenceSeconds * ACCOUNT_TTL_FACTOR;
  const asOf = text(data.as_of);
  const time = sourceInstant(data.as_of, now);
  const reasons: AccountReason[] = [];
  if (loaded) {
    if (error) reasons.push("accounts_error");
    if (time === null) reasons.push("no_source_time");
    else if (now - time > ttl * 1000) reasons.push("old_as_of");
    if (read.issue && read.issue !== "unauthorized" && read.issue !== "awaiting_first_snapshot") reasons.push("refresh_failed");
  }
  const stale = reasons.length > 0;
  // Failed attempts are time-stamped too. Their stamp says nothing about when a retained balance was read.
  const at = error ? null : asOf;

  const accounts: AccountView[] = [];
  (raw ?? []).forEach((item) => {
    if (!record(item) || !text(item.code)) return;
    const code = String(text(item.code));
    const rejected: string[] = [];
    const balance = (name: string): Fact<string> => {
      const picked = pick(item[name], nonNegative);
      if (picked.rejected) rejected.push(name);
      return fact(read, picked.value, {
        source: "3commas", at, ttl, stale, reason: picked.rejected ? "unexpected_value" : null,
      }, now);
    };
    const accountError = text(item.error);
    const assets: AssetView[] = Array.isArray(item.assets)
      ? item.assets.flatMap((asset) => {
        if (!record(asset) || !text(asset.asset)) return [];
        const name = String(text(asset.asset));
        const usd = pick(asset.usd, nonNegative);
        const total = pick(asset.total, nonNegative);
        const age = typeof asset.quote_age_seconds === "number" && Number.isFinite(asset.quote_age_seconds) &&
          asset.quote_age_seconds >= 0 ? asset.quote_age_seconds : null;
        // USD of a stable coin is its own balance. Other coins are a reported balance times a Binance mark.
        const stable = STABLE.has(name);
        return [{
          asset: name,
          total: fact(read, total.value, { source: "3commas", at, ttl, stale, reason: total.rejected ? "unexpected_value" : null }, now),
          usd: fact(read, usd.value, {
            source: stable ? "3commas" : "computed", at: stable ? at : null, ttl, stale,
            reason: usd.rejected ? "unexpected_value" : null,
          }, now),
          usdReason: text(asset.usd_reason),
          markAgeSeconds: age,
          markStale: age !== null && age > MARK_LIMIT_SECONDS,
        }];
      })
      : [];
    const demo = typeof item.demo === "boolean" ? item.demo : null;
    accounts.push({
      code,
      name: text(item.name) ?? code,
      demo,
      badge: badgeOf(item.demo, item.badge),
      status: text(item.status),
      exchange: text(item.exchange_name) ?? text(item.exchange_code),
      market: text(item.market),
      quote: text(item.quote),
      selectable: item.selectable === true,
      reason: text(item.reason),
      error: accountError,
      total: balance("total"), free: balance("free"), used: balance("used"),
      assets, stale: stale || accountError !== null, rejected,
    });
  });

  const known = raw !== null;
  const selected = object(data.selected);
  const sleeve = object(data.sleeve);
  const eligible = pick(sleeve.eligible, nonNegative);
  return {
    input: read,
    read: fact(read, known ? accounts : null, {
      source: "3commas", at, ttl, stale: error !== null,
      partial: known && (accounts.length !== raw!.length || Boolean(error)),
      empty: known && accounts.length === 0, reason: error,
    }, now),
    accounts, known, selectedCode: text(selected.code), asOf, cadenceSeconds, error, stale, reasons,
    current: known && !stale,
    sleeve: {
      eligible: fact(read, stale ? null : eligible.value, { source: "worker", at, ttl, reason: eligible.rejected ? "unexpected_value" : null }, now),
      reason: text(sleeve.reason),
    },
  };
}

export type BotsView = {
  input: ReadInput;
  read: Fact<unknown[]>;
  bots: { accountCode: string | null; status: string | null }[] | null;
  malformed: boolean;
  budget: {
    sleeveUsdt: Fact<string>;
    sleevePct: Fact<string>;
    used: Fact<string>;
    remaining: Fact<string>;
    maxBots: Fact<number>;
  };
};

export function botsView(read: ReadInput, now: number): BotsView {
  const data = object(read.data);
  const raw = Array.isArray(data.bots) ? data.bots : null;
  const malformed = raw !== null && !raw.every((bot) => record(bot) && typeof bot.status === "string");
  const bots = raw === null || malformed
    ? null : raw.map((bot) => ({ accountCode: text(object(bot).account_code), status: text(object(bot).status) }));
  const budget = object(data.budget);
  // The budget is worker configuration. It carries no source time of its own.
  const config = (name: string, accept: (value: unknown) => string | null) => {
    const picked = pick(budget[name], accept);
    return fact(read, picked.value, { source: "worker", reason: picked.rejected ? "unexpected_value" : null }, now);
  };
  const share = (value: unknown) => {
    const found = nonNegative(value);
    return found !== null && Number(found) <= 1 ? found : null;
  };
  const cap = count(budget.max_bots);
  return {
    input: read,
    read: fact(read, bots, { source: "worker", partial: malformed, empty: bots !== null && bots.length === 0 }, now),
    bots, malformed,
    budget: {
      sleeveUsdt: config("sleeve_usdt", nonNegative),
      sleevePct: config("sleeve_pct", share),
      used: config("used", nonNegative),
      remaining: config("remaining", decimal),
      maxBots: fact(read, cap !== null && cap >= 1 ? cap : null, { source: "worker" }, now),
    },
  };
}

export type Capital = {
  quote: string | null;
  total: Fact<string>;
  free: Fact<string>;
  used: Fact<string>;
  sleeveEligible: Fact<string>;
  sleeveConfigured: Fact<string>;
  sleevePct: Fact<string>;
  budgetUsed: Fact<string>;
  budgetRemaining: Fact<string>;
  openBots: Fact<number>;
  maxBots: Fact<number>;
};

export function capital(
  accounts: AccountsView, account: AccountView, bots: BotsView, now: number,
): Capital {
  const selected = accounts.selectedCode === account.code;
  const owned = bots.bots?.filter((bot) => bot.accountCode === account.code && bot.status !== null && OPEN_BOT.has(bot.status));
  const eligible = selected ? accounts.sleeve.eligible : fact<string>(accounts.input, null, {
    source: "worker", reason: "not_selected_account",
  }, now);
  return {
    quote: account.quote,
    total: account.total, free: account.free, used: account.used,
    sleeveEligible: eligible,
    sleeveConfigured: bots.budget.sleeveUsdt,
    sleevePct: bots.budget.sleevePct,
    budgetUsed: bots.budget.used,
    budgetRemaining: bots.budget.remaining,
    openBots: fact(bots.input, owned ? owned.length : null, {
      source: "computed", empty: owned?.length === 0, reason: owned ? null : "bots_unavailable",
    }, now),
    maxBots: bots.budget.maxBots,
  };
}

export type Scope = {
  unassigned: PositionView[];
  current: string;
  account: AccountView | null;
  shown: PositionView[];
  /** Exact count for an account, or null when the book or the account list cannot support one. */
  count: (code: string) => number | null;
};

/** Unassigned holds every row whose account cannot be confirmed. A failed accounts read never hides rows. */
export function scope(accounts: AccountsView, book: BookView, viewing: string, selected: string): Scope {
  const profiles = accounts.accounts;
  const unassigned = book.positions.filter((row) =>
    !accounts.current || !row.accountCode || !profiles.some((profile) => profile.code === row.accountCode));
  const tallied = (code: string) => book.positions.filter((row) => row.accountCode === code).length;
  const fallback =
    (!accounts.current && unassigned.length > 0 && UNASSIGNED) ||
    (selected && profiles.some((profile) => profile.code === selected) && selected) ||
    profiles.find((profile) => tallied(profile.code) > 0)?.code ||
    profiles[0]?.code ||
    (unassigned.length ? UNASSIGNED : "");
  const valid = viewing && ((viewing === UNASSIGNED && unassigned.length > 0) || profiles.some((profile) => profile.code === viewing));
  const current = valid ? viewing : fallback;
  return {
    unassigned, current,
    account: profiles.find((profile) => profile.code === current) ?? null,
    shown: current === UNASSIGNED ? unassigned : book.positions.filter((row) => accounts.current && row.accountCode === current),
    count(code) {
      if (!accounts.current || !book.complete) return null;
      const own = tallied(code);
      // by_account is the producer's own tally. A disagreement with its rows means neither can be trusted.
      if (book.byAccount && (book.byAccount[code] ?? 0) !== own) return null;
      return own;
    },
  };
}

export type BotGroup = {
  key: string;
  kind: PositionView["kind"];
  owner: PositionView["owner"];
  name: string | null;
  rows: number;
  incomplete: boolean;
};

/** Rows grouped by the bot that reported them. This is a row tally, not a position count. */
export function botGroups(rows: PositionView[]): BotGroup[] {
  const groups = new Map<string, BotGroup>();
  for (const row of rows) {
    const key = `${row.owner}:${row.kind}:${row.botCode ?? row.botName ?? row.id ?? row.key}`;
    const group = groups.get(key) ?? { key, kind: row.kind, owner: row.owner, name: row.botName ?? row.botCode, rows: 0, incomplete: false };
    group.rows += 1;
    group.incomplete ||= row.incomplete;
    groups.set(key, group);
  }
  return [...groups.values()];
}
