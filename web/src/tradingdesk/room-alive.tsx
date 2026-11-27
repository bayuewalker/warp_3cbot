import type { Station } from "./room.ts";
import type { RoomLife } from "./room-life.ts";
import "./room-alive.css";

const ROLES: Station[] = ["SCAN", "ANALYST", "AEGIS", "EXECUTOR", "PORTFOLIO", "WATCHER"];

/** Decorative and data-driven layers on the classic control-room art. CSS transforms and opacity only. */
export default function RoomAlive({ life, play }: { life: RoomLife; play: boolean }) {
  return <div className="room-alive" data-play={play ? "on" : "off"} data-halt={life.halt ? "on" : "off"} aria-hidden="true">
    <div className="alive-rain"><i /></div>
    <i className="alive-neon" />
    {ROLES.map(role => {
      const item = life.stations[role];
      return <div key={role} className="alive-desk" data-desk={role} data-mood={item.mood}
        data-radar={item.radar ? "on" : "off"} data-stamp={item.stamp ? "on" : "off"}>
        <i className="alive-monitor"><i className="alive-scrim" /><i className="alive-scroll" /><i className="alive-sweep" /><i className="alive-radar" /></i>
        <i className="alive-eye" />
        <i className="alive-lamp" />
        <i className="alive-keys" />
        <i className="alive-stamp" />
        {item.bubble && <span className="alive-bubble">{item.bubble}{item.time && <small>{item.time}</small>}</span>}
      </div>;
    })}
    <i className="alive-floor" />
    <i className="alive-led" data-led="a" /><i className="alive-led" data-led="b" />
    <i className="alive-led" data-led="c" /><i className="alive-led" data-led="d" />
    {life.halt && <i className="alive-halt" />}
  </div>;
}
