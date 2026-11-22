/** Compare publication and evidence clocks separately. A failed refresh can carry older retained evidence. */
export type Frame = {
  data: unknown;
  at: number;
  publication: number | null;
  evidence: number | null;
};

export function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function instant(value: unknown): number | null {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(value)) return null;
  const date = value.slice(0, 10);
  const day = new Date(date + "T00:00:00Z");
  if (!Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== date || Number(value.slice(11, 13)) > 23) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

// Python producers emit microseconds. Preserve sub-millisecond ordering at equal JS millisecond times.
function order(value: unknown, now: number) {
  const ms = instant(value);
  if (ms === null || ms > now) return null;
  const fraction = /\.(\d+)/.exec(String(value))?.[1] ?? "";
  return ms * 1000 + Number(fraction.padEnd(6, "0").slice(3, 6));
}

const evidenceField: Record<string, string> = {
  "/api/status": "published_at",
  "/api/accounts": "as_of",
  "/api/account-positions": "as_of",
  "/api/positions": "as_of",
  "/api/events": "as_of",
  "/api/prices": "as_of",
  "/api/screener": "fetched_at",
};

export function acceptFrame(previous: Frame | undefined, path: string, data: unknown, at: number, published?: unknown): Frame {
  const publication = order(published, at);
  if (previous && publication !== null && previous.publication !== null && publication < previous.publication) return previous;
  const degraded = record(data) && (data.error || data.stale || (Array.isArray(data.errors) && data.errors.length > 0));
  // Accounts and legacy positions timestamp failed attempts too. The combined
  // book and price feeds can instead step back to their last good source time.
  const attemptClock = path === "/api/accounts" || path === "/api/positions";
  const evidence = record(data) && (!degraded || attemptClock) ? order(data[evidenceField[path]], at) : null;
  const nextPublication = publication ?? previous?.publication ?? null;
  const olderEvidence = previous && evidence !== null && previous.evidence !== null && evidence < previous.evidence;
  if (previous && (olderEvidence || JSON.stringify(previous.data) === JSON.stringify(data))) {
    return nextPublication === previous.publication ? previous : { ...previous, publication: nextPublication };
  }
  return { data, at, publication: nextPublication, evidence: evidence ?? previous?.evidence ?? null };
}
