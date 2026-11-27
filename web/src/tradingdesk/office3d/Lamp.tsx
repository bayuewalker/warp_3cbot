import { LAMP_COLOR } from "./layout.ts";
import type { Lamp as LampColor } from "./office-state.ts";

export function Lamp({ lamp, glow }: { lamp: LampColor; glow: number }) {
  const color = LAMP_COLOR[lamp];
  return     <mesh position={[0.46, 0.92, -0.18]} castShadow>
    <cylinderGeometry args={[0.07, 0.09, 0.22, 8]} />
    <meshLambertMaterial color={color} emissive={color} emissiveIntensity={glow} />
  </mesh>;
}
