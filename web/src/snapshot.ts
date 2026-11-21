import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { ensureLive, liveConnected, liveRoute, onLive } from "./live";

/** Prefer the live snapshot. Poll only while the stream is down. */
export function useLoad<T>(path: string, poll = false) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState(false);
  const [received, setReceived] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((value) => value + 1), []);
  useEffect(() => {
    return onLive(() => {
    const frame = liveRoute<T>(path);
    if (frame) {
      setData(frame.data);
      setError(false);
      setReceived(new Date(frame.at));
    } else if (poll && !liveConnected()) {
      setTick((value) => value + 1);
    }
    });
  }, [path, poll]);
  useEffect(() => {
    ensureLive();
    const controller = new AbortController();
    let timer: number | undefined;
    async function read() {
      const frame = liveRoute<T>(path);
      if (frame && !controller.signal.aborted) {
        setData(frame.data);
        setError(false);
        setReceived(new Date(frame.at));
      }
      try {
        const row = await api<T>(path, { signal: controller.signal });
        if (!controller.signal.aborted && !liveRoute<T>(path)) {
          setData(row);
          setError(false);
          setReceived(new Date());
        }
      } catch {
        if (!controller.signal.aborted && !liveRoute<T>(path)) setError(true);
      } finally {
        if (poll && !liveConnected() && !controller.signal.aborted) timer = window.setTimeout(read, 5000);
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

export function useLiveState() {
  const [on, setOn] = useState(liveConnected());
  useEffect(() => {
    return onLive(() => setOn(liveConnected()));
  }, []);
  return on;
}
