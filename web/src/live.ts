/** One authenticated event stream for every panel. Polling is the fallback. */

import { acceptFrame, instant, record, type Frame } from "./tradingdesk/frames";

const routes = new Map<string, Frame>();
const listeners = new Set<(paths?: readonly string[]) => void>();
let source: EventSource | null = null;
let attempt = 0;
let connected = false;
let lastFrameAt: number | null = null;
let downSince: number | null = Date.now();
let timer: number | undefined;
let started = false;
let stopped = false;

function notify(paths?: readonly string[]) {
  listeners.forEach((fn) => fn(paths));
}

export function onLive(fn: (paths?: readonly string[]) => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function liveRoute<T>(path: string) {
  return routes.get(path) as (Frame & { data: T }) | undefined;
}

export function rememberRoute<T>(path: string, data: T, published?: unknown) {
  const frame = acceptFrame(routes.get(path), path, data, Date.now(), published);
  routes.set(path, frame);
  return frame as Frame & { data: T };
}

export function liveConnected() {
  return connected;
}

/** Receipt time of the latest valid stream frame, heartbeats included. */
export function liveLastFrameAt() {
  return lastFrameAt;
}

/** When the stream was first seen down; null while connected. Survives the views that read it. */
export function liveDownSince() {
  return connected ? null : downSince;
}

export function ensureLive() {
  if (started) return;
  stopped = false;
  started = true;
  connect();
}

export function stopLive() {
  stopped = true;
  started = false;
  connected = false;
  downSince ??= Date.now();
  lastFrameAt = null;
  attempt = 0;
  if (timer !== undefined) {
    window.clearTimeout(timer);
    timer = undefined;
  }
  source?.close();
  source = null;
  routes.clear();
  notify();
}

function connect() {
  if (stopped) return;
  const stream = new EventSource("/api/stream");
  source = stream;
  function listen(event: string, apply: (body: Record<string, unknown>) => string[] | undefined) {
    stream.addEventListener(event, (ev) => {
      if (source !== stream) return;
      let body: unknown;
      try {
        body = JSON.parse((ev as MessageEvent).data);
      } catch {
        return;
      }
      if (!record(body)) return;
      const updated = apply(body);
      if (!updated) return;
      lastFrameAt = Date.now();
      if (event === "hello" || event === "heartbeat") {
        connected = true;
        downSince = null;
        attempt = 0;
      }
      notify(updated);
    });
  }
  const handshake = (body: Record<string, unknown>) => instant(body.ts) !== null ? [] : undefined;
  listen("hello", handshake);
  listen("heartbeat", handshake);
  listen("snapshot", (body) => {
    if (!record(body.routes)) return;
    const changed: string[] = [];
    for (const [path, data] of Object.entries(body.routes)) {
      const previous = routes.get(path);
      const frame = rememberRoute(path, data, body.published_at);
      if (frame !== previous) changed.push(path);
    }
    return changed;
  });
  for (const [name, path] of [["prices", "/api/prices"], ["accounts", "/api/accounts"], ["positions", "/api/account-positions"]]) {
    listen(name, data => {
      const previous = routes.get(path);
      return rememberRoute(path, data) !== previous ? [path] : [];
    });
  }
  listen("halt", (data) => {
    const previous = routes.get("/api/status");
    if (!previous || !record(previous.data) || typeof data.halt !== "boolean") return;
    const next = { ...previous.data, ...data };
    if (data.halt) {
      next.execution_permitted = false;
      next.risk_permitted = false;
    }
    rememberRoute("/api/status", next);
    return ["/api/status"];
  });
  stream.onerror = () => {
    if (source !== stream || stopped) return;
    connected = false;
    downSince ??= Date.now();
    source?.close();
    source = null;
    notify();
    const wait = Math.min(30000, 1000 * 2 ** attempt);
    attempt += 1;
    timer = window.setTimeout(() => {
      timer = undefined;
      if (!stopped) connect();
    }, wait);
  };
}
