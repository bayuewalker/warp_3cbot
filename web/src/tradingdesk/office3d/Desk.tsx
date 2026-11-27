import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import type { MeshLambertMaterial } from "three";
import type { Lang } from "../../i18n.ts";
import { Character } from "./Character.tsx";
import { DESK_COLOR, LAMP_COLOR, LAYOUT } from "./layout.ts";
import { Lamp } from "./Lamp.tsx";
import { Props } from "./Props.tsx";
import { ambientShift } from "./ambient.ts";
import { bubbleText, deskLabel } from "./office-copy.ts";
import type { DeskId, DeskState } from "./office-state.ts";

function Monitor({ color, glow, live }: { color: string; glow: number; live: boolean }) {
  const screen = useRef<MeshLambertMaterial>(null);
  useFrame((state) => {
    if (!screen.current) return;
    const flicker = live ? ambientShift(state.clock.elapsedTime).flicker : 1;
    screen.current.emissiveIntensity = glow * flicker;
  });
  return <group position={[0.34, 0.96, 0.08]}>
    <mesh position={[0, -0.12, 0]}>
      <boxGeometry args={[0.04, 0.16, 0.04]} />
      <meshLambertMaterial color="#2c3544" />
    </mesh>
    <mesh position={[0, 0.06, 0]}>
      <boxGeometry args={[0.36, 0.24, 0.03]} />
      <meshLambertMaterial ref={screen} color="#10161e" emissive={color} emissiveIntensity={glow} />
    </mesh>
  </group>;
}

export function Desk({
  id, desk, lang, glow, live, showBubble, onPick, onHover,
}: {
  id: DeskId;
  desk: DeskState;
  lang: Lang;
  glow: number;
  live: boolean;
  showBubble: boolean;
  onPick: (id: DeskId, pointerType: string) => void;
  onHover: (id: DeskId) => void;
}) {
  const color = DESK_COLOR[id];
  const screen = LAMP_COLOR[desk.lamp];
  const plateY = LAYOUT[id].x < 0 ? 1.58 : 1.28;
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
      <boxGeometry args={[1.15, 0.06, 0.64]} />
      <meshLambertMaterial color={desk.present ? "#c8b49a" : "#8d7d6a"} />
    </mesh>
    <group position={[0, 0, -0.52]}>
      <mesh position={[0, 0.46, 0]}>
        <boxGeometry args={[0.4, 0.06, 0.38]} />
        <meshLambertMaterial color="#5c4636" />
      </mesh>
      <mesh position={[0, 0.74, -0.16]}>
        <boxGeometry args={[0.4, 0.48, 0.06]} />
        <meshLambertMaterial color="#5c4636" />
      </mesh>
    </group>
    <mesh position={[0, 0.78, 0.02]}>
      <boxGeometry args={[0.28, 0.02, 0.1]} />
      <meshLambertMaterial color="#2a313c" />
    </mesh>
    <mesh position={[-0.38, 0.8, 0.2]}>
      <cylinderGeometry args={[0.045, 0.05, 0.07, 6]} />
      <meshLambertMaterial color="#f4f1ea" />
    </mesh>
    <Monitor color={screen} glow={desk.present ? Math.max(glow, 0.45) : 0.08} live={live && desk.present} />
    {desk.present && <Character color={color} pose={desk.pose} live={live} />}
    <Lamp lamp={desk.lamp} glow={desk.present ? glow : 0} />
    <Props desk={desk} />
    {desk.plate === "OBSERVE" && <Html position={[0, 0.9, 0.42]} center zIndexRange={[12, 0]}>
      <span className="office-plate">OBSERVE</span>
    </Html>}
    <Html position={[0, plateY, -0.2]} center sprite distanceFactor={12} zIndexRange={[8, 0]}>
      <span className="office-name" style={{ color }}>{deskLabel(lang, id)}</span>
    </Html>
    {showBubble && <Html position={[0, 1.85, 0]} center zIndexRange={[30, 0]}>
      <div className="office-bubble" role="status">{bubbleText(lang, id, desk)}</div>
    </Html>}
  </group>;
}
