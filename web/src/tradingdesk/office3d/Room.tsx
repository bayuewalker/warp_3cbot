import { wibLight } from "./lighting.ts";

function sky(day: number) {
  if (day > 0.6) return { top: "#8fd4f8", bottom: "#ffe3b8" };
  if (day > 0.25) return { top: "#6f93c4", bottom: "#e7b07a" };
  return { top: "#1a2748", bottom: "#243456" };
}

function Window({ position, day }: { position: [number, number, number]; day: number }) {
  const tone = sky(day);
  return <group position={position}>
    <mesh>
      <boxGeometry args={[1.35, 1.2, 0.08]} />
      <meshLambertMaterial color="#f6f1e8" />
    </mesh>
    <mesh position={[0, 0.22, 0.05]}>
      <planeGeometry args={[1.12, 0.46]} />
      <meshBasicMaterial color={tone.top} />
    </mesh>
    <mesh position={[0, -0.24, 0.05]}>
      <planeGeometry args={[1.12, 0.46]} />
      <meshBasicMaterial color={tone.bottom} />
    </mesh>
  </group>;
}

function Plant({ position }: { position: [number, number, number] }) {
  return <group position={position}>
    <mesh position={[0, 0.16, 0]}>
      <cylinderGeometry args={[0.11, 0.09, 0.26, 6]} />
      <meshLambertMaterial color="#8d5a3c" />
    </mesh>
    <mesh position={[0, 0.42, 0]}>
      <sphereGeometry args={[0.2, 8, 6]} />
      <meshLambertMaterial color="#3c8a56" />
    </mesh>
    <mesh position={[0.1, 0.56, 0.02]}>
      <sphereGeometry args={[0.13, 8, 6]} />
      <meshLambertMaterial color="#57a86e" />
    </mesh>
  </group>;
}

export function Room({ now, shadows }: { now: number; shadows: boolean }) {
  const day = wibLight(now).day;
  const wall = day > 0.4 ? "#f3ecdf" : "#2a3344";
  const floor = day > 0.4 ? "#d7b48a" : "#5a4534";
  const seam = day > 0.4 ? "#c4a078" : "#463628";
  const ambient = 0.32 + day * 0.5;
  const hemi = 0.46 + day * 0.62;
  const key = 0.5 + day * 1.2;
  return <group>
    <color attach="background" args={[day > 0.45 ? "#c5dff3" : "#101820"]} />
    <ambientLight color={day > 0.35 ? "#fff4e2" : "#c5d0de"} intensity={ambient} />
    <hemisphereLight args={[day > 0.35 ? "#ffe7c2" : "#8ea4be", "#c4a574", hemi]} />
    <directionalLight
      position={[2.2, 8.4, 5.2]}
      intensity={key}
      color={day > 0.35 ? "#fff6e4" : "#9eb4cc"}
      castShadow={shadows}
      shadow-mapSize-width={512}
      shadow-mapSize-height={512}
    />
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.04, 1.6]} receiveShadow>
      <planeGeometry args={[10.4, 13.2]} />
      <meshLambertMaterial color={floor} />
    </mesh>
    {[-2.4, -0.8, 0.8, 2.4].map((x) => <mesh key={x} rotation={[-Math.PI / 2, 0, 0]} position={[x, -0.035, 1.6]}>
      <planeGeometry args={[0.07, 13.2]} />
      <meshLambertMaterial color={seam} />
    </mesh>)}
    <mesh position={[0, 1.55, -3.7]}>
      <boxGeometry args={[10.4, 3.3, 0.12]} />
      <meshLambertMaterial color={wall} />
    </mesh>
    <mesh position={[-4.9, 1.55, 1.6]}>
      <boxGeometry args={[0.12, 3.3, 13.2]} />
      <meshLambertMaterial color={wall} />
    </mesh>
    <mesh position={[4.9, 1.55, 1.6]}>
      <boxGeometry args={[0.12, 3.3, 13.2]} />
      <meshLambertMaterial color={wall} />
    </mesh>
    <Window position={[-2.85, 1.85, -3.58]} day={day} />
    <Window position={[2.85, 1.85, -3.58]} day={day} />
    {[-2.4, 0.1, 2.6].map((x) => <mesh key={x} position={[x, 3.05, 0.5]}>
      <boxGeometry args={[0.9, 0.05, 0.22]} />
      <meshLambertMaterial color="#fff6dd" emissive="#ffe7b0" emissiveIntensity={0.2 + (1 - day) * 1.1} />
    </mesh>)}
    <Plant position={[-2.35, 0, -2.85]} />
    <Plant position={[2.35, 0, -2.85]} />
    <Plant position={[-2.2, 0, 4.35]} />
    <group position={[2.05, 0, 4.2]}>
      <mesh position={[0, 0.55, 0]}>
        <boxGeometry args={[0.38, 1.05, 0.36]} />
        <meshLambertMaterial color="#d7e6f0" />
      </mesh>
      <mesh position={[0, 1.15, 0.14]}>
        <boxGeometry args={[0.1, 0.08, 0.08]} />
        <meshLambertMaterial color="#7ec8c0" />
      </mesh>
    </group>
    <group position={[-2.05, 0, 4.25]}>
      <mesh position={[0, 0.42, 0]}>
        <boxGeometry args={[0.72, 0.8, 0.4]} />
        <meshLambertMaterial color="#6d4b33" />
      </mesh>
      <mesh position={[-0.14, 0.86, 0.08]}>
        <cylinderGeometry args={[0.055, 0.06, 0.09, 6]} />
        <meshLambertMaterial color="#f4f1ea" />
      </mesh>
      <mesh position={[0.16, 0.9, 0.05]}>
        <cylinderGeometry args={[0.08, 0.09, 0.16, 8]} />
        <meshLambertMaterial color="#2c2622" />
      </mesh>
    </group>
  </group>;
}
