import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

/** Poll monitoring views only. An operator's editable forms keep their draft. */
export function useLoad<T>(path: string, poll = false) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState(false);
  const [received, setReceived] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((value) => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    async function read() {
      try {
        const row = await api<T>(path, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setData(row);
          setError(false);
          setReceived(new Date());
        }
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (poll && !controller.signal.aborted) timer = window.setTimeout(read, 30000);
      }
    }
    void read();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [path, tick, poll]);
  return { data, error, received, reload };
}
