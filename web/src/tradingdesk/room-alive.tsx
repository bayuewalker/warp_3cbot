import { useEffect, useRef, useState } from "react";
import type { Station } from "./room.ts";
import type { ObservedValue } from "./types.ts";
import {
  alivePlayback, nextScan, roomLife, screeningKey,
  type LifeStatus, type Mood, type ScanMemory,
} from "./room-life.ts";
import "./room-alive.css";

const ROLES: Station[] = ["SCAN", "ANALYST", "AEGIS", "EXECUTOR", "PORTFOLIO", "WATCHER"];

type Flags = {
  reduced: boolean; hidden: boolean; offscreen: boolean; panel: boolean; text: boolean; motion: boolean;
};

/** Decorative and data-driven layers on the classic control-room art. CSS transforms and opacity only. */
export default function RoomAlive({
  agents, status, events, now, flags, onMoods,
}: {
  agents: Partial<Record<Station, ObservedValue<string | null>>>;
  status: LifeStatus;
  events: unknown;
  now: number;
  flags: Flags;
  onMoods: (moods: Record<Station, Mood>) => void;
}) {
  const scanRef = useRef<ScanMemory | null>(null);
  const [clock, setClock] = useState(now);
  const moment = Math.max(now, clock);
  const scan = nextScan(scanRef.current, screeningKey(status), moment);
  scanRef.current = { key: scan.key, until: scan.until };
  const life = roomLife({ agents, status, events, now: moment, radar: scan.radar });
  const play = alivePlayback(flags);
  useEffect(() => {
    if (scan.until <= Date.now()) return;
    const timer = window.setTimeout(() => setClock(Date.now()), Math.max(0, scan.until - Date.now() + 1));
    return () => window.clearTimeout(timer);
  }, [scan.until]);
  const signature = ROLES.map(role => life.stations[role].mood).join("|");
  useEffect(() => {
    const moods = {} as Record<Station, Mood>;
    signature.split("|").forEach((mood, index) => { moods[ROLES[index]] = mood as Mood; });
    onMoods(moods);
  }, [signature, onMoods]);
  return <div className="room-alive" data-play={play ? "on" : "off"} data-halt={life.halt ? "on" : "off"} aria-hidden="true">
    <div className="alive-rain"><i /></div>
    <i className="alive-neon" />
    {ROLES.map(role => {
      const item = life.stations[role];
      return <div key={role} className="alive-desk" data-desk={role} data-mood={item.mood}
        data-radar={item.radar ? "on" : "off"} data-stamp={item.stamp ? "on" : "off"}>
        <i className="alive-monitor">
          <i className="alive-glow" />
          <i className="alive-sheen" />
          <i className="alive-sweep" />
          <i className="alive-radar" />
          <i className="alive-stamp" />
        </i>
        <i className="alive-eye" />
        <i className="alive-lamp" />
        <i className="alive-keys" />
        {item.bubble && <span className="alive-bubble">{item.bubble}{item.time && <small>{item.time}</small>}</span>}
      </div>;
    })}
    <i className="alive-floor" />
    <i className="alive-led" data-led="a" /><i className="alive-led" data-led="b" />
    <i className="alive-led" data-led="c" /><i className="alive-led" data-led="d" />
    {life.halt && <i className="alive-halt" />}
  </div>;
}
