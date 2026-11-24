import { Radio, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import type { Status } from "../api";
import { fmtMoney, fmtTime } from "../format";
import { t, type Key, type Lang } from "../i18n";
import { Providers, RiskControls } from "../monitor";
import { useLoad } from "../snapshot";
import { Badge, Card, CardHead, Field, Note, inputClass } from "../ui";
import {
  ACTIVITY_SOURCES, NO_FILTER, NO_OUTCOME, activityModel, applyFilters, currentResumeAck, facets, resumeGate, riskHealth,
  type ActivityItem, type ActivitySource, type Filters,
} from "./activity-model";
import { readLabel } from "./Evidence";
import { incidentDetail, incidentKeys, readOf, useNow, useStreamView, words } from "./IncidentStrip";
import type { ReadInput, ReadState } from "./types";

type StatusRead = ReturnType<typeof useLoad<Status>>;
type Tone = "good" | "warn" | "bad" | "neutral" | "info";

const sourceKeys: Record<ActivitySource, Key> = {
  events: "actSourceEvents", alerts: "actSourceAlerts", journal: "actSourceJournal",
  signals: "actSourceSignals", breakers: "actSourceBreakers",
};
const refKeys = { alert_id: "actRefAlert", client_id: "actRefClient", strategy_id: "actRefStrategy" } as const;
const severities = { info: ["actSevInfo", "info"], warn: ["actSevWarn", "warn"], bad: ["actSevBad", "bad"] } as const;

function stateTone(state: ReadState, healthy: boolean): Tone {
  if (state === "confirmed" || state === "confirmed_empty") return healthy ? "good" : "neutral";
  return state === "error" || state === "unauthorized" ? "bad" : state === "partial" || state === "stale" ? "warn" : "neutral";
}

function ActivityRow({ lang, item }: { lang: Lang; item: ActivityItem }) {
  const unknown = t(lang, "unknown");
  const severity = item.severity && severities[item.severity];
  const ref = (id: string | null, kind: keyof typeof refKeys | null) =>
    [kind ? t(lang, refKeys[kind]) : t(lang, "actRef"), id ? <code className="mono-break">{id}</code> : unknown] as const;
  const facts = [
    [t(lang, "actFilterOutcome"), item.outcome ? words(item.outcome) : unknown],
    [t(lang, "actSeverity"), severity ? <Badge tone={severity[1]}>{t(lang, severity[0])}</Badge> : unknown],
    [t(lang, "reason"), item.reason ? words(item.reason) : unknown],
    ref(item.refId, item.refKind),
    ...(item.related ? [ref(item.related.refId, item.related.refKind)] : []),
  ];
  return (
    <li className="act-item" data-source={item.source} data-state={item.state} data-timed={item.at === null ? "no" : "yes"}>
      <div className="act-top">
        <span className="act-chip" data-source={item.source}>{t(lang, sourceKeys[item.source])}</span>
        <strong className="act-kind">{words(item.kind)}</strong>
        {item.subject && <code className="act-subject">{item.subject === "*" ? t(lang, "actAllSymbols") : item.subject}</code>}
        {item.state !== "confirmed" && item.state !== "confirmed_empty" && (
          <Badge tone={stateTone(item.state, false)}>{readLabel(lang, item.state)}</Badge>
        )}
      </div>
      <p className="act-time">
        {item.at !== null ? <time dateTime={item.at}>{fmtTime(item.atMs, lang, true)}</time> : t(lang, "actUntimed")}
      </p>
      <dl className="act-facts">
        {facts.map(([label, value], index) => <div key={index}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
    </li>
  );
}

export function ActivityPanel({ lang, status }: { lang: Lang; status: StatusRead }) {
  const loads = {
    events: useLoad("/api/events", true), alerts: useLoad("/api/alerts", true), journal: useLoad("/api/journal", true),
    signals: useLoad("/api/signals", true), breakers: useLoad("/api/breakers", true),
  };
  const connected = useStreamView().connected;
  const now = useNow(5000);
  const [filters, setFilters] = useState<Filters>(NO_FILTER);
  const model = activityModel(Object.fromEntries(ACTIVITY_SOURCES.map(key => [key, readOf(loads[key])])) as Record<ActivitySource, ReadInput>,
    readOf(status), now);
  const all = [...model.timed, ...model.untimed];
  const counts = facets(all);
  const lists = [applyFilters(model.timed, filters), applyFilters(model.untimed, filters)];
  const shown = lists[0].length + lists[1].length;
  const reading = ACTIVITY_SOURCES.every(key => ["confirmed", "confirmed_empty"].includes(model.sources[key].state));
  const entries = {
    source: ACTIVITY_SOURCES.filter(key => counts.sources[key]).map(key => [key, t(lang, sourceKeys[key]), counts.sources[key]] as const),
    kind: counts.kinds.map(([key, count]) => [key, words(key), count] as const),
    outcome: counts.outcomes.map(([key, count]) => [key, key === NO_OUTCOME ? t(lang, "unknown") : words(key), count] as const),
  };
  return (
    <div className="monitor-col activity-page">
      <Card>
        <CardHead title={t(lang, "actTitle")} help={t(lang, "actWindow")} />
        <ul className="act-sources">
          {ACTIVITY_SOURCES.map(key => {
            const s = model.sources[key];
            return (
              <li key={key} data-state={s.state}>
                <span className="act-chip" data-source={key}>{t(lang, sourceKeys[key])}</span>
                <Badge tone={stateTone(s.state, connected && s.freshness !== "stale")}>{readLabel(lang, s.state)}</Badge>
                <small>
                  {key === "breakers" ? "" : `${t(lang, "actLatest")} `}{s.count} {t(lang, "actReported")}
                  {s.invalid > 0 ? ` · ${s.invalid} ${t(lang, "actUnreadable")}` : ""}
                </small>
                <small>{t(lang, key === "events" ? "sourceTime" : "rkPublished")} · {fmtTime(s.sourceAt, lang, true) ?? t(lang, "unknown")}</small>
              </li>
            );
          })}
        </ul>
      </Card>
      <Card>
        <div className="act-filters">
          {(["source", "kind", "outcome"] as const).map(name => (
            <Field key={name} label={t(lang, name === "source" ? "actFilterSource" : name === "kind" ? "actFilterKind" : "actFilterOutcome")}>
              <select className={inputClass} value={filters[name]} onChange={event => setFilters({ ...filters, [name]: event.target.value })}>
                <option value="all">{t(lang, "actAll")} · {all.length}</option>
                {entries[name].map(([key, label, count]) => <option key={key} value={key}>{label} · {count}</option>)}
              </select>
            </Field>
          ))}
        </div>
        <p className="card-help act-count" role="status">
          {t(lang, "actShowing")} {shown} / {all.length} · {t(lang, "actInWindow")}
          {shown !== all.length || filters !== NO_FILTER ? (
            <> <button type="button" className="act-clear" onClick={() => setFilters(NO_FILTER)}>{t(lang, "actClear")}</button></>
          ) : null}
        </p>
      </Card>
      {shown === 0 ? (
        <Card><p className="panel-note">{all.length === 0 && !reading ? t(lang, "unavailable") : t(lang, "actNothing")}</p></Card>
      ) : lists.map((rows, group) => rows.length > 0 && (
        <Card key={group}>
          {group === 1 && <><h3 className="act-group">{t(lang, "actUntimed")}</h3><p className="card-help">{t(lang, "actUntimedHelp")}</p></>}
          <ol className="act-list">
            {rows.map((item, index) => <ActivityRow key={`${item.source}-${item.refId}-${item.at}-${index}`} lang={lang} item={item} />)}
          </ol>
        </Card>
      ))}
    </div>
  );
}

function ageText(ms: number | null) {
  if (ms === null) return "";
  const seconds = Math.floor(ms / 1000);
  return seconds < 90 ? `${seconds}s` : seconds < 5400 ? `${Math.round(seconds / 60)}m` : `${Math.round(seconds / 3600)}h`;
}

const levels = {
  incident: ["rkIncident", "bad"], awaiting: ["actAwaitingFresh", "warn"], unverified: ["rkUnverified", "neutral"], clear: ["rkClear", "good"],
} as const;
const workers = {
  current: ["readFresh", "good"], stale: ["readStale", "warn"], failed: ["refreshError", "bad"],
  loading: ["loading", "neutral"], awaiting: ["awaitingWorker", "neutral"], unknown: ["unknown", "neutral"],
} as const;
const streams = {
  connected: ["streamOn", "good"], fallback: ["rkStreamFallback", "warn"], disconnected: ["rkStreamDown", "bad"],
} as const;

export function RiskHealthPanel({ lang, status }: { lang: Lang; status: StatusRead }) {
  const breakers = useLoad("/api/breakers", true);
  const risk = useLoad("/api/risk", true);
  const stream = useStreamView();
  const now = useNow(1000);
  const gate = resumeGate(currentResumeAck(), status.data?.published_at, now);
  const h = riskHealth({ status: readOf(status), breakers: readOf(breakers), risk: readOf(risk) }, stream, now, gate);
  const unknown = t(lang, "unknown");
  const yesNo = (value: boolean | null) => value === null ? unknown : t(lang, value ? "rkYes" : "rkNo");
  const onOff = (value: boolean | null) => value === null ? unknown : t(lang, value ? "on" : "off");
  const published = fmtTime(h.worker.publishedAt, lang, true);
  const rows: [string, string, string][] = [
    ["HALT", onOff(h.breaker.halt), "/api/breakers"],
    ["HALT", onOff(h.statusHalt), "/api/status"],
    [t(lang, "rkRiskPermitted"), yesNo(h.breaker.riskPermitted), "/api/breakers"],
    [t(lang, "rkRiskPermitted"), yesNo(h.risk.riskPermitted), "/api/risk"],
    [t(lang, "rkExecPermitted"), yesNo(h.risk.executionPermitted), "/api/risk"],
    [t(lang, "reason"), h.breaker.reason ? words(h.breaker.reason) : unknown, "/api/breakers"],
    [t(lang, "equity"), h.risk.equity === null ? unknown : `${fmtMoney(h.risk.equity, lang)} USDT`, "/api/risk"],
    [t(lang, "rkEquityReliable"), yesNo(h.risk.equityReliable), "/api/risk"],
  ];
  const row = status.error ? null : status.data;
  const ledger = (label: string, value: string, source?: string) => (
    <div className="ledger rk-row" key={label + source}><span>{label}{source && <small>{source}</small>}</span><b>{value}</b></div>
  );
  const level = levels[h.level];
  const worker = workers[h.worker.state];
  const link = streams[h.stream.state];
  return (
    <div className="monitor-col risk-page">
      <Card>
        <CardHead icon={h.level === "clear" ? ShieldCheck : ShieldAlert} title={t(lang, "risk")} help={t(lang, "rkOverallHelp")}
          aside={<Badge tone={level[1]}>{t(lang, level[0])}</Badge>} />
        {h.observeGate && <Badge tone="neutral">{t(lang, "obsGate")}</Badge>}
        {h.level === "awaiting" && <p role="status" className="panel-note risk-awaiting">{t(lang, "actAwaitingFreshHelp")}</p>}
        {h.incidents.length > 0 && (
          <ul className="rk-incidents" aria-label={t(lang, "incTitle")}>
            {h.incidents.map((incident, index) => {
              const detail = incidentDetail(lang, incident, h);
              return <li key={index}>{t(lang, incidentKeys[incident.id])}{detail ? ` · ${detail}` : ""}</li>;
            })}
          </ul>
        )}
      </Card>
      <Card>
        <CardHead title={t(lang, "rkWorker")} aside={<Badge tone={worker[1]}>{t(lang, worker[0])}</Badge>} />
        {ledger(t(lang, "rkPublished"), published ? `${published} · ${ageText(h.worker.ageMs)}` : unknown, "/api/status")}
        {ledger(t(lang, "dependencies"), h.dependenciesHealthy === null ? unknown : t(lang, h.dependenciesHealthy ? "healthy" : "degraded"), "/api/status")}
      </Card>
      <Card>
        <CardHead title={t(lang, "providers")} help={t(lang, "providersHelp")} />
        <Providers lang={lang} data={row} heading={false} current={h.worker.state === "current" && stream.connected} />
      </Card>
      <Card>
        <CardHead title={t(lang, "rkBreaker")} />
        <ul className="rk-reads">
          {([["/api/breakers", h.breaker.read], ["/api/risk", h.risk.read]] as const).map(([path, read]) => (
            <li key={path}>
              <code>{path}</code>
              <Badge tone={stateTone(read?.state ?? "loading", stream.connected && read?.freshness !== "stale")}>{readLabel(lang, read?.state ?? "loading")}</Badge>
            </li>
          ))}
        </ul>
        {rows.map(([label, value, source]) => ledger(label, value, source))}
      </Card>
      <Card>
        <CardHead icon={Radio} title={t(lang, "rkStream")} aside={<Badge tone={link[1]}>{t(lang, link[0])}</Badge>} />
        {ledger(t(lang, "rkLastFrame"), fmtTime(h.stream.lastFrameAt, lang, true) ?? unknown)}
      </Card>
      {row ? <RiskControls lang={lang} row={row} reload={status.reload} /> : <Card><Note tone="warn">{t(lang, "noSnapshot")}</Note></Card>}
    </div>
  );
}
