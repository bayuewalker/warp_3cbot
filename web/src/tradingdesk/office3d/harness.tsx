/** Dev-only fixture page. The production bundle does not import this module. */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import type { Status } from "../../api.ts";
import { TradingRoom } from "../TradingRoom.tsx";
import "../room.css";
import "../../index.css";
import type { ObservedValue } from "../types.ts";

const params = new URLSearchParams(location.search);
const now = params.get("phase") === "night" ? Date.parse("2026-11-26T15:00:00Z") : Date.parse("2026-11-26T02:00:00Z");
// The room clock jumps forward when a fixture instant is already past the publication TTL.
Date.now = () => now;
localStorage.setItem("warp.room.motion", params.get("motion") === "0" ? "false" : "true");
localStorage.setItem("warp.room.text", "false");
localStorage.setItem("warp.room.view", params.get("view") === "2d" ? "2d" : "3d");
if (params.get("reduced") === "1") {
  window.matchMedia = ((query: string) => ({
    matches: query.includes("prefers-reduced-motion"), media: query, onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
    dispatchEvent() { return false; },
  })) as typeof window.matchMedia;
}

const published = new Date(now - 1000).toISOString();
const pose = params.get("pose") ?? "";
function observed(value: string, asleep = false): ObservedValue<string | null> {
  return {
    value, state: asleep ? "stale" : "confirmed", availability: asleep ? "unavailable" : "confirmed",
    freshness: asleep ? "stale" : "fresh", source: "worker",
    sourceAt: published, receivedAt: new Date(now).toISOString(), reason: null, traceId: null,
  };
}

const poseEvent = pose === "working"
  ? { event: "observe_candidate", ts: published, reason: "BTCUSDT crossed the book" }
  : pose === "fill"
    ? { event: "strategy_confirmed", ts: published }
    : pose === "alert"
      ? { event: "alert_failed", ts: published, alert_event: "telegram delivery rejected now" }
      : null;

const status = {
  alive: true, mode: pose === "fill" ? "paper" : "observe", reason: pose === "halt" ? "daily loss" : "ok",
  market_data_fresh: true, risk_permitted: pose !== "risk",
  execution_permitted: false, halt: pose === "halt", gating: "current", published_at: published,
  equity: null, pnl_today: null, universe: ["BTCUSDT"],
  pipeline: {
    screening: { state: "ready", source: "binance_spot_public", count: 12 },
    analysis: { state: "ready", count: 1, mode: "auto" },
    risk: { state: "pass", reason: "ok" },
    opportunities: { state: "ready", count: 1 },
  },
  research: {
    state: "ok", error_code: null, last_ok_at: null, last_failed_at: null,
    next_attempt_at: new Date(now + 60_000).toISOString(), consecutive_failures: 0, running: false,
  },
  providers: { budget: { calls_used: 4, calls_cap: 48, day: "2026-11-26", usd_used: "0", usd_cap: "2" } },
  xau: {
    enabled: true, state: "running", warming: false, window: { name: "London KZ", start_wib: "09:00", end_wib: "12:00" },
    engine: "SMC", last_bar_age_s: 40, basis_samples: 10, last_error: null,
    halt: { global: false, xau: false }, today: { signals_sent: 0 }, open_signal: null,
  },
} as unknown as Status;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TradingRoom
      lang="en"
      now={now}
      status={status}
      events={poseEvent ? [poseEvent] : [{ event: "worker_started", ts: published }]}
      agents={{
        SCAN: observed("ready", pose === "sleep"),
        ANALYST: observed("ready", pose === "sleep"),
        AEGIS: observed("pass", pose === "sleep"),
        EXECUTOR: observed("observe", pose === "sleep"),
        PORTFOLIO: { ...observed("reported", pose === "sleep"), source: "threecommas" },
        WATCHER: observed("reported", pose === "sleep"),
      }}
    />
  </StrictMode>,
);
