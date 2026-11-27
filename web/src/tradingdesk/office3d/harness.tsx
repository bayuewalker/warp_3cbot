/** Dev-only fixture page. The production bundle does not import this module. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import type { Status } from "../../api.ts";
import { TradingRoom } from "../TradingRoom.tsx";
import "../room.css";
import "../../index.css";
import type { ObservedValue } from "../types.ts";

const params = new URLSearchParams(location.search);
const now = params.get("phase") === "night" ? Date.parse("2026-11-26T15:00:00Z") : Date.parse("2026-11-26T02:00:00Z");
// The room clock jumps forward when a fixture instant is already past the publication TTL.
// act=walk lets Date.now advance from that instant so a signal walk can glide.
if (params.get("act") === "walk") {
  const origin = performance.now();
  Date.now = () => now + (performance.now() - origin);
} else {
  Date.now = () => now;
}
localStorage.setItem("warp.room.motion", params.get("motion") === "0" ? "false" : "true");
localStorage.setItem("warp.room.text", "false");
localStorage.setItem("warp.room.view", params.get("view") === "2d" ? "2d" : "3d");

const published = new Date(now - 1000).toISOString();
const alert = params.get("alert") === "1";
const fixtures: Record<string, unknown> = {
  "/api/alerts": {
    alerts: alert ? [{ id: 9, event: "halt", state: "pending", last_error: "no channel", attempts: 2, created_at: published }] : [],
  },
  "/api/forward": { scored: 4, pending: 2 },
  "/api/breakers": { halt: false, risk_permitted: true, reason: "ok" },
  "/api/prices": {
    symbols: { BTCUSDT: { last: "64000", change_pct: "0.4" } },
    as_of: published,
    limit_seconds: 60,
  },
};
const realFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const path = url.startsWith("/") ? url.split("?")[0] : "";
  const body = fixtures[path];
  if (body == null) return realFetch(input, init);
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
};
class FixtureSource extends EventTarget {
  onerror: ((this: EventSource, ev: Event) => unknown) | null = null;
  constructor(url: string) {
    super();
    void url;
    queueMicrotask(() => {
      this.dispatchEvent(new MessageEvent("hello", { data: JSON.stringify({ ts: new Date(now).toISOString() }) }));
    });
  }
  close() {}
}
window.EventSource = FixtureSource as unknown as typeof EventSource;
function observed(value: string): ObservedValue<string | null> {
  return {
    value, state: "confirmed", availability: "confirmed", freshness: "fresh", source: "worker",
    sourceAt: published, receivedAt: new Date(now).toISOString(), reason: null, traceId: null,
  };
}

const status = {
  alive: true, mode: "observe", reason: "ok", market_data_fresh: true, risk_permitted: true,
  execution_permitted: false, halt: false, gating: "current", published_at: published,
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
};

function HarnessRoom() {
  const [sent, setSent] = useState(0);
  useEffect(() => {
    if (params.get("act") !== "walk") return;
    const timer = window.setTimeout(() => setSent(1), 400);
    return () => window.clearTimeout(timer);
  }, []);
  const roomStatus = {
    ...status,
    xau: { ...status.xau, today: { signals_sent: sent } },
  } as unknown as Status;
  return <TradingRoom
    lang="en"
    now={now}
    status={roomStatus}
    events={[{ event: "worker_started", ts: published }]}
    agents={{
      SCAN: observed("ready"),
      ANALYST: observed("ready"),
      AEGIS: observed("pass"),
      EXECUTOR: observed("observe"),
      PORTFOLIO: { ...observed("reported"), source: "threecommas" },
      WATCHER: observed("reported"),
    }}
  />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HarnessRoom />
  </StrictMode>,
);
