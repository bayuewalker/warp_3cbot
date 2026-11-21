import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ensureLive, liveConnected, liveRoute, onLive, rememberRoute } from "./live";
import { failureVisible, liveReadDecision } from "./live-policy";

/** Prefer the live snapshot. Poll only while the stream is down. */
export function useLoad<T>(path: string, poll = false) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState(false);
  const [received, setReceived] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const showing = useRef(false);
  const seenPath = useRef(path);
  const reload = useCallback(() => setTick((value) => value + 1), []);
  const publish = useCallback((row: T, at: Date) => {
    showing.current = true;
    setData(row);
    setError(false);
    setReceived(at);
  }, []);
  useEffect(() => {
    return onLive(() => {
      const decision = liveReadDecision(liveConnected(), liveRoute<T>(path) != null, poll);
      if (decision.startPoll) setTick((value) => value + 1);
      if (!decision.applyCachedFrame) return;
      const frame = liveRoute<T>(path);
      if (!frame) return;
      publish(frame.data, new Date(frame.at));
    });
  }, [path, poll, publish]);
  useEffect(() => {
    if (seenPath.current !== path) {
      seenPath.current = path;
      showing.current = false;
    }
    ensureLive();
    const controller = new AbortController();
    let timer: number | undefined;
    async function read() {
      const decision = liveReadDecision(liveConnected(), liveRoute<T>(path) != null, poll);
      const frame = liveRoute<T>(path);
      if (decision.applyCachedFrame && frame && !controller.signal.aborted) {
        publish(frame.data, new Date(frame.at));
      }
      try {
        const row = await api<T>(path, { signal: controller.signal });
        const next = liveReadDecision(liveConnected(), liveRoute<T>(path) != null, poll);
        if (!controller.signal.aborted && next.acceptHttp) {
          publish(row, new Date());
          if (!liveConnected()) rememberRoute(path, row);
        }
      } catch {
        if (
          failureVisible(
            liveConnected(),
            liveRoute<T>(path) != null,
            poll,
            controller.signal.aborted,
            showing.current,
          )
        ) {
          setError(true);
        }
      } finally {
        const again = liveReadDecision(liveConnected(), liveRoute<T>(path) != null, poll);
        if (again.startPoll && !controller.signal.aborted) timer = window.setTimeout(read, 5000);
      }
    }
    void read();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [path, tick, poll, publish]);
  return { data, error, received, reload };
}

export function useLiveState() {
  const [on, setOn] = useState(liveConnected());
  useEffect(() => {
    return onLive(() => setOn(liveConnected()));
  }, []);
  return on;
}
