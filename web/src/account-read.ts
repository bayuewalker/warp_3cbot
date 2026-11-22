import { useEffect, useState } from "react";

/** Expire a broker read even when the stream keeps sending unrelated heartbeats. */
export function useCurrentRead(at: string | null | undefined, ttlSeconds: number) {
  const stamp = Date.parse(at ?? "");
  const deadline = stamp + ttlSeconds * 1000;
  const [, reevaluate] = useState(0);
  useEffect(() => {
    const remaining = deadline - Date.now();
    if (remaining >= 0 && stamp <= Date.now()) {
      const timer = window.setTimeout(() => reevaluate((value) => value + 1), remaining + 1);
      return () => window.clearTimeout(timer);
    }
  }, [stamp, deadline]);
  const now = Date.now();
  return stamp <= now && now <= deadline;
}
