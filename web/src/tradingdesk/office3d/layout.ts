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
  SCAN: "#ff5d6c",
  ANALYST: "#ff7eb6",
  AEGIS: "#ff9a3c",
  EXECUTOR: "#d5e6ff",
  PORTFOLIO: "#4db2ff",
  WATCHER: "#3ddc86",
  XAU: "#f0c14a",
  FORWARD: "#c084fc",
};

export const LAMP_COLOR = {
  off: "#3a4450",
  green: "#3dd68c",
  amber: "#e6b15a",
  red: "#ff6b6b",
  blue: "#5eb0ff",
} as const;
