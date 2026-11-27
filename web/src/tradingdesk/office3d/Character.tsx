import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import type { Group } from "three";
import { ambientShift } from "./ambient.ts";
import type { Pose } from "./office-state.ts";

const ARM: Record<Pose, { ly: number; ry: number; lz: number; rz: number; headY: number; headZ: number }> = {
  idle: { ly: 0.9, ry: 0.9, lz: 0, rz: 0, headY: 1.22, headZ: 0 },
  typing: { ly: 0.84, ry: 0.84, lz: 0.2, rz: 0.2, headY: 1.14, headZ: 0.08 },
  thinking: { ly: 0.9, ry: 1.12, lz: 0, rz: 0.08, headY: 1.24, headZ: -0.02 },
  coffee: { ly: 0.9, ry: 1.05, lz: 0, rz: 0.14, headY: 1.22, headZ: 0 },
  alert: { ly: 1.12, ry: 1.12, lz: 0.02, rz: 0.02, headY: 1.26, headZ: 0 },
  sleeping: { ly: 0.76, ry: 0.76, lz: 0.22, rz: 0.22, headY: 1.0, headZ: 0.16 },
  warming: { ly: 0.96, ry: 0.96, lz: 0.06, rz: 0.06, headY: 1.2, headZ: 0 },
  stamping: { ly: 0.88, ry: 0.8, lz: 0.06, rz: 0.26, headY: 1.12, headZ: 0.08 },
  phone: { ly: 0.9, ry: 1.18, lz: 0, rz: 0.06, headY: 1.22, headZ: 0 },
  away: { ly: 0, ry: 0, lz: 0, rz: 0, headY: 0, headZ: 0 },
  unknown: { ly: 0.9, ry: 0.9, lz: 0, rz: 0, headY: 1.22, headZ: 0 },
};

export function Character({ color, pose, live }: { color: string; pose: Pose; live: boolean }) {
  const group = useRef<Group>(null);
  const head = useRef<Group>(null);
  useFrame((state) => {
    if (!group.current || !head.current) return;
    if (!live) {
      group.current.position.y = 0;
      head.current.rotation.y = 0;
      return;
    }
    const shift = ambientShift(state.clock.elapsedTime);
    group.current.position.y = shift.bob;
    head.current.rotation.y = shift.head;
  });
  if (pose === "away") return null;
  const arm = ARM[pose];
  return <group ref={group}>
    <mesh position={[-0.09, 0.46, -0.22]}>
      <boxGeometry args={[0.09, 0.28, 0.09]} />
      <meshLambertMaterial color="#3a4250" />
    </mesh>
    <mesh position={[0.09, 0.46, -0.22]}>
      <boxGeometry args={[0.09, 0.28, 0.09]} />
      <meshLambertMaterial color="#3a4250" />
    </mesh>
    <mesh position={[0, 0.78, -0.28]} castShadow>
      <boxGeometry args={[0.34, 0.38, 0.18]} />
      <meshLambertMaterial color={color} />
    </mesh>
    <group ref={head} position={[0, arm.headY, -0.28 + arm.headZ]}>
      <mesh castShadow>
        <sphereGeometry args={[0.13, 8, 6]} />
        <meshLambertMaterial color="#f0c9a8" />
      </mesh>
      <mesh position={[0, 0.07, 0]}>
        <boxGeometry args={[0.2, 0.08, 0.18]} />
        <meshLambertMaterial color={color} />
      </mesh>
    </group>
    <mesh position={[-0.24, arm.ly, -0.28 + arm.lz]} castShadow>
      <boxGeometry args={[0.08, 0.24, 0.08]} />
      <meshLambertMaterial color={color} />
    </mesh>
    <mesh position={[0.24, arm.ry, -0.28 + arm.rz]} castShadow>
      <boxGeometry args={[0.08, 0.24, 0.08]} />
      <meshLambertMaterial color={color} />
    </mesh>
    {pose === "coffee" && <mesh position={[0.24, 1.02, -0.08]}>
      <cylinderGeometry args={[0.04, 0.045, 0.08, 6]} />
      <meshLambertMaterial color="#d7c4a3" />
    </mesh>}
  </group>;
}
