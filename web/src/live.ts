/** One authenticated event stream for every panel. Polling is the fallback. */

type Frame = { data: unknown; at: number };

const routes = new Map<string, Frame>();
const listeners = new Set<() => void>();
let source: EventSource | null = null;
let attempt = 0;
let connected = false;
let timer: number | undefined;
let started = false;
let stopped = false;

function notify() {
  listeners.forEach((fn) => fn());
}

export function onLive(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function liveRoute<T>(path: string): { data: T; at: number } | null {
  const frame = routes.get(path);
  return frame ? { data: frame.data as T, at: frame.at } : null;
}

export function rememberRoute(path: string, data: unknown) {
  routes.set(path, { data, at: Date.now() });
}

export function liveConnected() {
  return connected;
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
  source = new EventSource("/api/stream");
  source.addEventListener("hello", () => {
    connected = true;
    attempt = 0;
    notify();
  });
  source.addEventListener("heartbeat", () => {
    connected = true;
    notify();
  });
  source.addEventListener("snapshot", (ev) => {
    const body = JSON.parse((ev as MessageEvent).data) as {
      routes?: Record<string, unknown>;
      published_at?: string;
    };
    const at = Date.parse(body.published_at || "") || Date.now();
    for (const [path, data] of Object.entries(body.routes || {})) routes.set(path, { data, at });
    connected = true;
    attempt = 0;
    notify();
  });
  source.addEventListener("prices", (ev) => {
    const data = JSON.parse((ev as MessageEvent).data) as { as_of?: string | null };
    routes.set("/api/prices", { data, at: Date.parse(data.as_of || "") || Date.now() });
    connected = true;
    notify();
  });
  source.addEventListener("halt", (ev) => {
    const data = JSON.parse((ev as MessageEvent).data) as {
      halt?: boolean;
      gating?: string;
      execution_permitted?: boolean;
      risk_permitted?: boolean;
      pipeline?: unknown;
      reason?: string;
    };
    const status = routes.get("/api/status");
    if (status && status.data && typeof status.data === "object") {
      const next = { ...(status.data as object), ...data };
      if (data.halt) {
        next.execution_permitted = false;
        next.risk_permitted = false;
      }
      routes.set("/api/status", { data: next, at: Date.now() });
      notify();
    }
  });
  source.onerror = () => {
    if (stopped) return;
    connected = false;
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
