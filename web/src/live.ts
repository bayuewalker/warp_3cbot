/** One authenticated event stream for every panel. Polling is the fallback. */

type Frame = { data: unknown; at: number };

const routes = new Map<string, Frame>();
const listeners = new Set<() => void>();
let source: EventSource | null = null;
let attempt = 0;
let connected = false;
let timer: number | undefined;
let started = false;

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

export function liveConnected() {
  return connected;
}

export function ensureLive() {
  if (started) return;
  started = true;
  connect();
}

function connect() {
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
    const data = JSON.parse((ev as MessageEvent).data) as { halt?: boolean; gating?: string };
    const status = routes.get("/api/status");
    if (status && status.data && typeof status.data === "object") {
      routes.set("/api/status", {
        data: { ...(status.data as object), halt: Boolean(data.halt), gating: data.gating },
        at: Date.now(),
      });
      notify();
    }
  });
  source.onerror = () => {
    connected = false;
    source?.close();
    source = null;
    notify();
    const wait = Math.min(30000, 1000 * 2 ** attempt);
    attempt += 1;
    timer = window.setTimeout(() => {
      timer = undefined;
      connect();
    }, wait);
  };
}
