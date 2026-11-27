import { useEffect, useRef, useState } from "react";
import { fmtTime } from "../../format.ts";
import { t, type Lang } from "../../i18n.ts";
import { useLiveState, useLoad } from "../../snapshot.ts";
import { deskPrices, retainQuotes, type QuoteHistory } from "../quotes.ts";
import type { Station } from "../room.ts";
import type { ObservedValue } from "../types.ts";
import { ambientFrameDelay } from "./ambient.ts";
import { cardRatio, cardValue, deliveryProgress, officeCues, type OfficeCues, type WatcherCardRow } from "./life.ts";
import { wibLight } from "./lighting.ts";
import { bubbleText, cardSentence, deskLabel, fmtWib, PLATE } from "./office-copy.ts";
import { DESKS, officeState, type DeskId, type DeskState, type PriorFrame } from "./office-state.ts";
import { deskHit, paintOffice, SCENE_H, SCENE_W, type PaintScene } from "./pixel.ts";
import { DESK_COLOR } from "./layout.ts";
import "./office3d.css";

const STATIONS = new Set<string>(["SCAN", "ANALYST", "AEGIS", "EXECUTOR", "PORTFOLIO", "WATCHER"]);
const FRAME_S = 1 / 8;

export type Office3DProps = {
  lang: Lang;
  status: unknown;
  events: unknown;
  agents: Record<Station, ObservedValue<string | null>>;
  now: number;
  animate: boolean;
  paused: boolean;
  onOpenStation: (role: Station) => void;
  onLost: () => void;
};

type SceneView = {
  desks: PaintScene["desks"];
  cues: OfficeCues;
  day: number;
  motion: boolean;
  quote: PaintScene["quote"];
  deliveries: OfficeCues["deliveries"];
};

export default function Office3D({ lang, status, events, agents, now, animate, paused, onOpenStation, onLost }: Office3DProps) {
  const alerts = useLoad("/api/alerts", true);
  const forward = useLoad("/api/forward", true);
  const breakers = useLoad("/api/breakers", true);
  const prices = useLoad("/api/prices", true);
  const stream = useLiveState();
  const prior = useRef<PriorFrame | null>(null);
  const history = useRef<QuoteHistory>({});
  const model = officeState({
    status,
    events,
    alerts: alerts.data,
    alertsIssue: alerts.issue,
    forward: forward.data,
    forwardIssue: forward.issue,
    breakers: breakers.data,
    breakersIssue: breakers.issue,
    agents,
    stream,
    prior: prior.current,
  }, now);
  prior.current = model.prior;
  const cues = officeCues(model.desks);
  const tape = deskPrices({ data: prices.data, received: prices.received, issue: prices.issue }, now);
  if (tape.state === "confirmed" && tape.freshness === "fresh") history.current = retainQuotes(history.current, tape);
  const quote = chartQuote(history.current, tape.value, universeSymbols(status));
  const light = wibLight(now);
  const [bubble, setBubble] = useState<DeskId | null>(null);
  const [extra, setExtra] = useState<Extract<DeskId, "XAU" | "FORWARD"> | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const hold = paused || extra != null;
  const live = animate && !hold;
  const pending = typeof (forward.data as { pending?: unknown } | null)?.pending === "number"
    ? (forward.data as { pending: number }).pending : null;
  const watcherRows = alerts.issue || !alerts.data ? null : watcherCardRows(alerts.data);
  const statusRecord = recordStatus(status);
  const view = useRef<SceneView>({ desks: model.desks, cues, day: light.day, motion: live, quote, deliveries: cues.deliveries });
  view.current = { desks: model.desks, cues, day: light.day, motion: live, quote, deliveries: cues.deliveries };
  const draw = useRef<(ms: number) => void>(() => {});
  const lost = useRef(onLost);
  lost.current = onLost;
  draw.current = (ms: number) => {
    const el = canvas.current;
    const ctx = el?.getContext("2d");
    if (!ctx) return;
    const scene = view.current;
    paintOffice(ctx, {
      desks: scene.desks,
      cues: scene.cues,
      day: scene.day,
      seconds: ms / 1000,
      motion: scene.motion,
      quote: scene.quote,
      walks: scene.deliveries.map((row) => ({
        from: row.from,
        progress: scene.motion ? deliveryProgress(row.sourceAt, Date.now()) : 1,
      })),
    });
  };

  useEffect(() => {
    const el = canvas.current;
    const ctx = el?.getContext("2d");
    if (!ctx) {
      lost.current();
      return;
    }
    let stop = false;
    let timer = 0;
    let raf = 0;
    let deadline = 0;
    const frame = (ms: number) => {
      if (stop) return;
      draw.current(ms);
      if (!view.current.motion) return;
      const next = ambientFrameDelay(ms / 1000, deadline, FRAME_S);
      deadline = next.deadline;
      timer = window.setTimeout(() => { raf = requestAnimationFrame(frame); }, next.waitMs);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      stop = true;
      window.cancelAnimationFrame(raf);
      window.clearTimeout(timer);
    };
  }, [live]);

  useEffect(() => {
    if (live) return;
    draw.current(0);
  });

  function open(id: DeskId) {
    if (id === "XAU" || id === "FORWARD") setExtra(id);
    else if (STATIONS.has(id)) onOpenStation(id as Station);
  }
  function pick(id: DeskId) {
    setBubble(id);
    open(id);
  }

  return <div className="office-stage pixel-office" data-motion={live ? "on" : "off"} data-paused={hold ? "on" : "off"}>
    <ul className="office-a11y">
      {DESKS.map((id) => {
        const desk = model.desks[id];
        const when = fmtWib(desk.sourceAt, lang) ?? fmtTime(desk.sourceAt, lang, true) ?? t(lang, "unknown");
        return <li key={id}><button type="button" data-desk={id} onFocus={() => setBubble(id)} onClick={() => pick(id)}>
          {deskLabel(lang, id) + " · " + bubbleText(lang, id, desk).split("\n")[0] + " · " + when}
        </button></li>;
      })}
    </ul>
    <div className="pixel-cards">
      {DESKS.map((id) => {
        const desk = model.desks[id];
        const value = cardValue(id, desk, statusRecord, pending, watcherRows);
        const ratio = cardRatio(id, desk, statusRecord);
        const color = cardColor(id, desk, cues);
        return <button key={id} type="button" className="pixel-card" data-card={id} data-value={value}
          onClick={() => pick(id)} style={{ borderColor: color, color }}>
          <RoleMark id={id} color={color} />
          <span className="pixel-card-name">{PLATE[id]}</span>
          <strong>{value}</strong>
          {ratio && <span className="pixel-bar" aria-hidden="true"><span style={{ width: Math.round((ratio.used / ratio.cap) * 100) + "%", background: color }} /></span>}
          <em title={cardSentence(lang, id, desk, value)}>{cardSentence(lang, id, desk, value)}</em>
        </button>;
      })}
    </div>
    <canvas
      ref={canvas}
      width={SCENE_W}
      height={SCENE_H}
      aria-hidden="true"
      onClick={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const x = (event.clientX - rect.left) * (SCENE_W / rect.width);
        const y = (event.clientY - rect.top) * (SCENE_H / rect.height);
        const id = deskHit(x, y);
        if (id) pick(id);
        else setBubble(null);
      }}
    />
    {bubble && <p className="pixel-bubble" role="status">{bubbleText(lang, bubble, model.desks[bubble])}</p>}
    {extra && <ExtraDrawer lang={lang} id={extra} desk={model.desks[extra]} close={() => setExtra(null)} />}
  </div>;
}

function RoleMark({ id, color }: { id: DeskId; color: string }) {
  const shape = id === "SCAN" || id === "XAU" ? "box" : id === "AEGIS" ? "helm" : "round";
  return <svg className="pixel-mark" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
    <rect width="14" height="14" fill="#120c1c" />
    {shape === "round" ? <rect x="3" y="1" width="8" height="7" rx="3" fill={DESK_COLOR[id]} /> : <rect x="3" y="1" width="8" height="7" fill={DESK_COLOR[id]} />}
    <rect x="4" y="4" width="6" height="2" fill={color} />
    <rect x="4" y="9" width="6" height="3" fill={DESK_COLOR[id]} />
  </svg>;
}

function recordStatus(status: unknown): Record<string, unknown> | null {
  return status !== null && typeof status === "object" && !Array.isArray(status) ? status as Record<string, unknown> : null;
}

function universeSymbols(status: unknown): string[] {
  const row = recordStatus(status);
  const list = row && Array.isArray(row.universe) ? row.universe : [];
  return list.filter((item): item is string => typeof item === "string" && /^[A-Z0-9]{2,30}$/.test(item));
}

function watcherCardRows(data: unknown): WatcherCardRow[] | null {
  if (!data || typeof data !== "object") return null;
  const rows = (data as { alerts?: unknown }).alerts;
  if (!Array.isArray(rows)) return null;
  const out: WatcherCardRow[] = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const record = row as Record<string, unknown>;
    if (typeof record.id !== "number" || !Number.isFinite(record.id)) continue;
    const attempts = typeof record.attempts === "number" && Number.isFinite(record.attempts) ? record.attempts : null;
    const error = typeof record.last_error === "string" && record.last_error ? record.last_error : null;
    out.push({
      id: record.id,
      state: typeof record.state === "string" ? record.state : "",
      event: typeof record.event === "string" ? record.event : "",
      error,
      attempts,
    });
  }
  return out;
}

function chartQuote(history: QuoteHistory, latest: { symbol: string; last: string }[] | null, universe: string[]): { symbol: string; points: number[] } | null {
  const symbol = universe[0] ?? latest?.[0]?.symbol ?? Object.keys(history)[0] ?? null;
  if (!symbol) return null;
  const points = (history[symbol] ?? []).map((point) => point.last).filter((value) => Number.isFinite(value));
  if (!points.length && latest) {
    const row = latest.find((item) => item.symbol === symbol);
    const last = row ? Number(row.last) : Number.NaN;
    if (row && Number.isFinite(last)) return { symbol, points: [last] };
  }
  return { symbol, points };
}

function cardColor(id: DeskId, desk: DeskState, cues: OfficeCues): string {
  if (id === "AEGIS" && cues.alarm) return "#ff6b6b";
  if (id === "WATCHER" && cues.phone) return "#5eb0ff";
  if (desk.lamp === "red") return "#ff6b6b";
  if (desk.lamp === "amber") return "#e6b15a";
  if (desk.lamp === "off") return "#3a4450";
  return DESK_COLOR[id];
}

function ExtraDrawer({ lang, id, desk, close }: { lang: Lang; id: DeskId; desk: DeskState; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    el.showModal();
    return () => { el.close(); };
  }, []);
  return <dialog ref={dialog} className="room-dialog" aria-labelledby="office-extra-title" onCancel={(event) => { event.preventDefault(); close(); }}>
    <header>
      <div><strong id="office-extra-title">{deskLabel(lang, id)}</strong><p>{t(lang, "officeDetail")}</p></div>
      <button type="button" autoFocus aria-label={t(lang, "roomClose")} onClick={close}>×</button>
    </header>
    <p>{bubbleText(lang, id, desk)}</p>
    <dl>{desk.fields.map((row) => <div key={row.name}><dt>{row.name}</dt><dd>{row.value}</dd></div>)}</dl>
  </dialog>;
}
