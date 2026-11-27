import { Html } from "@react-three/drei";
import type { Lang } from "../../i18n.ts";
import { DESK_COLOR } from "./layout.ts";
import { bubbleText, deskLabel } from "./office-copy.ts";
import { Character } from "./Character.tsx";
import { Lamp } from "./Lamp.tsx";
import { Props } from "./Props.tsx";
import type { DeskId, DeskState } from "./office-state.ts";

export function Desk({
  id, desk, lang, glow, showBubble, onPick, onHover,
}: {
  id: DeskId;
  desk: DeskState;
  lang: Lang;
  glow: number;
  showBubble: boolean;
  onPick: (id: DeskId, pointerType: string) => void;
  onHover: (id: DeskId) => void;
}) {
  const color = DESK_COLOR[id];
  function pointerKind(event: { nativeEvent: Event }) {
    const native = event.nativeEvent;
    return native instanceof PointerEvent ? native.pointerType : "mouse";
  }
  return <group position={[0, 0, 0]}>
    <mesh position={[0, 0.36, 0]} receiveShadow>
      <boxGeometry args={[0.9, 0.68, 0.08]} />
      <meshLambertMaterial color="#243140" />
    </mesh>
    <mesh position={[0, 0.72, 0.16]} castShadow receiveShadow
      onClick={(event) => { event.stopPropagation(); onPick(id, pointerKind(event)); }}
      onPointerOver={(event) => { event.stopPropagation(); if (pointerKind(event) !== "touch") onHover(id); }}>
      <boxGeometry args={[1.2, 0.06, 0.64]} />
      <meshLambertMaterial color={desk.present ? "#1c2836" : "#151b24"} />
    </mesh>
    {desk.present && <Character color={color} pose={desk.pose} />}
    <Lamp lamp={desk.lamp} glow={desk.present ? glow : 0} />
    <Props desk={desk} />
    {desk.plate === "OBSERVE" && <Html position={[0, 0.84, 0.42]} center zIndexRange={[12, 0]}>
      <span className="office-plate">OBSERVE</span>
    </Html>}
    <Html position={[0, 0.62, 0.5]} center zIndexRange={[8, 0]}>
      <span className="office-name" style={{ color }}>{deskLabel(lang, id)}</span>
    </Html>
    {showBubble && <Html position={[0, 1.55, 0]} center zIndexRange={[30, 0]}>
      <div className="office-bubble" role="status">{bubbleText(lang, id, desk)}</div>
    </Html>}
  </group>;
}
