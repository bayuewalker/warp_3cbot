import assert from "node:assert/strict";
import test from "node:test";
import { t } from "./src/i18n.ts";
import { fmtMoney, fmtQty } from "./src/format.ts";
import {
  UNASSIGNED, accountsView, botGroups, botsView, bookView, capital, classifyBookError,
  provenance, scope, shown, sourceKey,
} from "./src/tradingdesk/accounts-model.ts";
import type { ReadInput, ReadIssue } from "./src/tradingdesk/types.ts";

const stamp = "2026-11-24T10:00:00.000Z";
const now = Date.parse(stamp) + 1000;
const old = "2026-11-24T09:00:00.000Z";
const read = (data: unknown, issue: ReadIssue | null = null): ReadInput => ({ data, received: new Date(now), issue });
const row = (extra: Record<string, unknown> = {}) => ({
  id: "p1", account_code: "A1", account_name: "Alpha", kind: "dca", bot_name: "Bot", bot_code: "B1", owner: "external",
  pair: "BTC/USDT", symbol: "BTCUSDT", base_asset: "BTC", quote_asset: "USDT", side: "long", status: "bought", paper: true,
  average_price: "100", size: "2", cost: "200", current_price: "110", current_price_source: "3commas",
  pnl_usd: "20", pnl_usd_source: "3commas", pnl_pct: "10", pnl_pct_source: "3commas", safety_used: 1, safety_max: 3,
  take_profits: ["120"], stop_price: "90", opened_at: "2026-11-24T08:00:00Z", ...extra,
});
const payload = (positions: unknown[], extra: Record<string, unknown> = {}) => ({
  positions, by_account: {}, as_of: stamp, error: null, errors: [], stale: false, cadence_seconds: 60, readonly: true, ...extra,
});
const account = (extra: Record<string, unknown> = {}) => ({
  code: "A1", name: "Alpha", demo: true, badge: "DEMO", status: "active", exchange_name: "Binance", market: "spot",
  quote: "USDT", free: "10", used: "5", total: "15", assets: [], selectable: true, reason: "ok", ...extra,
});
const accountsPayload = (list: unknown[], extra: Record<string, unknown> = {}) => ({
  accounts: list, selected: { code: "A1" }, as_of: stamp, error: null, sleeve: { eligible: "8", reason: null }, ...extra,
});

test("null size and cost from a partial fill or partial sell read Not reported, never zero or full size", () => {
  const [view] = bookView(read(payload([row({ size: null, cost: null, status: "partially_sold" })])), now).positions;
  assert.equal(view.size.value, null);
  assert.equal(view.size.state, "not_reported");
  assert.equal(view.cost.value, null);
  assert.equal(shown(view.size, "en", (v) => fmtQty(v, "en")), t("en", "unknown"));
  assert.equal(shown(view.cost, "en", (v) => fmtMoney(v, "en")), "Not reported");
  assert.equal(shown(view.size, "id", (v) => fmtQty(v, "id")), "Belum dilaporkan");
  // The other reported values are untouched and do not fill the gap.
  assert.equal(view.averagePrice.value, "100");
});

test("a missing sold amount leaves size unreported even when the entry-side fields exist", () => {
  const [view] = bookView(read(payload([row({ size: null, cost: "200" })])), now).positions;
  assert.equal(view.size.value, null);
  assert.equal(view.cost.value, "200");
  assert.equal(t("en", "soldSize"), "Sold size");
});

test("a confirmed zero stays visible and is not treated as missing", () => {
  const [view] = bookView(read(payload([row({ pnl_usd: "0", pnl_pct: "0", size: "0" })])), now).positions;
  assert.equal(view.pnlUsd.value, "0");
  assert.equal(view.pnlUsd.state, "confirmed");
  assert.equal(view.size.value, "0");
  assert.equal(shown(view.pnlUsd, "en", (v) => fmtMoney(v, "en")), "0.00");
});

test("a malformed record is ignored and the book becomes partial with the remaining rows kept", () => {
  const book = bookView(read(payload([row(), null, "x"])), now);
  assert.equal(book.positions.length, 1);
  assert.equal(book.malformedRows, 2);
  assert.equal(book.read.state, "partial");
  assert.equal(book.complete, false);
});

test("a row with non-decimal or negative values is partial and rejects only those fields", () => {
  const [view] = bookView(read(payload([row({ size: "1e3", cost: "-5", average_price: "abc", opened_at: "yesterday", take_profits: ["x", "120"] })])), now).positions;
  assert.equal(view.size.value, null);
  assert.equal(view.cost.value, null);
  assert.equal(view.averagePrice.value, null);
  assert.equal(view.openedAt.value, null);
  assert.deepEqual(view.takeProfits.value, ["120"]);
  assert.equal(view.state, "partial");
  assert.deepEqual(view.rejected.sort(), ["average_price", "cost", "opened_at", "size", "take_profits"]);
});

test("a stale strategy feed raises the broker-stale condition and keeps every row", () => {
  const book = bookView(read(payload([row({ kind: "strategy", stale: true }), row({ id: "p2" })], {
    stale: true, error: "strategies_stale", errors: ["strategies: HTTP 429"],
  })), now);
  assert.equal(book.brokerStale, true);
  assert.equal(book.brokerPartial, true);
  assert.ok(book.brokerReasons.includes("feed_stale"));
  assert.equal(book.positions.length, 2);
  assert.equal(book.positions[0].retained, true);
  assert.equal(book.positions[0].state, "stale");
  assert.equal(book.positions[0].size.state, "stale");
  assert.equal(book.complete, false);
  assert.equal(t("en", "brokerStaleBadge"), "Broker book stale");
  assert.equal(t("id", "brokerStaleBadge"), "Buku broker basi");
});

test("an old as_of or a failed refresh is broker-stale while the retained rows stay listed", () => {
  const aged = bookView(read(payload([row()], { as_of: old })), now);
  assert.ok(aged.brokerReasons.includes("old_as_of"));
  assert.equal(aged.positions.length, 1);
  const failed = bookView(read(payload([row()]), "request_failed"), now);
  assert.ok(failed.brokerReasons.includes("refresh_failed"));
  assert.equal(failed.positions[0].averagePrice.state, "stale");
});

test("an account profile error is account-stale and its positions are still listed", () => {
  const accounts = accountsView(read(accountsPayload([account({ error: "HTTP 401", total: null, free: null, used: null, selectable: false })])), now);
  assert.equal(accounts.accounts[0].stale, true);
  assert.equal(accounts.accounts[0].total.state, "not_reported");
  assert.equal(accounts.stale, false);
  const book = bookView(read(payload([row()], { by_account: { A1: 1 } })), now);
  const view = scope(accounts, book, "", "A1");
  assert.equal(view.current, "A1");
  assert.equal(view.shown.length, 1);
});

test("an accounts read error marks every account stale and drops the balance clock", () => {
  const accounts = accountsView(read(accountsPayload([account()], { error: "HTTP 500" })), now);
  assert.equal(accounts.stale, true);
  assert.deepEqual(accounts.reasons, ["accounts_error"]);
  assert.equal(accounts.accounts[0].total.state, "stale");
  assert.equal(accounts.accounts[0].total.sourceAt, null);
  assert.equal(accounts.sleeve.eligible.value, null);
});

test("negative or non-decimal balances are rejected as Not reported", () => {
  const accounts = accountsView(read(accountsPayload([account({ total: "-1", free: "1e2", used: "abc", assets: [
    { asset: "BTC", total: "-0.5", usd: "NaN", usd_reason: null },
  ] })])), now);
  const [item] = accounts.accounts;
  for (const balance of [item.total, item.free, item.used]) {
    assert.equal(balance.value, null);
    assert.equal(balance.state, "not_reported");
    assert.equal(shown(balance, "en", (v) => fmtMoney(v, "en")), "Not reported");
  }
  assert.deepEqual(item.rejected.sort(), ["free", "total", "used"]);
  assert.equal(item.assets[0].total.value, null);
  assert.equal(item.assets[0].usd.value, null);
});

test("DEMO or REAL is shown only when the badge and the boolean agree", () => {
  const list = accountsView(read(accountsPayload([
    account(), account({ code: "R1", demo: false, badge: "REAL" }), account({ code: "X1", demo: true, badge: "REAL" }),
    account({ code: "Y1", demo: "yes", badge: undefined }), account({ code: "Z1", demo: false, badge: undefined }),
  ])), now).accounts;
  assert.deepEqual(list.map((a) => a.badge), ["DEMO", "REAL", null, null, "REAL"]);
});

test("a missing symbol mark is Not reported and PnL is never labelled computed", () => {
  const [view] = bookView(read(payload([row({
    current_price: null, current_price_source: null, pnl_usd: null, pnl_usd_source: null, pnl_pct: null, pnl_pct_source: null,
  })])), now).positions;
  assert.equal(view.mark.value, null);
  assert.equal(shown(view.mark, "en", (v) => v), "Not reported");
  assert.deepEqual(view.markState, { stale: true, reason: "no_mark_source" });
  assert.notEqual(view.pnlUsd.source, "computed");
  assert.notEqual(view.pnlPct.source, "computed");
  assert.equal(view.pnlUsd.value, null);
});

test("a computed PnL claim without a mark, or any value without a source, is rejected", () => {
  const [bare] = bookView(read(payload([row({ current_price: null, current_price_source: null, pnl_usd_source: "computed" })])), now).positions;
  assert.equal(bare.pnlUsd.value, null);
  assert.ok(bare.rejected.includes("pnl_usd"));
  const [orphan] = bookView(read(payload([row({ pnl_pct_source: null, current_price_source: null })])), now).positions;
  assert.equal(orphan.pnlPct.value, null);
  assert.equal(orphan.mark.value, null);
  assert.deepEqual(orphan.markState, { stale: true, reason: "mark_source_missing" });
});

test("3Commas PnL and computed PnL carry different source labels", () => {
  const [reported, derived] = bookView(read(payload([
    row(),
    row({ id: "p2", current_price_source: "binance", pnl_usd_source: "computed", pnl_pct_source: "computed" }),
  ])), now).positions;
  assert.equal(reported.pnlUsd.source, "3commas");
  assert.equal(derived.pnlUsd.source, "computed");
  assert.equal(derived.mark.source, "binance");
  const labels = [sourceKey(reported.pnlUsd.source), sourceKey(derived.pnlUsd.source)].map((key) => t("en", key!));
  assert.deepEqual(labels, ["3Commas", "Derived (computed)"]);
  assert.notEqual(labels[0], labels[1]);
  assert.match(t("id", sourceKey("computed")!), /Turunan/);
  // A Binance mark has no source time, so the label says so instead of borrowing the book clock.
  assert.equal(derived.mark.sourceAt, null);
  assert.match(provenance(derived.mark, "en"), /Binance mark · source time not reported/);
  assert.equal(reported.mark.sourceAt, stamp);
});

test("a successful empty book is confirmed_empty and counts zero", () => {
  const book = bookView(read(payload([])), now);
  assert.equal(book.read.state, "confirmed_empty");
  assert.equal(book.complete, true);
  assert.equal(t("en", "noOpenPositions"), "No open positions on this account.");
  const accounts = accountsView(read(accountsPayload([account()])), now);
  assert.equal(scope(accounts, book, "", "A1").count("A1"), 0);
});

test("loading and unauthorized reads are their own states, never an empty book", () => {
  assert.equal(bookView(read(null), now).read.state, "loading");
  assert.equal(bookView(read(null, "unauthorized"), now).read.state, "unauthorized");
  assert.equal(bookView(read({}), now).read.state, "loading");
  const waiting = bookView(read(payload([], { as_of: null, error: "not_loaded" })), now);
  assert.equal(waiting.awaiting, true);
  assert.equal(waiting.brokerStale, false);
  assert.equal(waiting.complete, false);
});

test("Unassigned keeps every row when the accounts read fails, is selected by default, and hides no position", () => {
  const book = bookView(read(payload([row(), row({ id: "p2", account_code: null })], { by_account: { A1: 1, unknown: 1 } })), now);
  for (const failed of [read(null, "request_failed"), read({ accounts: [], as_of: stamp, error: "HTTP 500" }), read(null)]) {
    const accounts = accountsView(failed, now);
    const view = scope(accounts, book, "", "");
    assert.equal(view.unassigned.length, 2);
    assert.equal(view.current, UNASSIGNED);
    assert.equal(view.shown.length, 2);
    assert.equal(view.count("A1"), null);
  }
  const healthy = accountsView(read(accountsPayload([account()])), now);
  const split = scope(healthy, book, "", "A1");
  assert.equal(split.unassigned.length, 1);
  assert.equal(split.shown.length, 1);
  assert.equal(scope(healthy, book, UNASSIGNED, "A1").shown[0].id, "p2");
});

test("account switching filters positions by account and by_account must agree with the rows", () => {
  const accounts = accountsView(read(accountsPayload([account(), account({ code: "A2", name: "Beta", demo: false, badge: "REAL" })])), now);
  const book = bookView(read(payload([row(), row({ id: "p2", account_code: "A2" }), row({ id: "p3", account_code: "A2" })], {
    by_account: { A1: 1, A2: 2 },
  })), now);
  assert.equal(scope(accounts, book, "A2", "A1").shown.length, 2);
  assert.equal(scope(accounts, book, "A1", "A1").shown.length, 1);
  assert.equal(scope(accounts, book, "A2", "A1").count("A2"), 2);
  const disagree = bookView(read(payload([row()], { by_account: { A1: 5 } })), now);
  assert.equal(scope(accounts, disagree, "A1", "A1").count("A1"), null);
});

// An incomplete grid row: 3Commas reported a position count that disagrees with the rows, and fields are missing.
const gridRow = (extra: Record<string, unknown> = {}) => row({
  id: "g1", kind: "grid", bot_code: "G1", bot_name: "Grid One", owner: "external", side: null, average_price: null,
  size: null, cost: null, current_price: null, current_price_source: null, pnl_usd: null, pnl_usd_source: null,
  pnl_pct: null, pnl_pct_source: null, safety_used: null, safety_max: null, take_profits: [], stop_price: null, ...extra,
});

test("an incomplete grid bot renders partial with Not reported fields and its error stays visible", () => {
  const book = bookView(read(payload([gridRow(), gridRow({ id: "g2" }), row()], {
    errors: ["grid G1: incomplete position rows"], by_account: { A1: 3 },
  })), now);
  const [first, second, dca] = book.positions;
  assert.equal(first.incomplete, true);
  assert.equal(first.state, "partial");
  assert.equal(second.incomplete, true);
  assert.equal(dca.incomplete, false);
  for (const field of [first.size, first.cost, first.mark, first.pnlUsd, first.pnlPct, first.stop, first.averagePrice, first.takeProfits]) {
    assert.equal(field.value, null);
    assert.equal(field.state, "not_reported");
  }
  assert.equal(first.safety.value, null);
  assert.equal(book.complete, false);
  assert.equal(book.read.state, "partial");
  assert.deepEqual(book.errors.map((e) => [e.kind, e.botKind, e.botCode]), [["incomplete_rows", "grid", "G1"]]);
  const accounts = accountsView(read(accountsPayload([account()])), now);
  assert.equal(scope(accounts, book, "A1", "A1").count("A1"), null);
});

test("a bot code that merely resembles an incomplete bot is not marked", () => {
  const book = bookView(read(payload([gridRow({ bot_code: "G10" }), row({ kind: "dca", bot_code: "G1", id: "d1" })], {
    errors: ["grid G1: incomplete position rows"],
  })), now);
  assert.deepEqual(book.positions.map((p) => p.incomplete), [false, false]);
});

test("grid sub-rows with a repeated broker id are counted once and rows without an id stay partial", () => {
  const book = bookView(read(payload([gridRow(), gridRow(), gridRow({ id: null }), gridRow({ id: null })], {
    errors: ["grid G1: incomplete position rows"],
  })), now);
  assert.equal(book.duplicateRows, 1);
  assert.equal(book.positions.length, 3);
  assert.equal(book.positions.filter((p) => p.id === null).length, 2);
  assert.ok(book.positions.filter((p) => p.id === null).every((p) => p.rejected.includes("id") && p.state === "partial"));
  assert.equal(book.complete, false);
  const groups = botGroups(book.positions);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].rows, 3);
  assert.equal(groups[0].incomplete, true);
});

test("book errors are classified without altering the reported text", () => {
  assert.deepEqual(classifyBookError("position_read_cap").kind, "read_cap");
  assert.equal(classifyBookError("strategies: HTTP 429").kind, "strategies");
  assert.equal(classifyBookError("grid_bots: HTTP 500").kind, "list_failed");
  assert.equal(classifyBookError("dca My Bot: HTTP 500").kind, "read_failed");
  assert.equal(classifyBookError("dca B1: incomplete position rows").botCode, "B1");
  assert.equal(classifyBookError("surprise").raw, "surprise");
});

test("external bots are labelled external and a missing owner is never assumed to be WARP", () => {
  const rows = bookView(read(payload([row(), row({ id: "p2", owner: "warp" }), row({ id: "p3", owner: undefined })])), now).positions;
  assert.deepEqual(rows.map((p) => p.owner), ["external", "warp", null]);
  assert.equal(t("en", "readOnlyExternal"), "Read-only · external");
  assert.equal(t("id", "readOnlyExternal"), "Hanya baca · eksternal");
});

test("capital reports sleeve, budget gaps and a derived open-bot count with their provenance", () => {
  const accounts = accountsView(read(accountsPayload([account(), account({ code: "A2" })])), now);
  const bots = botsView(read({
    bots: [
      { account_code: "A1", status: "active" }, { account_code: "A1", status: "deleted" },
      { account_code: "A1", status: "paused" }, { account_code: "A2", status: "active" },
    ],
    budget: { sleeve_usdt: "500", sleeve_pct: "0.10" },
  }), now);
  const first = capital(accounts, accounts.accounts[0], bots, now);
  assert.equal(first.openBots.value, 2);
  assert.equal(first.openBots.source, "computed");
  assert.equal(first.sleeveConfigured.value, "500");
  assert.equal(first.sleeveConfigured.source, "worker");
  assert.equal(first.sleevePct.value, "0.10");
  assert.equal(first.sleeveEligible.value, "8");
  // The real payload publishes neither a cap nor used/remaining. They stay unreported.
  assert.equal(first.maxBots.value, null);
  assert.equal(first.budgetUsed.value, null);
  assert.equal(first.budgetRemaining.value, null);
  const second = capital(accounts, accounts.accounts[1], bots, now);
  assert.equal(second.sleeveEligible.value, null);
  assert.equal(second.sleeveEligible.reason, "not_selected_account");
  const none = botsView(read({ bots: [], budget: {} }), now);
  assert.equal(capital(accounts, accounts.accounts[0], none, now).openBots.state, "confirmed_empty");
  assert.equal(capital(accounts, accounts.accounts[0], botsView(read(null), now), now).openBots.state, "loading");
});

test("asset marks carry usd_reason, mark age and a stale flag above the 30 s tape limit", () => {
  const accounts = accountsView(read(accountsPayload([account({ assets: [
    { asset: "USDT", total: "5", usd: "5", usd_reason: null },
    { asset: "ETH", total: "1", usd: "2000", usd_reason: null },
    { asset: "SOL", total: "2", usd: null, usd_reason: "usd_mark_unavailable", quote_age_seconds: 45 },
    { asset: "ADA", total: "3", usd: null, usd_reason: "usd_mark_unavailable", quote_age_seconds: 12 },
    { asset: "DOT", total: "4", usd: null, usd_reason: "usd_mark_unavailable" },
  ] })])), now);
  const [usdt, eth, sol, ada, dot] = accounts.accounts[0].assets;
  assert.equal(usdt.usd.source, "3commas");
  assert.equal(eth.usd.source, "computed");
  assert.equal(eth.usd.sourceAt, null);
  assert.equal(eth.markAgeSeconds, null);
  assert.equal(sol.usd.value, null);
  assert.equal(sol.usdReason, "usd_mark_unavailable");
  assert.equal(sol.markAgeSeconds, 45);
  assert.equal(sol.markStale, true);
  assert.equal(ada.markStale, false);
  assert.equal(dot.markAgeSeconds, null);
  assert.equal(dot.markStale, false);
});

test("the three stale conditions are independent", () => {
  const accounts = accountsView(read(accountsPayload([account()])), now);
  const brokerOnly = bookView(read(payload([row()], { stale: true })), now);
  assert.equal(brokerOnly.brokerStale, true);
  assert.equal(accounts.stale, false);
  assert.equal(brokerOnly.positions[0].markState.stale, false);
  const accountOnly = accountsView(read(accountsPayload([account()], { as_of: old })), now);
  assert.equal(accountOnly.stale, true);
  assert.equal(bookView(read(payload([row()])), now).brokerStale, false);
  const markOnly = bookView(read(payload([row({ current_price: null, current_price_source: null })])), now);
  assert.equal(markOnly.brokerStale, false);
  assert.equal(markOnly.positions[0].markState.stale, true);
});

test("a future source clock beyond the P1 skew is not accepted as a source time", () => {
  const future = bookView(read(payload([row()], { as_of: "2026-11-24T10:05:00Z" })), now);
  assert.ok(future.brokerReasons.includes("no_source_time"));
  const skew = bookView(read(payload([row()], { as_of: "2026-11-24T10:00:05Z" })), now);
  assert.equal(skew.brokerStale, false);
});

test("a fresh book with five incomplete grid errors is partial and not stale (F25-1)", () => {
  const errors = ["G1", "G2", "G3", "G4", "G5"].map((code) => `grid ${code}: incomplete position rows`);
  const book = bookView(read(payload([gridRow(), gridRow({ id: "g2", bot_code: "G2" })], { errors, bots_checked: 38 })), now);
  assert.equal(book.brokerPartial, true);
  assert.equal(book.brokerStale, false);
  assert.deepEqual(book.brokerReasons, ["partial_read"]);
  assert.equal(book.errors.length, 5);
  assert.equal(book.complete, false);
  assert.equal(book.positions.length, 2);
  assert.equal(t("en", "brokerPartialBadge"), "Broker book partial");
  assert.equal(t("id", "brokerPartialBadge"), "Buku broker parsial");
  // Rows from a current book are not labelled stale because the book is partial.
  assert.notEqual(book.positions[0].averagePrice.state, "stale");
  assert.notEqual(book.positions[0].state, "stale");
});

test("stale and partial are independent: each alone, and both together", () => {
  const partialOnly = bookView(read(payload([row()], { errors: ["position_read_cap"] })), now);
  assert.deepEqual([partialOnly.brokerStale, partialOnly.brokerPartial], [false, true]);
  const bookError = bookView(read(payload([row()], { error: "HTTP 500" })), now);
  assert.deepEqual([bookError.brokerStale, bookError.brokerPartial], [false, true]);
  const staleOnly = bookView(read(payload([row()], { as_of: old })), now);
  assert.deepEqual([staleOnly.brokerStale, staleOnly.brokerPartial], [true, false]);
  const failed = bookView(read(payload([row()]), "request_failed"), now);
  assert.deepEqual([failed.brokerStale, failed.brokerPartial], [true, false]);
  const feed = bookView(read(payload([row()], { stale: true })), now);
  assert.deepEqual([feed.brokerStale, feed.brokerPartial], [true, false]);
  const both = bookView(read(payload([row()], { stale: true, errors: ["strategies: HTTP 429"] })), now);
  assert.deepEqual([both.brokerStale, both.brokerPartial], [true, true]);
  const clean = bookView(read(payload([row()])), now);
  assert.deepEqual([clean.brokerStale, clean.brokerPartial, clean.complete], [false, false, true]);
});
