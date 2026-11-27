import { OrbitControls } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import { fmtTime } from "../../format.ts";
import { t, type Lang } from "../../i18n.ts";
import { useLiveState, useLoad } from "../../snapshot.ts";
import { deskPrices } from "../quotes.ts";
import type { Station } from "../room.ts";
import type { ObservedValue } from "../types.ts";
import { Desk } from "./Desk.tsx";
import { LAYOUT } from "./layout.ts";
import { wibLight } from "./lighting.ts";
import { bubbleText, deskLabel, fmtWib } from "./office-copy.ts";
import { DESKS, officeState, type DeskId, type DeskState, type PriorFrame } from "./office-state.ts";
import { Room } from "./Room.tsx";
import { WallScreen } from "./WallScreen.tsx";
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

function quality() {
  const mobile = window.matchMedia("(max-width: 800px)").matches;
  const cores = navigator.hardwareConcurrency || 8;
  const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8;
  const low = cores <= 4 || memory <= 4;
  return {
    dpr: (mobile ? [1, 1.5] : [1, 2]) as [number, number],
    antialias: !low,
    shadows: !mobile && !low,
  };
}

function Refresh({ token, paused }: { token: string; paused: boolean }) {
  const invalidate = useThree((state) => state.invalidate);
  useEffect(() => { if (!paused) invalidate(); }, [token, paused, invalidate]);
  return null;
}

export default function Office3D({ lang, status, events, agents, now, animate, paused, onOpenStation, onLost }: Office3DProps) {
  const alerts = useLoad("/api/alerts", true);
  const forward = useLoad("/api/forward", true);
  const breakers = useLoad("/api/breakers", true);
  const prices = useLoad("/api/prices", true);
  const stream = useLiveState();
  const prior = useRef<PriorFrame | null>(null);
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
  const [bubble, setBubble] = useState<DeskId | null>(null);
  const [extra, setExtra] = useState<Extract<DeskId, "XAU" | "FORWARD"> | null>(null);
  const settings = useMemo(quality, []);
  const light = wibLight(now);
  const hold = paused || extra != null;
  const token = JSON.stringify(DESKS.map((id) => {
    const desk = model.desks[id];
    return [desk.pose, desk.lamp, desk.prop, desk.bubble, desk.stack, desk.plate, desk.present, desk.facts];
  })) + light.phase + light.day.toFixed(2);
  const tape = deskPrices({ data: prices.data, received: prices.received, issue: prices.issue }, now);
  const quotes = tape.state === "confirmed" && tape.freshness === "fresh" && tape.value
    ? tape.value.map((quote) => ({ symbol: quote.symbol, last: quote.last, change: quote.change }))
    : [];
  const watcher = agents.WATCHER;
  const notice = !Array.isArray(events)
    ? t(lang, watcher.state === "unauthorized" ? "sessionExpired" : watcher.state === "loading" || watcher.state === "awaiting_first_snapshot" ? "loading" : "unknown")
    : null;

  function open(id: DeskId) {
    if (id === "XAU" || id === "FORWARD") setExtra(id);
    else if (STATIONS.has(id)) onOpenStation(id as Station);
  }
  function pick(id: DeskId, pointerType: string) {
    if (pointerType === "touch" && bubble !== id) {
      setBubble(id);
      return;
    }
    setBubble(id);
    open(id);
  }

  return <div className="office-stage" data-motion={animate ? "on" : "off"} data-paused={hold ? "on" : "off"}>
    <ul className="office-a11y">
      {DESKS.map((id) => {
        const desk = model.desks[id];
        const when = fmtWib(desk.sourceAt, lang) ?? fmtTime(desk.sourceAt, lang, true) ?? t(lang, "unknown");
        return <li key={id}><button type="button" data-desk={id} onFocus={() => setBubble(id)} onClick={() => { setBubble(id); open(id); }}>
          {deskLabel(lang, id) + " · " + bubbleText(lang, id, desk).split("\n")[0] + " · " + when}
        </button></li>;
      })}
    </ul>
    <Canvas
      dpr={settings.dpr}
      frameloop={hold ? "never" : "demand"}
      shadows={settings.shadows}
      gl={{ antialias: settings.antialias, alpha: false, powerPreference: "low-power" }}
      camera={{ position: [0.85, 8.5, 10.8], fov: 46, near: 0.1, far: 48 }}
      onPointerMissed={() => setBubble(null)}
      onCreated={({ gl, camera }) => {
        camera.lookAt(0, 0.4, 0.55);
        gl.domElement.addEventListener("webglcontextlost", (event) => {
          event.preventDefault();
          onLost();
        });
      }}
    >
      <Refresh token={token} paused={hold} />
      <Room now={now} shadows={settings.shadows} />
      <WallScreen lang={lang} events={events} notice={notice} quotes={quotes} />
      {DESKS.map((id) => <group key={id} position={[LAYOUT[id].x, 0, LAYOUT[id].z]}>
        <Desk
          id={id}
          desk={model.desks[id]}
          lang={lang}
          glow={model.desks[id].lamp === "off" ? 0 : 0.35 + (1 - light.day) * 0.75}
          showBubble={bubble === id}
          onPick={pick}
          onHover={setBubble}
        />
      </group>)}
      <OrbitControls
        enablePan={false}
        enableDamping={animate}
        minPolarAngle={0.78}
        maxPolarAngle={1.22}
        minAzimuthAngle={-0.85}
        maxAzimuthAngle={0.85}
        minDistance={7}
        maxDistance={18}
        target={[0, 0.4, 0.55]}
      />
    </Canvas>
    {extra && <ExtraDrawer lang={lang} id={extra} desk={model.desks[extra]} close={() => setExtra(null)} />}
  </div>;
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
