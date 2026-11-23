import { useCallback, useEffect, useRef, useState } from "react";
import { api, AuthError } from "./api";
import { ensureLive, liveConnected, liveRoute, onLive, rememberRoute } from "./live";
import { failureVisible, liveReadDecision } from "./live-policy";
import { payloadIssue } from "./tradingdesk/validate";
import type { ReadIssue } from "./tradingdesk/types";

/** Monitoring reads share one authenticated stream. Form reads always use HTTP. */
export function useLoad<T>(path: string, poll = false) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState(false);
  const [issue, setIssue] = useState<ReadIssue | null>(null);
  const [received, setReceived] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const showing = useRef(false);
  const failedHttp = useRef(false);
  const seenPath = useRef(path);
  const applied = useRef<{ data: T; at: number } | null>(null);
  const reload = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    if (seenPath.current !== path) {
      seenPath.current = path;
      showing.current = false;
      failedHttp.current = false;
      applied.current = null;
      setData(null);
      setError(false);
      setIssue(null);
      setReceived(null);
    }
    const controller = new AbortController();
    let timer: number | undefined;
    function publish(frame: { data: T; at: number }, http = false) {
      if (!http && applied.current?.data === frame.data && applied.current?.at === frame.at) return;
      failedHttp.current = false;
      applied.current = frame;
      const problem = payloadIssue(path, frame.data);
      showing.current = !problem;
      setData(problem ? null : frame.data);
      setReceived(new Date(frame.at));
      setIssue(problem);
      setError(Boolean(problem));
    }
    let wasConnected = liveConnected();
    function streamChanged(paths?: readonly string[]) {
      const connected = liveConnected();
      if (!connected && wasConnected) reload();
      const reconnected = connected && !wasConnected;
      wasConnected = connected;
      if (!reconnected && !paths?.includes(path)) return;
      const frame = liveRoute<T>(path);
      if (connected && frame) publish(frame, failedHttp.current && Boolean(paths?.includes(path)));
    }
    const unsubscribe = poll ? onLive(streamChanged) : undefined;
    if (poll) ensureLive();

    async function read() {
      const frame = liveRoute<T>(path);
      const decision = liveReadDecision(liveConnected(), !!frame, poll);
      if (decision.applyCachedFrame && frame) publish(frame);
      try {
        const row = await api<T>(path, { signal: controller.signal });
        if (controller.signal.aborted) return;
        const next = liveReadDecision(liveConnected(), !!liveRoute(path), poll);
        if (next.acceptHttp) publish(poll ? rememberRoute(path, row) : { data: row, at: Date.now() }, true);
      } catch (exc) {
        if (controller.signal.aborted) return;
        const hasFrame = !!liveRoute(path);
        if (exc instanceof AuthError) {
          showing.current = false;
          setData(null);
          setError(true);
          setIssue("unauthorized");
        } else if (liveReadDecision(liveConnected(), hasFrame, poll).acceptHttp) {
          failedHttp.current = true;
          const awaiting = exc instanceof Error && exc.message === "snapshot_unavailable";
          const malformed = exc instanceof SyntaxError || (exc instanceof Error && exc.message === "malformed_payload");
          setIssue(awaiting ? "awaiting_first_snapshot" : malformed ? "malformed_payload" : "request_failed");
          setError(failureVisible(liveConnected(), hasFrame, poll, false, showing.current));
        }
      } finally {
        if (poll && !liveConnected() && !controller.signal.aborted) timer = window.setTimeout(read, 5000);
      }
    }
    void read();
    return () => {
      unsubscribe?.();
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [path, poll, tick, reload]);
  return { data, error, refreshFailed: issue !== null, issue, received, reload };
}

export function useLiveState() {
  const [on, setOn] = useState(liveConnected());
  useEffect(() => onLive(() => setOn(liveConnected())), []);
  return on;
}
