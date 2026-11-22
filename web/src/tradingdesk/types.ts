export type ReadIssue = "awaiting_first_snapshot" | "malformed_payload" | "request_failed" | "unauthorized";
export type ReadInput = { data: unknown; received: Date | null; issue: ReadIssue | null };
export type ReadState = "loading" | "awaiting_first_snapshot" | "confirmed" | "confirmed_empty" |
  "partial" | "stale" | "unavailable" | "error" | "unauthorized";
export type ObservedValue<T> = {
  value: T | null;
  availability: "confirmed" | "partial" | "unavailable";
  freshness: "fresh" | "stale" | "unknown";
  state: ReadState;
  source: "worker" | "derived" | "threecommas" | "binance";
  sourceAt: string | null;
  receivedAt: string | null;
  reason: string | null;
  traceId: string | null;
};
