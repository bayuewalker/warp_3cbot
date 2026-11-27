import { test } from "node:test";
import assert from "node:assert/strict";
import { roomLife } from "./src/tradingdesk/room-life.ts";
import { lanePage, navIds, spotPostponed } from "./src/tradingdesk/room.ts";
import {
  agentCards, analysisChip, basisRead, chipLabel, deskRows, eventLabel, historyRows, isWarming,
  playbookLines, postponedShowsLog, preferenceTabs, stationFor, stripLine, tickerChip,
  xauAgents, xauCard, xauReason, xauTape,
} from "./src/tradingdesk/xau-lane.ts";
import { POSTPONED_TAB_ROUTES, payloadIssue } from "./src/tradingdesk/validate.ts";

const now = Date.parse("2026-11-27T07:30:00Z");
const at = new Date(now).toISOString();

function status(state: string, extra: Record<string, unknown> = {}) {
  return {
    lanes: { primary: "xau", spot: { state: "postponed", flag: "WARP_SPOT_LANE_ENABLED", since: at } },
    published_at: at,
    xau: { enabled: true, state, spot_at: at, cycle_seconds: 30, ...extra },
  };
}

test("xau card reports ready, warming, degraded, unavailable, and unknown", () => {
  assert.equal(xauCard(status("running", { ready: true, ready_reason: "ok" })).ready, true);
  assert.equal(xauCard(status("running", { ready: true, ready_reason: "ok" })).state, "running");
  assert.equal(xauCard(status("warming", { ready: false, ready_reason: "warming" })).state, "warming");
  assert.equal(xauCard(status("degraded", { ready: false, ready_reason: "dirty_basis" })).reason, "dirty_basis");
  assert.equal(xauCard(status("feed_unavailable", { ready_reason: "feed_unavailable" })).state, "feed_unavailable");
  assert.equal(xauCard({ lanes: { primary: "xau" } }).state, "unknown");
  assert.equal(xauCard(null).reason, "missing");
});

test("postponed is a lane state", () => {
  assert.equal(spotPostponed(status("running")), true);
  assert.equal(spotPostponed({ lanes: { primary: "xau", spot: { state: "active" } } }), false);
  assert.equal(spotPostponed(null), false);
});

test("postponed spot routes leave the nav and a direct hash returns to overview", () => {
  const bottom = navIds(true, "bottom");
  const rail = navIds(true, "rail");
  assert.ok(bottom.length <= 5);
  assert.ok(rail.length <= 5);
  for (const id of ["signals", "assets", "screener"]) {
    assert.equal(bottom.includes(id), false);
    assert.equal(rail.includes(id), false);
    assert.equal(lanePage(id, true), "overview");
  }
  assert.deepEqual(bottom, ["overview", "preferences"]);
  assert.deepEqual(rail, ["overview", "desk", "preferences"]);
  assert.equal(lanePage("desk", true), "desk");
  assert.equal(lanePage("overview", true), "overview");
});

test("an active spot lane restores the main nav", () => {
  assert.deepEqual(navIds(false, "bottom"), ["overview", "signals", "assets", "screener", "preferences"]);
  assert.equal(navIds(false, "bottom").length, 5);
  assert.deepEqual(navIds(false, "rail"), ["overview", "desk", "signals", "assets", "screener", "preferences"]);
  assert.equal(lanePage("signals", false), "signals");
  assert.equal(lanePage("assets", false), "assets");
  assert.equal(lanePage("screener", false), "screener");
});

test("market tape defaults to XAUUSD and says unavailable without a spot", () => {
  const live = xauTape(status("running", { spot_last: "4088.2", spot_at: at, ready_reason: "ok" }), now);
  assert.equal(live.symbol, "XAUUSD");
  assert.equal(live.last, "4088.2");
  assert.equal(live.fresh, true);
  assert.equal(live.source, "OANDA:XAUUSD via TradingView (spot)");
  const stale = xauTape(status("running", { spot_last: "4088.2", spot_at: at }), now + 120_000);
  assert.equal(stale.fresh, false);
  assert.equal(stale.state, "stale");
  const closed = xauTape(status("running", { spot_last: "4088.2", spot_at: at, spot_state: "market_closed" }), now);
  assert.equal(closed.fresh, false);
  assert.equal(closed.state, "market_closed");
  const missing = xauTape(status("feed_unavailable", { ready_reason: "feed" }), now);
  assert.equal(missing.last, null);
  assert.equal(missing.active, true);
  assert.equal(missing.reason, "feed");
});

test("the six desks read XAU roles", () => {
  const row = status("running", {
    ready: true, ready_reason: "ok", spot_last: "4088.2", basis: "0.40", feed: "binance",
    engine: "SMC", window: { name: "London KZ" },
    open_signal: { direction: "long", entry: "4080", sl: "4070", tp1: "4100", tp2: "4120", grade: "A+" },
    today: { signals_sent: 1, journal_only: 2, realized_r: "0.5" },
    last_signal_at: at,
  });
  const seats = xauAgents(row, now);
  assert.match(String(seats.SCAN.value), /4088.2/);
  assert.match(String(seats.ANALYST.value), /SMC/);
  assert.equal(seats.AEGIS.value, "pass");
  assert.match(String(seats.EXECUTOR.value), /long/);
  assert.match(String(seats.PORTFOLIO.value), /0.5/);
  assert.equal(seats.WATCHER.value, at);
  const life = roomLife({ agents: {}, status: row, events: [{ event: "XAU BUY LIMIT A+ #ABC123", ts: at }], now, radar: false });
  assert.equal(life.stations.SCAN.bubble, null);
  assert.equal(life.stations.ANALYST.bubble, null);
  assert.equal(life.stations.EXECUTOR.stamp, false);
  assert.equal(life.stations.EXECUTOR.bubble, null);
  assert.match(life.stations.WATCHER.bubble ?? "", /^XAU BUY/);
  const warming = roomLife({ agents: {}, status: status("warming", { ready_reason: "warming", feed: "okx", today: { realized_r: "0" } }), events: [], now, radar: false });
  assert.equal(warming.stations.AEGIS.mood, "idle");
  assert.equal(warming.stations.AEGIS.bubble, null);
  assert.equal(warming.stations.PORTFOLIO.mood, "idle");
  assert.equal(warming.stations.PORTFOLIO.bubble, null);
  const down = roomLife({ agents: {}, status: status("feed_unavailable", { ready_reason: "feed_unavailable" }), events: [], now, radar: false });
  assert.equal(down.stations.AEGIS.mood, "alert");
  const filled = roomLife({ agents: {}, status: row, events: [{ event: "XAU FILLED #ABC123", ts: at }], now, radar: false });
  assert.equal(filled.stations.EXECUTOR.stamp, true);
  assert.equal(xauReason("en", "warming_up"), "Warming up");
  assert.equal(xauReason("id", "dirty_basis"), "Basis belum siap");
  assert.equal(xauReason("en", "gate_fail:C5"), "Gate C5");
});

test("signals tab treats a postponed payload as a lane, not a failed read", () => {
  for (const path of POSTPONED_TAB_ROUTES.signals) assert.equal(payloadIssue(path, { state: "postponed" }), null, path);
});

test("bots inventory tab treats a postponed payload as a lane, not a failed read", () => {
  for (const path of POSTPONED_TAB_ROUTES.manage) assert.equal(payloadIssue(path, { state: "postponed" }), null, path);
});

test("bots positions tab treats a postponed payload as a lane, not a failed read", () => {
  for (const path of POSTPONED_TAB_ROUTES.positions) assert.equal(payloadIssue(path, { state: "postponed" }), null, path);
});

test("bots preset tab treats a postponed payload as a lane, not a failed read", () => {
  for (const path of POSTPONED_TAB_ROUTES.template) assert.equal(payloadIssue(path, { state: "postponed" }), null, path);
});

test("screener tab treats a postponed payload as a lane, not a failed read", () => {
  for (const path of POSTPONED_TAB_ROUTES.screener) assert.equal(payloadIssue(path, { state: "postponed" }), null, path);
});

const moods = { SCAN: "working", ANALYST: "working", AEGIS: "idle", EXECUTOR: "idle", PORTFOLIO: "idle", WATCHER: "idle" } as const;

test("basis stays a value after warm-up even when the funnel still says warming_up", () => {
  const row = status("running", {
    ready: true, ready_reason: "ok", warming: false, basis: "1.20", basis_value: "1.20",
    basis_healthy: true, basis_samples: 15,
    funnel: { recent: [{ reason: "warming_up", count: 2 }] },
  });
  assert.equal(isWarming(row), false);
  assert.equal(basisRead("en", row).text, "1.20 · Healthy");
  assert.equal(basisRead("id", row).text, "1.20 · Sehat");
  assert.equal(basisRead("en", row).bar, null);
  assert.equal(analysisChip(row), "waiting");
  assert.equal(chipLabel("en", "waiting"), "Waiting for a setup");
  assert.equal(chipLabel("en", "waiting").includes("Ready:"), false);
});

test("warming, closed, stale, halted, and an open signal each have one chip", () => {
  const warming = status("warming", { warming: true, ready_reason: "warming", basis_samples: 4 });
  assert.equal(basisRead("en", warming).text, "Warming up (4/10)");
  assert.equal(analysisChip(warming), "warming");
  const closed = status("market_closed", { spot_state: "market_closed", spot_last: "2300" });
  assert.equal(analysisChip(closed), "closed");
  assert.equal(tickerChip("en", closed, now).id, "closed");
  const stale = status("running", { spot_last: "2300", spot_at: "2026-11-27T07:00:00Z", spot_state: "live" });
  assert.equal(tickerChip("en", stale, now).id, "stale");
  const halted = status("running", { halt: { global: true, xau: false } });
  assert.equal(analysisChip(halted), "halted");
  const open = status("running", {
    ready: true, ready_reason: "ok", spot_last: "2300", spot_state: "live",
    open_signal: { direction: "long", entry: "2301", sl: "2290", tp1: "2320", tp2: null, grade: "A" },
  });
  assert.equal(analysisChip(open), "open");
  const cards = agentCards("en", open, now, moods, 0);
  assert.equal(cards.find(item => item.role === "EXECUTOR")?.status.includes("long"), true);
  assert.equal(cards.find(item => item.role === "EXECUTOR")?.value, "A");
  const empty = agentCards("en", status("running", { ready: true, ready_reason: "ok" }), now, moods, null);
  assert.equal(empty.find(item => item.role === "EXECUTOR")?.status, "No open signal");
  assert.equal(empty.find(item => item.role === "SCAN")?.value, "0s");
  const quiet = agentCards("en", { lanes: { primary: "xau" }, xau: { ready: true, ready_reason: "ok" } }, now, moods, null);
  assert.equal(quiet.find(item => item.role === "SCAN")?.value, "Not reported");
});

test("a missing basis is unavailable in both languages", () => {
  const row = status("running", { ready: true, ready_reason: "ok", basis: null, basis_value: null });
  assert.equal(basisRead("en", row).text, "Unavailable");
  assert.equal(basisRead("id", row).text, "Tidak tersedia");
  assert.equal(historyRows("en", row), null);
  const brief = historyRows("en", status("running", { recent: [{ direction: "long", entry: "2301" }] }));
  assert.equal(brief?.[0].time, "Not reported");
  assert.equal(brief?.[0].result, "Not reported");
  assert.equal(brief?.[0].direction, "long");
  assert.equal(playbookLines("en", row), null);
  assert.match(stripLine("en", status("running", { ready: true, ready_reason: "ok", today: { signals_sent: 0 } })), /0 signals today/);
  assert.match(stripLine("en", row), /W\/L not reported/);
});

test("desk log maps events to stations and filters them", () => {
  assert.equal(stationFor("market_data_blocked"), "SCAN");
  assert.equal(stationFor("halt"), "AEGIS");
  assert.equal(stationFor("XAU BUY GOLD"), "EXECUTOR");
  assert.equal(stationFor("XAU cycle note"), "WATCHER");
  assert.equal(eventLabel("en", "alert_skipped"), "Alert skipped");
  assert.equal(eventLabel("en", "bad_json"), "Unreadable message");
  assert.equal(eventLabel("en", "mystery_event"), "Logged event");
  const rows = [
    { event: "halt", ts: "2026-11-27T07:31:00Z" },
    { event: "market_data_blocked", ts: "2026-11-27T07:32:00Z" },
  ];
  assert.equal(deskRows("en", rows, "SCAN").length, 1);
  assert.equal(deskRows("en", rows, "SCAN")[0].label, "Market data blocked");
  assert.equal(deskRows("id", rows, "all")[0].label, "Data pasar terblokir");
  assert.equal(deskRows("en", rows, "PORTFOLIO").length, 0);
});

test("postponed settings hide the spot panels and an active lane restores them", () => {
  assert.deepEqual(preferenceTabs(true), []);
  assert.equal(postponedShowsLog("activity"), true);
  assert.equal(postponedShowsLog("venues"), false);
  assert.deepEqual(preferenceTabs(false), ["venues", "rules", "configuration", "activity", "risk"]);
  assert.ok(navIds(true, "bottom").length <= 5);
  assert.deepEqual(navIds(false, "bottom"), ["overview", "signals", "assets", "screener", "preferences"]);
});
