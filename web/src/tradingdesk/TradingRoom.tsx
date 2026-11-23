import { useEffect, useRef, useState } from "react";
import { Activity, SlidersHorizontal, X } from "lucide-react";
import type { Status } from "../api";
import { fmtTime } from "../format";
import { t, type Lang } from "../i18n";
import { useLiveState, useLoad } from "../snapshot";
import { readLabel, roleLabel } from "./Evidence";
import { record, sourceInstant } from "./frames";
import {
  ambient, currentPublication, EVENT_WINDOW_MS, PUBLICATION_TTL_MS, publishedCount, roleHistory,
  stationNames, stations, sweep, type Station,
} from "./room";
import type { ObservedValue } from "./types";
import "./room.css";

type RoomProps = {
  lang: Lang; agents: Record<Station, ObservedValue<string | null>>; status: Status | null;
  events: unknown; now: number;
};
type History = ReturnType<typeof roleHistory>;

function preference(key: string, fallback: boolean) {
  try { return (localStorage.getItem(key) ?? String(fallback)) === "true"; } catch { return fallback; }
}
function save(key: string, value: boolean) {
  try { localStorage.setItem(key, String(value)); } catch { /* In-memory selection still applies. */ }
}

function Sprite({ role }: { role: Station }) {
  const [failed, setFailed] = useState(false);
  return failed ? null : <img className="room-sprite" alt="" width="64" height="56"
    src={'/assets/room/' + role.toLowerCase() + '.svg'} onError={() => setFailed(true)} />;
}

function executorPermitted(status: Status | null) {
  return status?.halt === false && status.gating !== "halt" && status.gating !== "awaiting_worker" &&
    (status.mode === "paper" || status.mode === "live");
}

function reading(lang: Lang, role: Station, item: ObservedValue<string | null>, status: Status | null) {
  if (item.state === "confirmed" && item.freshness === "unknown" && (item.value === "ready" || item.value === "pass")) {
    return t(lang, "freshnessUnknown");
  }
  if (role === "EXECUTOR" && item.value === "permitted" && (item.freshness !== "fresh" || !executorPermitted(status))) {
    return item.state === "confirmed" ? t(lang, status?.halt || status?.gating === "halt" ? "blocked" : "unknown") : readLabel(lang, item.state);
  }
  return roleLabel(lang, item);
}

function SourceRecords({ role, lang, primary }: { role: Station; lang: Lang; primary: ObservedValue<string | null> }) {
  const { route, list, fields } = stations[role];
  const read = useLoad<Record<string, unknown>>(route, true);
  const data = read.data;
  const rows = data?.[list];
  const rawAt = role === "SCAN" ? data?.fetched_at : data?.as_of;
  const at = sourceInstant(rawAt, Date.now());
  const ttl = role === "SCAN" ? 1800000 : 150000;
  const issue = read.issue || data?.error || data?.stale ||
    (Array.isArray(data?.errors) && data.errors.length > 0);
  const incomplete = issue || (role === "PORTFOLIO" && primary.state !== "confirmed" && primary.state !== "confirmed_empty");
  const detail = read.issue === "unauthorized" ? "sessionExpired" : read.issue === "awaiting_first_snapshot" ? "awaitingWorker" :
    read.issue === "malformed_payload" ? "invalidRead" : read.issue ? "refreshError" : !data ? "loading" :
    !Array.isArray(rows) || !rows.every(record) ? "invalidRead" :
    incomplete ? "readPartial" : at === null ? "freshnessUnknown" : Date.now() - at > ttl ? "readStale" : "readFresh";
  const valid = Array.isArray(rows) && rows.every(record);
  const unknown = t(lang, "unknown");
  return <section className="room-records">
    <h3>{t(lang, "roomRecords")}</h3><code>{route}</code>
    <p>{t(lang, detail)} · {t(lang, "sourceTime")} {fmtTime(at, lang, true) ?? unknown}</p>
    {valid && rows.length > 0 && <ul>{rows.slice(0, 5).map((row, i) => <li key={i}>
      {fields.map(field => typeof row[field] === "string" && row[field] ? row[field] : unknown).join(" · ")}
    </li>)}</ul>}
    {valid && rows.length === 0 && !incomplete && <p>{t(lang, "roomEmpty")}</p>}
  </section>;
}

function RoomDetail({ role, lang, item, status, history, stream, historyState, close }: {
  role: Station; lang: Lang; item: ObservedValue<string | null>; status: Status | null;
  history: History; stream: boolean; historyState: string; close: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = dialog.current!;
    const trigger = document.activeElement;
    el.showModal();
    return () => { el.close(); if (trigger instanceof HTMLElement) trigger.focus({ preventScroll: true }); };
  }, []);
  const unknown = t(lang, "unknown");
  function eventText(event: History["latest"]) {
    return event ? [event.event, fmtTime(event.ts, lang, true), event.traceId].filter(Boolean).join(" · ") : unknown;
  }
  return <dialog ref={dialog} className="room-dialog" aria-labelledby="room-detail-title"
    onCancel={e => { e.preventDefault(); close(); }}>
    <header><div><strong id="room-detail-title">{role}</strong><p>{t(lang, stations[role].title)}</p></div>
      <button autoFocus aria-label={t(lang, "roomClose")} onClick={close}><X size={20} /></button></header>
    {status?.halt === true && <p className="room-halt" role="alert">HALT · {t(lang, "gateBlocked")}</p>}
    <dl>{[
      [t(lang, "roomState"), reading(lang, role, item, status)], [t(lang, "source"), stations[role].source],
      [t(lang, stations[role].source === "/api/status" ? "roomPublication" : "sourceTime"), fmtTime(item.sourceAt, lang, true) ?? unknown],
      [t(lang, "clientReceipt"), fmtTime(item.receivedAt, lang, true) ?? unknown],
      [t(lang, "freshness"), t(lang, item.freshness === "fresh" ? "readFresh" : item.freshness === "stale" ? "readStale" : "freshnessUnknown")],
      [t(lang, "reason"), item.reason ?? unknown], ...(item.traceId ? [["Trace ID", item.traceId]] : []),
      ...(role === "WATCHER" ? [[t(lang, "streamLabel"), t(lang, stream ? "streamOn" : "reconnecting")]] : []),
    ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <h3>{t(lang, "roomHistory")}</h3><code>/api/events · {historyState}</code>
    <dl>{[
      [t(lang, "roomLastEvent"), eventText(history.latest)], [t(lang, "roomSuccess"), eventText(history.success)],
      [t(lang, "roomFailure"), eventText(history.failure)], [t(lang, "roomNext"), unknown],
    ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <SourceRecords role={role} lang={lang} primary={item} />
  </dialog>;
}

export function TradingRoom({ lang, agents, status, events, now }: RoomProps) {
  const [selected, select] = useState<Station | null>(null);
  const [settings, showSettings] = useState(false);
  const [motion, setMotion] = useState(() => preference("warp.room.motion", true));
  const [text, setText] = useState(() => preference("warp.room.text",
    Boolean((navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData)));
  const room = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const stream = useLiveState();
  useEffect(() => {
    let onScreen = false;
    const update = () => setVisible(onScreen && !document.hidden);
    const observer = new IntersectionObserver(([entry]) => { onScreen = entry.isIntersecting; update(); });
    observer.observe(room.current!);
    document.addEventListener("visibilitychange", update);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", update); };
  }, []);
  const logs = agents.WATCHER.state === "unauthorized" ? null : events;
  // Expire motion at its evidence deadline even between two Overview refreshes.
  const [expiredAt, setExpiredAt] = useState(now);
  const motionNow = Math.max(now, expiredAt);
  const publicationFresh = currentPublication(status?.published_at, motionNow);
  useEffect(() => {
    const clock = Date.now();
    const deadlines: number[] = [];
    function addDeadline(stamp: unknown, ttl: number) {
      const time = sourceInstant(stamp, clock);
      if (time !== null && time + ttl >= motionNow) deadlines.push(time + ttl + 1);
    }
    addDeadline(status?.published_at, PUBLICATION_TTL_MS);
    if (Array.isArray(logs)) {
      for (const entry of logs) if (record(entry)) addDeadline(entry.ts, EVENT_WINDOW_MS);
    }
    if (!deadlines.length) return;
    const timer = window.setTimeout(() => setExpiredAt(Date.now()), Math.max(0, Math.min(...deadlines) - clock));
    return () => window.clearTimeout(timer);
  }, [logs, status?.published_at, motionNow]);
  return <>
    <section ref={room} className="room-summary trading-room" aria-labelledby="room-title"
      data-motion={motion && !text && visible && !selected ? "on" : "off"}>
      <header><h2 id="room-title"><Activity size={17} />{t(lang, "room")}</h2>
        <button className="room-settings" aria-label={t(lang, "roomSettings")} aria-expanded={settings}
          onClick={() => showSettings(!settings)}><SlidersHorizontal size={17} /></button></header>
      {settings && <div className="room-options">
        <label><input type="checkbox" checked={motion} onChange={e => { setMotion(e.target.checked); save("warp.room.motion", e.target.checked); }} />{t(lang, "roomMotion")}</label>
        <label><input type="checkbox" checked={text} onChange={e => { setText(e.target.checked); save("warp.room.text", e.target.checked); }} />{t(lang, "roomText")}</label>
      </div>}
      <dl>{stationNames.map(role => {
        const item = agents[role];
        const event = roleHistory(role, logs, now).latest;
        const at = event?.ts ?? item.sourceAt;
        const moving = ambient(role, item, stream, publicationFresh) && (role !== "EXECUTOR" || executorPermitted(status));
        const recent = moving && agents.WATCHER.state === "confirmed" && agents.WATCHER.freshness === "fresh" &&
          sweep(role, event, status?.mode, motionNow);
        const count = publishedCount(item, role === "SCAN" && item.value !== "blocked" ? status?.pipeline?.screening?.count :
          role === "ANALYST" ? status?.pipeline?.analysis?.count : null);
        const clock = at && new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit", hour12: false }).format(Date.parse(at));
        const clockTitle = t(lang, event ? "roomEventTime" : stations[role].source === "/api/status" ? "roomPublication" : "sourceTime");
        return <div key={role} className="station" data-role={role} data-ambient={moving ? "on" : "off"}
          data-recent={recent ? "on" : "off"}>
          <dt>
            {!text && <span className="station-stage" aria-hidden="true"><i className="station-optic" /><Sprite role={role} />
              {recent && event && <i key={event.ts + event.event} className="station-event" />}</span>}
            <span>{role}</span><button className="station-open" aria-haspopup="dialog" aria-label={role}
              aria-describedby={'state-' + role} onClick={() => select(role)} /></dt>
          <dd id={'state-' + role}>{reading(lang, role, item, status) +
            (role === "WATCHER" && !stream ? " · " + t(lang, "reconnecting") : "")}</dd>
          <dd className="station-clock" aria-label={clockTitle + ' · ' + (fmtTime(at, lang, true) ?? t(lang, "unknown"))} title={clockTitle + ' · ' + (fmtTime(at, lang, true) ?? t(lang, "unknown"))}>
            {clock ?? "—"}{count !== null && ' · ' + count + ' ' + t(lang, role === "SCAN" ? "symbols" : "outputs").toLowerCase()}</dd>
        </div>;
      })}</dl>
    </section>
    {selected && <RoomDetail role={selected} lang={lang} item={agents[selected]} status={status}
      history={roleHistory(selected, logs, now)} stream={stream} historyState={readLabel(lang, agents.WATCHER.state)} close={() => select(null)} />}
  </>;
}
