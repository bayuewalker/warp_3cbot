import { wibLight } from "./lighting.ts";

export function Room({ now, shadows }: { now: number; shadows: boolean }) {
  const day = wibLight(now).day;
  const hemi = 0.28 + day * 0.62;
  const dir = 0.34 + day * 1.05;
  const color = day > 0.55 ? "#f2f7ff" : "#8ea0b4";
  return <group>
    <color attach="background" args={[day > 0.45 ? "#243044" : "#07090d"]} />
    <hemisphereLight args={["#c5d7ea", "#1a140e", hemi]} />
    <directionalLight
      position={[3.2, 7.4, 4.2]}
      intensity={dir}
      color={color}
      castShadow={shadows}
      shadow-mapSize-width={512}
      shadow-mapSize-height={512}
    />
    <mesh position={[0, -0.04, 0.6]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
      <planeGeometry args={[8.4, 7.4]} />
      <meshLambertMaterial color={day > 0.45 ? "#314158" : "#10141c"} />
    </mesh>
    <mesh position={[0, 1.5, -3.05]}>
      <boxGeometry args={[8.4, 3.4, 0.12]} />
      <meshLambertMaterial color="#161d28" />
    </mesh>
  </group>;
}
