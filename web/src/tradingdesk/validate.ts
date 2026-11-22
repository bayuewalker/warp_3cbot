import { record } from "./frames.ts";
import type { ReadIssue } from "./types";

const lists: Record<string, string> = {
  "/api/accounts": "accounts", "/api/account-positions": "positions", "/api/positions": "positions",
  "/api/warp-bots": "bots", "/api/events": "events", "/api/opportunities": "cards",
  "/api/decisions": "decisions", "/api/screener": "hits", "/api/alerts": "alerts",
};

export function payloadIssue(path: string, data: unknown): ReadIssue | null {
  if (!record(data)) return "malformed_payload";
  if (!Object.keys(data).length) return "awaiting_first_snapshot";
  const field = lists[path];
  if (field && (!Array.isArray(data[field]) || !data[field].every(record))) return "malformed_payload";
  if (path === "/api/status") {
    if (typeof data.mode !== "string") return "malformed_payload";
    if (data.universe != null && (!Array.isArray(data.universe) || !data.universe.every(s => typeof s === "string"))) return "malformed_payload";
    if (data.pipeline != null && (!record(data.pipeline) || !Object.values(data.pipeline).every(stage =>
      record(stage) && typeof stage.state === "string" && (stage.reason == null || typeof stage.reason === "string")
    ))) return "malformed_payload";
    if (data.providers != null && (!record(data.providers) || !Object.values(data.providers).every(provider =>
      record(provider) && (provider.state == null || typeof provider.state === "string")
    ))) return "malformed_payload";
  }
  if (path === "/api/account-positions" && Array.isArray(data.positions) &&
    !data.positions.every(row => record(row) && Array.isArray(row.take_profits))) return "malformed_payload";
  if (path === "/api/events" && Array.isArray(data.events) &&
    !data.events.every(event => record(event) && typeof event.event === "string")) return "malformed_payload";
  if (path === "/api/prices" && (!record(data.symbols) || !Object.values(data.symbols).every(record))) return "malformed_payload";
  return null;
}
