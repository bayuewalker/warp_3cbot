/**
 * Pixel-art live office. Original rectangles drawn here. No third-party sprites and no CDN.
 * Decorative monitor marks are not prices. The wall line is only the series passed in.
 */
import { ambientShift } from "./ambient.ts";
import { DESK_COLOR, LAMP_COLOR } from "./layout.ts";
import { CHIEF, SEAT, walkPosition, type Delivery, type OfficeCues } from "./life.ts";
import type { DeskId, DeskState, Lamp } from "./office-state.ts";

export const SCENE_W = 168;
export const SCENE_H = 236;

export type PaintScene = {
  desks: Record<DeskId, DeskState>;
  cues: OfficeCues;
  day: number;
  seconds: number;
  motion: boolean;
  quote: { symbol: string; points: number[] } | null;
  walks: { from: DeskId; progress: number }[];
};

const DESK_BOX: Record<DeskId, { x: number; y: number; w: number }> = {
  SCAN: { x: 6, y: 72, w: 48 },
  ANALYST: { x: 58, y: 72, w: 48 },
  EXECUTOR: { x: 110, y: 68, w: 52 },
  PORTFOLIO: { x: 6, y: 124, w: 48 },
  XAU: { x: 58, y: 124, w: 48 },
  WATCHER: { x: 110, y: 124, w: 52 },
  AEGIS: { x: 6, y: 176, w: 48 },
  FORWARD: { x: 110, y: 176, w: 52 },
};

export function deskHit(x: number, y: number): DeskId | null {
  for (const id of Object.keys(DESK_BOX) as DeskId[]) {
    const box = DESK_BOX[id];
    if (x >= box.x && x <= box.x + box.w && y >= box.y && y <= box.y + 40) return id;
  }
  return null;
}

function box(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, color: string) {
  ctx.fillStyle = color;
  ctx.fillRect(Math.round(x), Math.round(y), w, h);
}

function chibi(ctx: CanvasRenderingContext2D, x: number, y: number, color: string, working: boolean, blink: boolean, stamp: boolean) {
  const arm = working ? 1 : 0;
  box(ctx, x + 2, y, 6, 4, "#f0c9a0");
  if (!blink) {
    box(ctx, x + 3, y + 1, 1, 1, "#1a1230");
    box(ctx, x + 6, y + 1, 1, 1, "#1a1230");
  }
  box(ctx, x + 1, y + 4, 8, 5, color);
  box(ctx, x + arm, y + 6, 2, 2, color);
  box(ctx, x + 7 - arm, y + 6, 2, 2, color);
  if (stamp) box(ctx, x + 8, y + 5, 3, 2, "#f4f1e6");
}

function monitors(ctx: CanvasRenderingContext2D, x: number, y: number, glow: string, step: number, motion: boolean) {
  for (let i = 0; i < 3; i += 1) {
    const sx = x + i * 12;
    box(ctx, sx, y, 10, 8, "#101622");
    box(ctx, sx + 1, y + 1, 8, 6, glow);
    const shift = motion ? (step + i) % 5 : 1;
    box(ctx, sx + 2, y + 2 + (shift % 3), 5, 1, "#071018");
  }
}

function plant(ctx: CanvasRenderingContext2D, x: number, y: number, sway: number) {
  box(ctx, x + sway, y, 6, 8, "#3c8a56");
  box(ctx, x + 1 + sway, y + 3, 4, 4, "#57a86e");
  box(ctx, x + 1, y + 8, 4, 3, "#8d5a3c");
}

export function paintOffice(ctx: CanvasRenderingContext2D, scene: PaintScene) {
  const shift = scene.motion ? ambientShift(scene.seconds) : { bob: 0, sway: 0, flicker: 1, blink: false, step: 0 };
  const night = scene.day < 0.35;
  ctx.imageSmoothingEnabled = false;
  box(ctx, 0, 0, SCENE_W, SCENE_H, night ? "#100e22" : "#1a1740");
  for (let x = 4; x < SCENE_W; x += 12) box(ctx, x, 0, 1, SCENE_H, night ? "#16132e" : "#221e4e");
  box(ctx, 2, 2, SCENE_W - 4, 2, scene.day > 0.5 ? "#ff4fd8" : "#7a3d78");
  box(ctx, 2, SCENE_H - 4, SCENE_W - 4, 2, "#3d8bff");
  box(ctx, 2, 2, 2, SCENE_H - 4, "#ff4fd8");
  box(ctx, SCENE_W - 4, 2, 2, SCENE_H - 4, "#3d8bff");

  const skyTop = scene.day > 0.6 ? "#8fd4f8" : scene.day > 0.25 ? "#6f93c4" : "#1a2748";
  const skyBottom = scene.day > 0.6 ? "#ffe3b8" : scene.day > 0.25 ? "#e7b07a" : "#243456";
  box(ctx, 8, 8, 28, 8, skyTop);
  box(ctx, 8, 16, 28, 8, skyBottom);
  box(ctx, 132, 8, 28, 8, skyTop);
  box(ctx, 132, 16, 28, 8, skyBottom);
  box(ctx, 40, 10, 8, 16, "#2a2048");
  box(ctx, 120, 10, 8, 16, "#2a2048");
  plant(ctx, 6, 30, scene.motion ? Math.round(shift.sway) : 0);
  plant(ctx, 150, 30, scene.motion ? -Math.round(shift.sway) : 0);

  box(ctx, 46, 8, 76, 40, "#0c1020");
  box(ctx, 48, 12, 72, 28, "#070b16");
  if (scene.quote && scene.quote.points.length >= 2) {
    const points = scene.quote.points;
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = max - min || 1;
    ctx.fillStyle = "#3dff9a";
    for (let i = 1; i < points.length; i += 1) {
      const x0 = 50 + ((i - 1) / (points.length - 1)) * 66;
      const y0 = 36 - ((points[i - 1] - min) / span) * 20;
      const x1 = 50 + (i / (points.length - 1)) * 66;
      const y1 = 36 - ((points[i] - min) / span) * 20;
      ctx.fillRect(Math.round(x1), Math.round(y1), 2, 2);
      if (Math.round(x1) !== Math.round(x0)) ctx.fillRect(Math.round((x0 + x1) / 2), Math.round((y0 + y1) / 2), 1, 1);
    }
  }
  ctx.fillStyle = "#d7fff6";
  ctx.font = "8px monospace";
  ctx.fillText(scene.quote && scene.quote.points.length ? scene.quote.symbol.slice(0, 10) : "—", 50, 18);

  const neon = night ? 0.55 + shift.flicker * 0.45 : 0.35 + shift.flicker * 0.2;
  ctx.globalAlpha = scene.motion ? neon : 0.7;
  box(ctx, 4, 56, SCENE_W - 8, 2, "#ff4fd8");
  box(ctx, 4, 60, SCENE_W - 8, 1, "#3d8bff");
  ctx.globalAlpha = 1;

  const walking = new Set(scene.walks.filter((walk) => walk.progress < 1 && walk.from !== CHIEF).map((walk) => walk.from));
  (Object.keys(DESK_BOX) as DeskId[]).forEach((id) => {
    const spot = DESK_BOX[id];
    const desk = scene.desks[id];
    const lamp = LAMP_COLOR[desk.lamp];
    const chief = id === CHIEF;
    box(ctx, spot.x, spot.y + 16, spot.w, 16, "#241c38");
    box(ctx, spot.x + 4, spot.y + 14, spot.w - 8, 6, "#3a3058");
    if (chief) {
      box(ctx, spot.x + spot.w - 10, spot.y + 8, 4, 6, scene.cues.phone ? "#5eb0ff" : "#8aa0c0");
      box(ctx, spot.x + 4, spot.y + 6, 4, 6, lamp);
    }
    if (desk.present !== false) {
      const glow = desk.lamp === "off" ? "#243044" : lamp;
      monitors(ctx, spot.x + 4, spot.y, glow, shift.step, scene.motion);
    }
    if (desk.present !== false && !walking.has(id)) {
      const working = scene.cues.typing.includes(id) || (id === "FORWARD" && scene.cues.stamp);
      const y = SEAT[id].y + (scene.motion ? Math.round(shift.bob) : 0);
      chibi(ctx, SEAT[id].x - 4, y, DESK_COLOR[id], working && (scene.motion ? shift.step % 2 === 0 : true), shift.blink, id === "FORWARD" && scene.cues.stamp);
    }
    if (id === "XAU" && scene.cues.sleep) {
      ctx.fillStyle = "#d7ecff";
      ctx.fillText("z", SEAT.XAU.x + 8, SEAT.XAU.y - 2);
    }
    if (id === "AEGIS" && scene.cues.alarm) {
      box(ctx, spot.x + 18, spot.y + 18, 8, 6, scene.motion && shift.step % 2 === 0 ? "#ff6b6b" : "#ffd0d0");
    }
    ctx.fillStyle = DESK_COLOR[id];
    ctx.fillText(id === "EXECUTOR" ? "CHIEF" : id === "FORWARD" ? "SCORE" : id.slice(0, 5), spot.x + 2, spot.y + 36);
  });

  box(ctx, 58, 188, 46, 16, "#4a3028");
  box(ctx, 62, 192, 16, 8, "#6b4034");
  box(ctx, 84, 192, 16, 8, "#6b4034");
  box(ctx, 8, 200, 14, 16, "#2a3148");
  box(ctx, 148, 168, 14, 40, "#1c2438");
  for (let i = 0; i < 6; i += 1) {
    const on = scene.motion ? (shift.step + i) % 4 !== 0 : i % 2 === 0;
    box(ctx, 151, 172 + i * 5, 8, 2, on ? (i % 2 ? "#3dff9a" : "#ff4fd8") : "#243044");
  }
  box(ctx, 136, 160, 10, 8, "#9aa4c0");
  box(ctx, 140, 154, 2, 6, "#c5d0de");
  box(ctx, 154, 160, 6, 10, "#d7dde8");

  for (const walk of scene.walks) {
    const at = walkPosition(walk.from, scene.motion ? walk.progress : 1);
    chibi(ctx, at.x - 4, at.y, DESK_COLOR[walk.from], false, shift.blink, false);
    box(ctx, at.x + 6, at.y + 6, 4, 3, "#f4f1e6");
  }

  ctx.fillStyle = "#9ad7ff";
  ctx.fillText(scene.day > 0.5 ? "DAY" : scene.day > 0.2 ? "DUSK" : "NIGHT", 70, 54);
}

export function lampOf(lamp: Lamp): string {
  return LAMP_COLOR[lamp];
}

export type { Delivery };
