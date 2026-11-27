import { useEffect, useRef, useState } from "react";
import { fmtTime } from "../../format.ts";
import { t, type Lang } from "../../i18n.ts";
import { useLiveState, useLoad } from "../../snapshot.ts";
import { deskPrices, retainQuotes, type QuoteHistory } from "../quotes.ts";
import type { Station } from "../room.ts";
import type { ObservedValue } from "../types.ts";
import { ambientFrameDelay } from "./ambient.ts";
import { cardValue, deliveryProgress, officeCues, type OfficeCues } from "./life.ts";
import { wibLight } from "./lighting.ts";
import { bubbleText, deskLabel, fmtWib } from "./office-copy.ts";
import { DESKS, officeState, type DeskId, type DeskState, type PriorFrame } from "./office-state.ts";
import { deskHit, paintOffice, SCENE_H, SCENE_W } from "./pixel.ts";
import "./office3d.css";

const STATIONS = new Set<string>(["SCAN", "ANALYST", "AEGIS", "EXECUTOR", "PORTFOLIO", "WATCHER"]);

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
  const quote = chartQuote(history.current, tape.value);
  const light = wibLight(now);
  const [bubble, setBubble] = useState<DeskId | null>(null);
  const [extra, setExtra] = useState<Extract<DeskId, "XAU" | "FORWARD"> | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const hold = paused || extra != null;
  const live = animate && !hold;
  const pending = typeof (forward.data as { pending?: unknown } | null)?.pending === "number"
    ? (forward.data as { pending: number }).pending : null;
  const alertCount = alerts.issue || !alerts.data ? null : alertLength(alerts.data);

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const ctx = el.getContext("2d");
    if (!ctx) {
      onLost();
      return;
    }
    let stop = false;
    let timer = 0;
    let deadline = 0;
    const paint = (ms: number) => {
      if (stop) return;
      const seconds = ms / 1000;
      paintOffice(ctx, {
        desks: model.desks,
        cues,
        day: light.day,
        seconds,
        motion: live,
        quote,
        walks: cues.deliveries.map((row) => ({ from: row.from, progress: deliveryProgress(row.sourceAt, now) })),
      });
      if (!live) return;
      const next = ambientFrameDelay(seconds, deadline);
      deadline = next.deadline;
      timer = window.setTimeout(() => requestAnimationFrame(paint), next.waitMs);
    };
    requestAnimationFrame(paint);
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [live, cues, light.day, quote, model, now, onLost]);

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
      {DESKS.map((id) => <button key={id} type="button" className="pixel-card" data-card={id}
        onClick={() => pick(id)} style={{ borderColor: cardColor(id, model.desks[id], cues) }}>
        <span>{id === "EXECUTOR" ? "CHIEF" : id === "FORWARD" ? "SCORE" : id}</span>
        <strong>{cardValue(id, model.desks[id], recordStatus(status), pending, alertCount)}</strong>
      </button>)}
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

function recordStatus(status: unknown): Record<string, unknown> | null {
  return status !== null && typeof status === "object" && !Array.isArray(status) ? status as Record<string, unknown> : null;
}

function alertLength(data: unknown): number | null {
  if (!data || typeof data !== "object") return null;
  const rows = (data as { alerts?: unknown }).alerts;
  return Array.isArray(rows) ? rows.length : null;
}

function chartQuote(history: QuoteHistory, latest: { symbol: string; last: string }[] | null): { symbol: string; points: number[] } | null {
  const symbols = Object.keys(history);
  const preferred = symbols.find((symbol) => symbol.includes("XAU")) ?? latest?.[0]?.symbol ?? symbols[0];
  if (!preferred) return null;
  const points = (history[preferred] ?? []).map((point) => point.last).filter((value) => Number.isFinite(value));
  if (!points.length && latest) {
    const row = latest.find((item) => item.symbol === preferred) ?? latest[0];
    const last = Number(row?.last);
    if (row && Number.isFinite(last)) return { symbol: row.symbol, points: [last] };
  }
  return points.length ? { symbol: preferred, points } : null;
}

function cardColor(id: DeskId, desk: DeskState, cues: OfficeCues): string {
  if (id === "AEGIS" && cues.alarm) return "#ff6b6b";
  if (id === "WATCHER" && cues.phone) return "#5eb0ff";
  if (desk.lamp === "red") return "#ff6b6b";
  if (desk.lamp === "amber") return "#e6b15a";
  if (desk.lamp === "off") return "#3a4450";
  return "#3d8bff";
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
