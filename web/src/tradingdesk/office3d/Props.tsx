import type { DeskState } from "./office-state.ts";

export function Props({ desk }: { desk: DeskState }) {
  return <group>
    {desk.prop === "paper_ticket_observe" && <mesh position={[0.05, 0.8, 0.12]}>
      <boxGeometry args={[0.22, 0.012, 0.14]} />
      <meshLambertMaterial color="#f4f1e6" />
    </mesh>}
    {desk.prop === "order_flag" && <group position={[0.28, 0.92, 0.05]}>
      <mesh><boxGeometry args={[0.02, 0.28, 0.02]} /><meshLambertMaterial color="#c9d4de" /></mesh>
      <mesh position={[0.06, 0.1, 0]}><boxGeometry args={[0.12, 0.08, 0.012]} /><meshLambertMaterial color="#ffb25e" /></mesh>
    </group>}
    {desk.prop === "signal_flag" && <group position={[-0.28, 0.92, 0.05]}>
      <mesh><boxGeometry args={[0.02, 0.28, 0.02]} /><meshLambertMaterial color="#c9d4de" /></mesh>
      <mesh position={[-0.06, 0.1, 0]}><boxGeometry args={[0.12, 0.08, 0.012]} /><meshLambertMaterial color="#3ecfbf" /></mesh>
    </group>}
    {desk.prop === "alarm" && <mesh position={[0, 0.96, 0]}>
      <boxGeometry args={[0.12, 0.08, 0.12]} />
      <meshLambertMaterial color="#ff6b6b" emissive="#ff6b6b" emissiveIntensity={0.7} />
    </mesh>}
    {desk.prop === "scorecard" && <mesh position={[0.02, 0.8, 0.1]} rotation={[-0.4, 0, 0]}>
      <boxGeometry args={[0.2, 0.26, 0.012]} />
      <meshLambertMaterial color="#e7f2ef" />
    </mesh>}
    {Array.from({ length: desk.stack }, (_, index) => <mesh key={index} position={[-0.22, 0.79 + index * 0.018, 0.08]}>
      <boxGeometry args={[0.16, 0.012, 0.11]} />
      <meshLambertMaterial color={index % 2 ? "#d5ddd8" : "#f7f4ea"} />
    </mesh>)}
  </group>;
}
