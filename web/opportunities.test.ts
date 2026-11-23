import assert from "node:assert/strict";
import { test } from "node:test";
import { t } from "./src/i18n.ts";
import { discovery, gateDecisions, journalEvidence, opportunities, researchFeed } from "./src/tradingdesk/opportunity-model.ts";
import type { ReadInput } from "./src/tradingdesk/types.ts";

const now = Date.parse("2026-11-23T01:00:00Z"), at = new Date(now).toISOString();
const read = (data: unknown, issue: ReadInput["issue"] = null): ReadInput => ({ data, received: new Date(now), issue });
const status = { published_at: at, halt: false, mode: "observe", universe: ["SOLUSDT", "BTCUSDT"],
  pipeline: { screening: { state: "ready", source: "binance_spot_public" } } };
const worker = discovery(read(status), now);
const emptyGates = gateDecisions(read({ decisions: [] }), worker, now);
const card = { symbol: "SOLUSDT", action: "BUY", model_action: "BUY", fresh: true, as_of: at, evidence_as_of: at,
  score: { total: 82 }, source: "llm evidence snapshot", thesis: "Worker research", backtest_passed: true,
  entry: "120.0", stop: "117", take: "125" };
test("keeps ranked array order and preserves deterministic prices independently of the thesis", () => {
  const list = [card, { ...card, symbol: "BTCUSDT", score: { total: 99 }, thesis: "Use a different price" }];
  const result = opportunities(read({ cards: list }), worker, emptyGates, now).value!;
  assert.deepEqual(result.map(c => c.symbol), ["SOLUSDT", "BTCUSDT"]);
  assert.deepEqual(result.map(c => c.entry), ["120.0", "120.0"]);
  assert.equal(result[0].proposed, true);
  assert.equal(t("en", "proposedRead"), "PROPOSED / NOT SUBMITTED");
  assert.equal(t("en", "rubricScore"), "Rubric score");
  assert.equal(t("id", "rubricScore"), "Skor rubrik");
});
test("empty, error and stale publication are distinct even on a new browser receipt", () => {
  assert.equal(opportunities(read({ cards: [] }), worker, emptyGates, now).state, "confirmed_empty");
  assert.equal(opportunities(read(null), worker, emptyGates, now).state, "loading");
  assert.equal(opportunities(read({ cards: [card] }, "request_failed"), worker, emptyGates, now).value![0].evidence.state, "error");
  assert.equal(opportunities(read({ cards: [null] }), worker, emptyGates, now).state, "error");
  const old = discovery(read({ ...status, published_at: new Date(now - 900001).toISOString() }), now);
  const list = opportunities(read({ cards: [card] }), old, emptyGates, now);
  assert.equal(list.state, "stale");
  assert.equal(list.value![0].evidence.state, "stale");
  assert.equal(list.value![0].proposed, false);
});
test("absent model, missing levels and inadmissible evidence clock cannot produce a proposal", () => {
  const data = { ...card, source: null, thesis: null, entry: null, evidence_as_of: new Date(now + 6000).toISOString() };
  const item = opportunities(read({ cards: [data] }), worker, emptyGates, now).value![0];
  assert.equal(item.evidence.state, "partial");
  assert.equal(item.entry, null);
  assert.equal(item.thesis ?? t("en", "unknown"), "Not reported");
  assert.equal(item.proposed, false);
});
test("a failed backtest leaves worker WAIT and model BUY intact; VETO never becomes approval", () => {
  const veto = gateDecisions(read({ decisions: [{ symbol: card.symbol, status: "VETO", reasons: ["spread"], warnings: [] }] }), worker, now);
  const list = opportunities(read({ cards: [card, { ...card, symbol: "BTCUSDT", action: "WAIT", backtest_passed: false }] }), worker, veto, now).value!;
  assert.equal(list[0].gate[0].verdict, "VETO");
  assert.equal(list[0].proposed, false);
  assert.deepEqual([list[1].action, list[1].modelAction, list[1].backtest, list[1].proposed], ["WAIT", "BUY", false, false]);
});
test("filtered-out symbol and independent dated reason remain journal evidence only", () => {
  const rejected = journalEvidence(read({ decisions: [{ symbol: "ETHUSDT", stage: "universe", reason: "denylist",
    detail: "rejected", recorded_at: at }] }), worker, now);
  assert.equal(rejected.value![0].at, at);
  assert.deepEqual(rejected.value![0].reasons, ["denylist"]);
  assert.equal(worker.universe.value?.includes("ETHUSDT"), false);
  const result = opportunities(read({ cards: [{ ...card, symbol: "ETHUSDT" }] }), worker, emptyGates, now);
  assert.equal(result.value![0].proposed, false);
  assert.equal(emptyGates.sourceAt, null);
});
test("manual research never supplies exchange membership or a missing market metric", () => {
  const data = { hits: [{ symbol: "ETHUSDT", source: "tradingview", quote_volume: "123" }], source: "tradingview", fetched_at: at };
  assert.equal(researchFeed(read(data), worker, now).feed.state, "confirmed");
  assert.equal(researchFeed(read({ ...data, source: "binance" }), worker, now).feed.state, "partial");
  assert.equal(researchFeed(read({ ...data, error: "TradingView scanner unavailable" }), worker, now).feed.state, "stale");
  assert.deepEqual(worker.universe.value, status.universe);
});

test("worker freshness is scoped to publication while exchange evidence ages independently", () => {
  const readAt = (evidence_as_of: string, elapsed = 0, fresh = true) => {
    const clock = now + elapsed;
    return opportunities(read({ cards: [{ ...card, fresh, evidence_as_of }] }),
      discovery(read(status), clock), emptyGates, clock).value![0];
  };
  const older = new Date(now - 25 * 60_000).toISOString();
  const item = readAt(older);
  assert.equal(item.freshAtPublication, at);
  assert.equal(t("en", "freshAtPublication"), "Fresh at worker publication");
  assert.equal(item.evidence.freshness, "unknown");
  assert.equal(item.evidenceAgeMs, 25 * 60_000);
  assert.equal(readAt(older, 60_000).evidenceAgeMs, 26 * 60_000);
  assert.equal(readAt(new Date(now + 5_000).toISOString()).evidenceAgeMs, 0);
  assert.equal(readAt(new Date(now + 5_001).toISOString()).evidenceAgeMs, null);
  for (const stale of [readAt(older, 0, false), readAt(older, 900_001)]) {
    assert.equal(stale.evidence.state, "stale");
    assert.equal(stale.freshAtPublication, null);
  }
});

test("AEGIS keeps a reported private reason distinct from missing evidence", () => {
  const gate = (reasons: unknown) => gateDecisions(read({ decisions: [
    { symbol: card.symbol, status: "VETO", reasons, warnings: [] },
  ] }), worker, now);
  const hidden = gate(["secret_internal_code"]);
  assert.deepEqual(hidden.value![0].reasons, ["unlisted"]);
  assert.equal(hidden.state, "confirmed");
  assert.equal(t("en", "unlistedCode"), "Unlisted code (hidden)");
  assert.equal(gate(undefined).value![0].reasons, null);
  const missing = gate([null]);
  assert.deepEqual(missing.value![0].reasons, [null]);
  assert.equal(missing.state, "partial");
  assert.equal(t("en", "unknown"), "Not reported");
});
