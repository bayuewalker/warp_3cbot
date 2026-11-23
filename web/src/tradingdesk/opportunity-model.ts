import { observe } from "./adapters.ts";
import { record, sourceInstant } from "./frames.ts";
import type { ObservedValue, ReadInput } from "./types";
import { payloadIssue } from "./validate.ts";

type Row = Record<string, unknown>;
const object = (value: unknown): Row => record(value) ? value : {};
const string = (value: unknown) => typeof value === "string" && value.trim() ? value.trim() : null;
const symbol = (v: unknown) => typeof v === "string" && /^[A-Z0-9]{2,30}$/.test(v) ? v : null;
const decimal = (v: unknown) => typeof v === "string" && /^[+-]?\d+(\.\d+)?$/.test(v) && Number.isFinite(Number(v)) ? v : null;
const price = (v: unknown) => decimal(v) && Number(v) > 0 ? String(v) : null;
const action = (v: unknown) => v === "BUY" || v === "WAIT" || v === "SKIP" ? v : null;
const stamp = (v: unknown, now: number) => sourceInstant(v, now) !== null ? String(v) : null;

function checked(path: string, read: ReadInput): ReadInput {
  return { ...read, issue: read.issue ?? (read.data == null ? null : payloadIssue(path, read.data)) };
}
function rows(read: ReadInput, key: string): Row[] | null {
  const list = object(read.data)[key];
  return read.issue !== "unauthorized" && Array.isArray(list) && list.every(record) ? list : null;
}

export function discovery(status: ReadInput, now: number) {
  const read = checked("/api/status", status);
  const data = object(read.data);
  const stage = object(object(data.pipeline).screening);
  const symbols = Array.isArray(data.universe) && data.universe.every(s => symbol(s)) ? data.universe as string[] : null;
  const publication = observe(read, string(data.mode), { source: "worker", at: data.published_at, ttl: 900 }, now);
  const universe = observe(read, symbols, { source: "worker", at: data.published_at, ttl: 900,
    empty: symbols?.length === 0, partial: Boolean(symbols && (!stage.source || !stage.state || !publication.sourceAt)) }, now);
  return { publication, universe, source: string(stage.source), stage: string(stage.state), halted: data.halt,
    reason: code(data.reason), mode: publication.value };
}
type Discovery = ReturnType<typeof discovery>;

function projection<T>(read: ReadInput, value: T, worker: Discovery, now: number, empty = false, partial = false) {
  const pub = worker.publication;
  // Publication is an independent liveness ceiling. Untimed lists have no source timestamp.
  const inaccessible = pub.state === "unauthorized" ? { ...read, issue: "unauthorized" as const } : read;
  return observe(inaccessible, value, { source: "worker", stale: pub.freshness === "stale",
    partial: pub.freshness !== "stale" && (pub.state !== "confirmed" || pub.freshness !== "fresh" || partial), empty }, now);
}

const publicCodes = new Set((
  "wrong_account stale risk_off daily_loss drawdown not_binance_spot not_trading listing_unknown listing_young " +
  "spread thin_book surge model_not_buy score stop_streak stop_cooldown tp_cooldown max_deals duplicate_symbol " +
  "alt_cap correlation_unknown correlation_cluster no_capital no_feasible_ladder missing_stop market_data rules " +
  "no_sl manual_exit score_below_80 ok market_not_trading stale_market_data illiquid trend_down range high_volatility " +
  "rsi_outside_band missing_min_notional spread_unknown spread_too_wide mtm_unreliable abnormal_volatility halt " +
  "observe_without_account observe duplicate_cooldown duplicate spot_only discovery_unavailable no_eligible_market screener_unavailable fail_closed excluded_spot_instrument not_usdt_spot missing_market_rule denylist outside_allowlist above_candidate_cap invalid_levels not_enough_candles too_few_trades non_positive_expectancy"
).split(" "));
function code(value: unknown) { return typeof value === "string" && publicCodes.has(value) ? value : null; }

const verdicts = new Set(["VETO", "WAIT_DATA_INCOMPLETE", "APPROVED_DEMO", "APPROVED_PAPER", "APPROVED_LIVE_PENDING_TRADE_DESK"]);
export function gateDecisions(input: ReadInput, worker: Discovery, now: number) {
  const read = checked("/api/decisions", input);
  const list = rows(read, "decisions")?.map(row => ({
    symbol: symbol(row.symbol), verdict: verdicts.has(String(row.status)) ? String(row.status) : null,
    reasons: Array.isArray(row.reasons) ? row.reasons.map(code) : null,
    warnings: Array.isArray(row.warnings) ? row.warnings.map(code) : null,
  })) ?? null;
  return projection(read, list, worker, now, list?.length === 0, list?.some(r => !r.symbol || !r.verdict ||
    !r.reasons || !r.warnings || r.reasons.includes(null) || r.warnings.includes(null)));
}

export function journalEvidence(input: ReadInput, worker: Discovery, now: number) {
  const read = checked("/api/signals", input);
  const records = rows(read, "decisions")?.filter(r => ["universe", "risk", "market", "broker", "backtest"].includes(String(r.stage)))
    .map(r => ({
      symbol: r.symbol === "*" ? "*" : symbol(r.symbol), stage: String(r.stage), at: stamp(r.recorded_at, now),
      outcome: ["passed", "planned", "rejected", "not_submitted", "skipped", "fail_closed"].includes(String(r.detail)) ? String(r.detail) : null,
      reasons: typeof r.reason === "string" ? r.reason.split(",").map(code) : [],
    })) ?? null;
  return projection(read, records, worker, now, records?.length === 0);
}

export function researchFeed(input: ReadInput, worker: Discovery, now: number) {
  const read = checked("/api/screener", input);
  const data = object(read.data);
  const hits = rows(read, "hits");
  const source = string(data.source);
  const failed = Boolean(data.error);
  const feed = observe(read, hits?.map(h => ({
    symbol: symbol(h.symbol), source: string(h.source), price: price(h.last), change: decimal(h.change_pct),
    volume: decimal(h.quote_volume), rsi: decimal(h.rsi), recommendation: decimal(h.recommendation),
  })) ?? null,
    { source: "worker", at: data.fetched_at, ttl: 1800, empty: hits?.length === 0,
      stale: failed || worker.publication.freshness === "stale",
      partial: !failed && worker.publication.freshness !== "stale" && (
        worker.publication.state !== "confirmed" || !worker.publication.sourceAt || !stamp(data.fetched_at, now) ||
        !source || hits?.some(h => h.source !== source || !symbol(h.symbol)) === true) }, now);
  const knownErrors = ["TradingView scanner unavailable", "TradingView scanner is not configured", "Binance public spot screener unavailable"];
  return { feed, source, failed, error: knownErrors.includes(String(data.error)) ? String(data.error) : null };
}

export function opportunities(input: ReadInput, worker: Discovery, gates: ReturnType<typeof gateDecisions>, now: number) {
  const read = checked("/api/opportunities", input);
  const list = rows(read, "cards")?.map(row => {
    const pair = symbol(row.symbol);
    const signalAt = stamp(row.as_of, now);
    const evidenceAt = stamp(row.evidence_as_of, now);
    const qualified = action(row.action);
    const score = object(row.score).total;
    const rubric = typeof score === "number" && Number.isFinite(score) && score >= 0 && score <= 100 ? score : null;
    const validTimes = Boolean(signalAt && evidenceAt);
    const isStale = worker.publication.freshness === "stale" || row.fresh === false;
    const entry = price(row.entry), stop = price(row.stop), take = price(row.take);
    const source = string(row.source), thesis = string(row.thesis);
    const threshold = row.min_score;
    const minScore = typeof threshold === "number" && Number.isFinite(threshold) && threshold >= 0 && threshold <= 100 ? threshold : null;
    const backtest = typeof row.backtest_passed === "boolean" ? row.backtest_passed : null;
    const evidence = observe(read, qualified, { source: "worker", at: signalAt, stale: isStale,
      partial: !isStale && (!pair || !qualified || !source || !validTimes || row.fresh !== true ||
        worker.publication.state !== "confirmed" || !worker.publication.sourceAt) }, now);
    if (evidence.state === "confirmed" && validTimes && row.fresh === true) evidence.freshness = "fresh";
    const components = ["trend", "reversal", "liquidity", "structure", "event"].map(k => {
      const value = object(row.score)[k];
      return typeof value === "number" && Number.isFinite(value) ? value : null;
    });
    const matching = pair ? gates.value?.filter(g => g.symbol === pair) ?? [] : [];
    // A research proposal describes a qualified BUY; a symbol-level assessment can still block it.
    const proposed = worker.mode === "observe" && worker.halted === false && worker.stage === "ready" &&
      worker.universe.state === "confirmed" && pair !== null && worker.universe.value?.includes(pair) === true &&
      evidence.state === "confirmed" && qualified === "BUY" && backtest === true && !!(entry && stop && take) &&
      (gates.state === "confirmed" || gates.state === "confirmed_empty") &&
      matching.every(g => g.verdict?.startsWith("APPROVED_"));
    return { symbol: pair, action: qualified, modelAction: action(row.model_action), rubric, components, minScore, scoreWarning: row.score_warning === true, confidence: decimal(row.confidence), thesis, source,
      entry, stop, take, signalAt, evidenceAt, evidence, backtest, backtestReason: string(row.backtest_reason),
      why: string(row.why), whyId: string(row.why_id), regime: string(row.regime), rsi: decimal(row.rsi),
      gate: matching, proposed };
  }) ?? null;
  return projection(read, list, worker, now, list?.length === 0, list?.some(r => r.evidence.state === "partial"));
}

export type Observation = ObservedValue<unknown>;
