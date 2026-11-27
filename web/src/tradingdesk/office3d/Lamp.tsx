import { LAMP_COLOR } from "./layout.ts";
import type { Lamp as LampColor } from "./office-state.ts";

export function Lamp({ lamp, glow }: { lamp: LampColor; glow: number }) {
  const color = LAMP_COLOR[lamp];
  return <group>
    <mesh position={[0.46, 0.92, -0.18]} castShadow>
      <cylinderGeometry args={[0.07, 0.09, 0.22, 8]} />
      <meshLambertMaterial color={color} emissive={color} emissiveIntensity={glow} />
    </mesh>
    {glow > 0 && <pointLight position={[0.46, 1.15, -0.05]} color={color} intensity={glow * 0.45} distance={1.6} decay={2} />}
  </group>;
}
