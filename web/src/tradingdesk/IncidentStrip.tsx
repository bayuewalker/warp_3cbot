import { ChevronRight, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import type { Status } from "../api";
import { t, type Key, type Lang } from "../i18n";
import { liveConnected, liveDownSince, liveLastFrameAt, onLive } from "../live";
import { stateLabel } from "../monitor";
import type { useLoad } from "../snapshot";
import { Badge } from "../ui";
import { requestPreferencesTab, riskHealth, type Incident, type RiskHealth, type StreamView } from "./activity-model";
import type { ReadInput } from "./types";

type StatusRead = ReturnType<typeof useLoad<Status>>;

export const words = (value: string) => value.replaceAll("_", " ");

export function readOf(load: { data: unknown; received: Date | null; issue: ReadInput["issue"] }): ReadInput {
  return { data: load.data, received: load.received, issue: load.issue };
}

export function useNow(ms: number) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(timer);
  }, [ms]);
  return now;
}

/** Connection facts the stream already tracks. A drop is counted from the first moment it is seen. */
export function useStreamView(): StreamView {
  const read = (): StreamView => ({ connected: liveConnected(), lastFrameAt: liveLastFrameAt(), downSince: liveDownSince() });
  const [view, setView] = useState<StreamView>(read);
  useEffect(() => onLive(() => setView(previous => {
    const { connected, lastFrameAt, downSince } = read();
    return previous.connected === connected && previous.lastFrameAt === lastFrameAt && previous.downSince === downSince
      ? previous : { connected, lastFrameAt, downSince };
  })), []);
  return view;
}

export const incidentKeys: Record<Incident["id"], Key> = {
  halt: "incHalt", risk: "incRisk", provider: "incProvider", stream: "incStream",
  statusFailed: "incStatusFailed", statusStale: "incStatusStale", xau: "incXau",
};

export function incidentDetail(lang: Lang, incident: Incident, health: RiskHealth) {
  if (incident.id === "risk" || incident.id === "xau") return incident.detail ? words(incident.detail) : null;
  if (incident.id !== "provider") return null;
  const provider = health.providers.find(row => incident.detail?.startsWith(row.name + " · "));
  return provider ? `${provider.name} · ${stateLabel(lang, provider.state ?? undefined)}` : incident.detail;
}

/** Shown only while a real incident exists. Every entry opens the Risk & health tab. */
export function IncidentStrip({ lang, status }: { lang: Lang; status: StatusRead }) {
  const stream = useStreamView();
  const now = useNow(1000);
  const health = riskHealth({ status: readOf(status) }, stream, now);
  if (!health.incidents.length) return null;
  return (
    <section className="incident-strip" aria-label={t(lang, "incTitle")}>
      <h2><ShieldAlert size={16} aria-hidden="true" />{t(lang, "incTitle")} · {health.incidents.length}</h2>
      {health.observeGate && <Badge tone="neutral">{t(lang, "obsGate")}</Badge>}
      <ul>
        {health.incidents.map((incident, index) => {
          const detail = incidentDetail(lang, incident, health);
          return (
            <li key={`${incident.id}-${index}`}>
              <a href="#preferences" data-incident={incident.id} onClick={() => requestPreferencesTab("risk")}>
                <span>{t(lang, incidentKeys[incident.id])}{detail ? <small>{detail}</small> : null}</span>
                <ChevronRight size={16} aria-hidden="true" />
              </a>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
