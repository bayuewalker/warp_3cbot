import type { DeskId } from "./office-state.ts";

/** x/z of each desk. The camera sits on +z and looks toward the back wall. */
export const LAYOUT: Record<DeskId, { x: number; z: number }> = {
  SCAN: { x: -1.62, z: -1.65 },
  ANALYST: { x: 1.62, z: -1.65 },
  AEGIS: { x: -1.62, z: -0.15 },
  EXECUTOR: { x: 1.62, z: -0.15 },
  PORTFOLIO: { x: -1.62, z: 1.35 },
  WATCHER: { x: 1.62, z: 1.35 },
  XAU: { x: -1.62, z: 2.85 },
  FORWARD: { x: 1.62, z: 2.85 },
};

export const DESK_COLOR: Record<DeskId, string> = {
  SCAN: "#73e9c7",
  ANALYST: "#bbabff",
  AEGIS: "#ffd17e",
  EXECUTOR: "#ffa88b",
  PORTFOLIO: "#91cfff",
  WATCHER: "#f7a9ca",
  XAU: "#e2c56a",
  FORWARD: "#9ad7ff",
};

export const LAMP_COLOR = {
  off: "#3a4450",
  green: "#3dd68c",
  amber: "#e6b15a",
  red: "#ff6b6b",
  blue: "#5eb0ff",
} as const;
