/**
 * Pixel-art live office. Original rectangles drawn here. No third-party sprites and no CDN.
 * Desk-monitor marks are decorative and are not prices.
 * The wall line is only the series passed in. Fewer than two points is a label, not a candle.
 */
import { ambientShift } from "./ambient.ts";
import { DESK_COLOR, LAMP_COLOR } from "./layout.ts";
import { CHIEF, SEAT, walkPosition, type OfficeCues } from "./life.ts";
import { PLATE } from "./office-copy.ts";
import type { DeskId, DeskState, Lamp } from "./office-state.ts";

export const SCENE_W = 320;
export const SCENE_H = 468;

export type PaintScene = {
  desks: Record<DeskId, DeskState>;
  cues: OfficeCues;
  day: number;
  seconds: number;
  motion: boolean;
  quote: { symbol: string; points: number[] } | null;
  walks: { from: DeskId; progress: number }[];
};

const PLACE: Record<DeskId, { x: number; y: number; w: number }> = {
  SCAN: { x: 8, y: 140, w: 96 },
  ANALYST: { x: 112, y: 140, w: 96 },
  EXECUTOR: { x: 214, y: 132, w: 98 },
  PORTFOLIO: { x: 8, y: 242, w: 96 },
  XAU: { x: 112, y: 242, w: 96 },
  WATCHER: { x: 214, y: 242, w: 98 },
  AEGIS: { x: 36, y: 344, w: 68 },
  FORWARD: { x: 196, y: 344, w: 64 },
};

type Look = { suit: string; shade: string; head: string; visor: string; shape: "round" | "box" | "helm"; antenna: boolean };

const LOOK: Record<DeskId, Look> = {
  SCAN: { suit: "#e24b58", shade: "#ff8d96", head: "#ffd3c2", visor: "#7ef0ff", shape: "box", antenna: true },
  ANALYST: { suit: "#e45b9a", shade: "#ffb3d4", head: "#ffd0e4", visor: "#fff4fb", shape: "round", antenna: true },
  AEGIS: { suit: "#e07a24", shade: "#ffc078", head: "#f0b070", visor: "#ffe08a", shape: "helm", antenna: false },
  EXECUTOR: { suit: "#c5d4ee", shade: "#f4f8ff", head: "#e7eefc", visor: "#4aa3ff", shape: "round", antenna: false },
  PORTFOLIO: { suit: "#2f86d6", shade: "#8fd0ff", head: "#b9ddff", visor: "#e8f6ff", shape: "round", antenna: true },
  WATCHER: { suit: "#1fa864", shade: "#8cf0b8", head: "#c8f5dc", visor: "#f3fff8", shape: "round", antenna: true },
  XAU: { suit: "#d4a017", shade: "#ffe08a", head: "#f6d56a", visor: "#fff6d0", shape: "box", antenna: false },
  FORWARD: { suit: "#8b5cf6", shade: "#d4b8ff", head: "#e4d4ff", visor: "#f6f0ff", shape: "round", antenna: true },
};

type Pose = "idle" | "typing" | "phone" | "stamp" | "sleep" | "walk";

export function deskHit(x: number, y: number): DeskId | null {
  for (const id of Object.keys(PLACE) as DeskId[]) {
    const box = PLACE[id];
    if (x >= box.x && x <= box.x + box.w && y >= box.y && y <= box.y + 78) return id;
  }
  return null;
}

function px(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, color: string) {
  ctx.fillStyle = color;
  ctx.fillRect(Math.round(x), Math.round(y), w, h);
}

function bot(ctx: CanvasRenderingContext2D, x: number, y: number, id: DeskId, pose: Pose, frame: number, blink: boolean) {
  const look = LOOK[id];
  const breath = pose === "idle" && frame % 2 === 1 ? -1 : 0;
  const headY = (pose === "sleep" ? y + 5 : y) + breath;
  px(ctx, x + 5, headY + 16, 18, 14, "#5c6b84");
  px(ctx, x + 3, headY + 26, 22, 6, "#3e4a60");
  px(ctx, x + 6, headY + 32, 16, 2, "#2a3344");
  px(ctx, x + 3, headY + 34, 4, 2, "#1a2030");
  px(ctx, x + 21, headY + 34, 4, 2, "#1a2030");
  if (pose !== "sleep" && look.antenna) {
    px(ctx, x + 12, headY - 5, 2, 5, look.visor);
    px(ctx, x + 10, headY - 7, 6, 3, look.visor);
  }
  if (look.shape === "round") {
    px(ctx, x + 8, headY, 12, 2, look.head);
    px(ctx, x + 6, headY + 2, 16, 10, look.head);
    px(ctx, x + 8, headY + 12, 12, 2, look.head);
  } else if (look.shape === "helm") {
    px(ctx, x + 6, headY + 2, 16, 11, look.head);
    px(ctx, x + 3, headY + 7, 22, 3, look.shade);
  } else {
    px(ctx, x + 6, headY, 16, 14, look.head);
  }
  if (pose === "sleep" || blink) {
    px(ctx, x + 8, headY + 7, 4, 1, "#241820");
    px(ctx, x + 15, headY + 7, 4, 1, "#241820");
  } else {
    px(ctx, x + 8, headY + 6, 12, 4, "#10141c");
    px(ctx, x + 9, headY + 7, 10, 2, look.visor);
    px(ctx, x + 10, headY + 7, 2, 1, "#f7fbff");
  }
  px(ctx, x + 7, headY + 15, 14, 10, look.suit);
  px(ctx, x + 9, headY + 16, 4, 6, look.shade);
  if (pose === "typing") {
    const arm = frame % 2;
    px(ctx, x + 1, headY + 18 + arm, 6, 3, look.suit);
    px(ctx, x + 21, headY + 19 - arm, 6, 3, look.suit);
  } else if (pose === "phone") {
    px(ctx, x + 20, headY + 4, 5, 9, look.suit);
    px(ctx, x + 20, headY + 2, 7, 5, "#e8eef8");
    px(ctx, x + 21, headY + 3, 5, 2, "#1a2030");
    px(ctx, x + 1, headY + 12 - (frame % 2) * 4, 5, 3, look.suit);
  } else if (pose === "stamp") {
    px(ctx, x + 20, headY + 14, 8, 4, look.suit);
    px(ctx, x + 26, headY + 11, 6, 6, "#f4f0e4");
    px(ctx, x + 27, headY + 13, 4, 2, "#e23b4a");
  } else if (pose === "walk") {
    const leg = frame % 4;
    px(ctx, x + 2, headY + 16, 4, 6, look.suit);
    px(ctx, x + 22, headY + 16, 4, 6, look.suit);
    px(ctx, x + 8, headY + 25, 4, leg < 2 ? 6 : 3, look.shade);
    px(ctx, x + 16, headY + 25, 4, leg >= 2 ? 6 : 3, look.shade);
  } else {
    px(ctx, x + 2, headY + 16, 4, 8, look.suit);
    px(ctx, x + 22, headY + 16, 4, 8, look.suit);
  }
  if (pose === "sleep") {
    px(ctx, x + 24, headY - 1, 2, 2, "#d7ecff");
    px(ctx, x + 28, headY - 6, 3, 3, "#d7ecff");
  }
}

function screenArt(ctx: CanvasRenderingContext2D, x: number, y: number, kind: number, step: number, glow: string) {
  if (kind === 0) {
    for (let i = 0; i < 4; i += 1) {
      const up = (i + step) % 2 === 0;
      const bx = x + 2 + i * 4;
      const by = y + (up ? 2 : 5);
      px(ctx, bx + 1, y + 1, 1, 8, glow);
      px(ctx, bx, by, 3, up ? 4 : 3, up ? "#3dff9a" : "#ff5d6c");
    }
    return;
  }
  if (kind === 1) {
    for (let i = 0; i < 14; i += 1) {
      const wave = Math.round(Math.sin((i + step) * 0.7) * 3);
      px(ctx, x + 2 + i, y + 6 - wave, 2, 2, glow);
    }
    return;
  }
  for (let row = 0; row < 4; row += 1) {
    const width = 4 + ((row + step) % 5) * 2;
    px(ctx, x + 2, y + 2 + row * 3, width, 1, glow);
  }
}

function monitors(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, glow: string, step: number, motion: boolean) {
  const mw = 22;
  const ox = x + Math.round((w - (mw * 3 + 4)) / 2);
  for (let i = 0; i < 3; i += 1) {
    const mx = ox + i * (mw + 2);
    px(ctx, mx + 8, y + 20, 6, 4, "#141820");
    px(ctx, mx + 4, y + 23, 14, 2, "#141820");
    px(ctx, mx, y, mw, 18, "#0c1018");
    px(ctx, mx + 1, y + 1, mw - 2, 14, "#070b12");
    screenArt(ctx, mx + 2, y + 2, i, motion ? step : 0, glow);
    ctx.globalAlpha = motion ? 0.35 : 0.22;
    px(ctx, mx, y + 24, mw, 3, glow);
    ctx.globalAlpha = 1;
  }
  px(ctx, x + Math.round(w / 2) - 16, y + 36, 26, 5, "#12161f");
  px(ctx, x + Math.round(w / 2) + 14, y + 37, 7, 4, "#12161f");
}

function plate(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, label: string, color: string) {
  ctx.font = "8px monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const tw = Math.ceil(ctx.measureText(label).width);
  const pw = Math.min(w, tw + 8);
  const left = x + Math.round((w - pw) / 2);
  px(ctx, left, y, pw, 11, "#120c1c");
  px(ctx, left, y + 10, pw, 1, color);
  ctx.fillStyle = color;
  ctx.fillText(label, left + pw / 2, y + 1);
  ctx.textAlign = "left";
}

function plant(ctx: CanvasRenderingContext2D, x: number, y: number, sway: number) {
  const lean = sway;
  px(ctx, x + 5, y + 18, 10, 8, "#c47a4a");
  px(ctx, x + 6, y + 16, 8, 3, "#8d5a32");
  px(ctx, x + 8, y + 8, 3, 10, "#2f6b3a");
  px(ctx, x + lean, y + 4, 8, 7, "#3f9a56");
  px(ctx, x + 8 + lean, y, 8, 7, "#67d484");
  px(ctx, x + 4 + lean, y + 8, 6, 5, "#2f8a48");
  px(ctx, x + 12 + lean, y + 7, 5, 4, "#8ee8a4");
}

function banner(ctx: CanvasRenderingContext2D, x: number, y: number) {
  px(ctx, x + 4, y, 2, 4, "#8aa0c0");
  px(ctx, x, y + 4, 10, 28, "#241433");
  px(ctx, x, y + 32, 10, 4, "#ff4fd8");
  px(ctx, x + 2, y + 10, 6, 6, "#f4f1ea");
  px(ctx, x + 3, y + 12, 4, 2, "#241433");
  px(ctx, x + 4, y + 18, 2, 6, "#f4f1ea");
}

function wallScreen(ctx: CanvasRenderingContext2D, quote: PaintScene["quote"]) {
  px(ctx, 54, 16, 212, 100, "#1b2436");
  px(ctx, 58, 20, 204, 92, "#070b12");
  ctx.font = "8px monospace";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillStyle = "#d7fff2";
  const symbol = quote?.symbol ? quote.symbol.slice(0, 12) : "—";
  ctx.fillText(symbol + " session", 64, 24);
  px(ctx, 168, 24, 22, 8, "#123222");
  ctx.fillStyle = "#8dffc0";
  ctx.fillText("live", 170, 24);
  const points = quote?.points ?? [];
  if (points.length >= 2) {
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = max - min || 1;
    for (let i = 1; i < points.length; i += 1) {
      const x0 = 66 + ((i - 1) / (points.length - 1)) * 150;
      const y0 = 88 - ((points[i - 1] - min) / span) * 48;
      const x1 = 66 + (i / (points.length - 1)) * 150;
      const y1 = 88 - ((points[i] - min) / span) * 48;
      px(ctx, x1, y1, 2, 2, "#3dff9a");
      const steps = Math.max(1, Math.abs(Math.round(x1) - Math.round(x0)));
      for (let s = 0; s < steps; s += 2) {
        const u = s / steps;
        px(ctx, x0 + (x1 - x0) * u, y0 + (y1 - y0) * u, 1, 1, "#1f8a56");
      }
    }
    ctx.fillStyle = "#9ad7ff";
    ctx.textAlign = "right";
    ctx.fillText(tick(max), 256, 40);
    ctx.fillText(tick(min), 256, 84);
    ctx.textAlign = "left";
  } else {
    ctx.fillStyle = "#9ad7ff";
    ctx.fillText("collecting quotes", 78, 52);
    ctx.fillStyle = "#d7fff2";
    ctx.font = "16px monospace";
    ctx.fillText("—", 150, 68);
    ctx.font = "8px monospace";
  }
  ctx.fillStyle = "#6f7c90";
  ctx.fillText(points.length >= 2 ? "session · " + points.length : "collecting quotes", 64, 98);
}

function tick(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1000) return String(Math.round(value));
  if (abs >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function poseOf(id: DeskId, cues: OfficeCues, walking: boolean): Pose {
  if (walking) return "walk";
  if (id === "WATCHER" && cues.phone) return "phone";
  if (id === "FORWARD" && cues.stamp) return "stamp";
  if (id === "XAU" && cues.sleep) return "sleep";
  if (cues.typing.includes(id)) return "typing";
  return "idle";
}

export function paintOffice(ctx: CanvasRenderingContext2D, scene: PaintScene) {
  const shift = scene.motion ? ambientShift(scene.seconds) : { bob: 0, sway: 0, flicker: 0.8, blink: false, step: 0 };
  const night = 1 - Math.min(1, Math.max(0, scene.day));
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, SCENE_W, SCENE_H);
  const tileA = night > 0.55 ? "#141226" : "#2a2454";
  const tileB = night > 0.55 ? "#1b1730" : "#342c62";
  for (let y = 128; y < SCENE_H; y += 16) {
    for (let x = 0; x < SCENE_W; x += 16) {
      px(ctx, x, y, 16, 16, ((x + y) / 16) % 2 === 0 ? tileA : tileB);
    }
  }
  ctx.globalAlpha = night > 0.55 ? 0.18 : 0.28;
  for (let x = 0; x < SCENE_W; x += 16) px(ctx, x, 128, 1, SCENE_H - 128, "#b388ff");
  ctx.globalAlpha = 1;

  const brickA = night > 0.55 ? "#1a1e2e" : "#2a3148";
  const brickB = night > 0.55 ? "#232838" : "#343c58";
  for (let row = 0; row < 8; row += 1) {
    const y = row * 16;
    const offset = row % 2 ? 8 : 0;
    for (let x = -8; x < SCENE_W; x += 18) px(ctx, x + offset, y, 16, 14, (row + x) % 2 === 0 ? brickA : brickB);
  }
  const neon = scene.motion ? shift.flicker : 0.75;
  ctx.globalAlpha = 0.35 + neon * 0.4;
  px(ctx, 8, 8, SCENE_W - 16, 3, "#3d8bff");
  px(ctx, 8, 118, SCENE_W - 16, 3, "#ff4fd8");
  ctx.globalAlpha = 0.18 + neon * 0.15;
  px(ctx, 8, 5, SCENE_W - 16, 8, "#3d8bff");
  px(ctx, 8, 115, SCENE_W - 16, 8, "#ff4fd8");
  ctx.globalAlpha = 1;
  banner(ctx, 16, 28);
  banner(ctx, 294, 28);
  plant(ctx, 8, 86, scene.motion ? Math.round(shift.sway) : 0);
  plant(ctx, 296, 86, scene.motion ? -Math.round(shift.sway) : 0);
  wallScreen(ctx, scene.quote);

  px(ctx, 4, 4, 10, 2, "#ff4fd8");
  px(ctx, 4, 4, 2, 10, "#ff4fd8");
  px(ctx, SCENE_W - 14, 4, 10, 2, "#ff4fd8");
  px(ctx, SCENE_W - 6, 4, 2, 10, "#ff4fd8");
  px(ctx, 4, SCENE_H - 6, 10, 2, "#3d8bff");
  px(ctx, 4, SCENE_H - 14, 2, 10, "#3d8bff");
  px(ctx, SCENE_W - 14, SCENE_H - 6, 10, 2, "#3d8bff");
  px(ctx, SCENE_W - 6, SCENE_H - 14, 2, 10, "#3d8bff");

  const walking = new Set(scene.walks.filter((walk) => walk.progress < 1 && walk.from !== CHIEF).map((walk) => walk.from));
  for (const id of Object.keys(PLACE) as DeskId[]) {
    const spot = PLACE[id];
    const desk = scene.desks[id];
    const glow = desk.lamp === "off" ? "#243044" : LAMP_COLOR[desk.lamp];
    px(ctx, spot.x, spot.y + 30, spot.w, 20, "#2c3548");
    px(ctx, spot.x, spot.y + 30, spot.w, 3, "#46506a");
    px(ctx, spot.x + 6, spot.y + 42, spot.w - 16, 6, "#1a2030");
    px(ctx, spot.x + 10, spot.y + 44, 8, 2, "#8aa0c0");
    if (desk.present !== false) monitors(ctx, spot.x, spot.y + 12, spot.w, glow, shift.step, scene.motion);
    if (id === CHIEF) {
      px(ctx, spot.x + spot.w - 16, spot.y + 36, 8, 6, "#f0d48a");
      ctx.globalAlpha = night > 0.4 ? 0.45 : 0.22;
      px(ctx, spot.x + spot.w - 20, spot.y + 34, 16, 10, "#f0d48a");
      ctx.globalAlpha = 1;
      const ringing = scene.cues.phone && (scene.motion ? shift.step % 2 === 0 : true);
      px(ctx, spot.x + 8, spot.y + 36, 10, 6, ringing ? "#5eb0ff" : "#9aa8c0");
    }
  }

  px(ctx, 4, 400, 28, 40, "#b7becc");
  px(ctx, 6, 404, 24, 26, "#8e96a8");
  px(ctx, 12, 412, 10, 10, scene.cues.alarm && (scene.motion ? shift.step % 2 === 0 : true) ? "#ff5d6c" : "#eef2f8");
  px(ctx, 8, 430, 20, 3, "#d5dbe6");
  px(ctx, 4, 352, 24, 44, "#4a5368");
  px(ctx, 6, 358, 20, 14, "#323a4c");
  px(ctx, 6, 376, 20, 14, "#323a4c");
  px(ctx, 10, 362, 10, 2, "#c5cad6");
  px(ctx, 10, 380, 10, 2, "#c5cad6");

  px(ctx, 104, 392, 26, 20, "#6a4034");
  px(ctx, 106, 394, 22, 10, "#c48a6a");
  px(ctx, 104, 408, 26, 6, "#4a2c24");
  px(ctx, 168, 392, 26, 20, "#6a4034");
  px(ctx, 170, 394, 22, 10, "#c48a6a");
  px(ctx, 168, 408, 26, 6, "#4a2c24");
  px(ctx, 132, 398, 28, 14, "#2c241c");
  px(ctx, 136, 400, 20, 8, "#3d342c");
  px(ctx, 112, 422, 72, 22, "#5c3428");
  px(ctx, 116, 426, 18, 10, "#d09a78");
  px(ctx, 138, 426, 18, 10, "#e0b090");
  px(ctx, 160, 426, 18, 10, "#d09a78");
  plant(ctx, 86, 400, scene.motion ? Math.round(shift.sway) : 0);
  plant(ctx, 196, 400, scene.motion ? -Math.round(shift.sway) : 0);

  px(ctx, 292, 360, 6, 28, "#9aa4b8");
  px(ctx, 278, 352, 28, 10, "#d5deea");
  px(ctx, 284, 346, 10, 8, "#9aa4b8");
  px(ctx, 300, 378, 10, 28, "#f4f7fb");
  px(ctx, 298, 370, 14, 8, "#ff5d6c");
  px(ctx, 296, 404, 5, 8, "#f4f7fb");
  px(ctx, 310, 404, 5, 8, "#f4f7fb");
  px(ctx, 304, 348, 14, 112, "#171d2c");
  for (let i = 0; i < 8; i += 1) {
    const on = scene.motion ? (shift.step + i) % 4 === 0 : i % 2 === 0;
    px(ctx, 306, 354 + i * 12, 10, 4, on ? (i % 2 ? "#3dff9a" : "#ff4fd8") : "#243044");
  }

  for (const id of Object.keys(PLACE) as DeskId[]) {
    if (scene.desks[id].present === false || walking.has(id)) continue;
    const pose = poseOf(id, scene.cues, false);
    const y = SEAT[id].y + (scene.motion && pose === "idle" ? Math.round(shift.bob) : 0);
    bot(ctx, SEAT[id].x, y, id, pose, shift.step, shift.blink && pose === "idle");
  }
  for (const walk of scene.walks) {
    if (walk.from === CHIEF) {
      px(ctx, SEAT[CHIEF].x + 20, SEAT[CHIEF].y + 20, 8, 6, "#f4f1e6");
      continue;
    }
    const at = walkPosition(walk.from, scene.motion ? walk.progress : 1);
    bot(ctx, at.x, at.y, walk.from, scene.motion && walk.progress < 1 ? "walk" : "idle", shift.step, false);
    px(ctx, at.x + 22, at.y + 16, 8, 6, "#f4f1e6");
  }

  if (night > 0.08) {
    ctx.globalAlpha = night * 0.38;
    px(ctx, 0, 128, SCENE_W, SCENE_H - 128, "#070814");
    ctx.globalAlpha = 1;
  }

  for (const id of Object.keys(PLACE) as DeskId[]) {
    const spot = PLACE[id];
    plate(ctx, spot.x, spot.y, spot.w, PLATE[id], DESK_COLOR[id]);
  }
}

export function lampOf(lamp: Lamp): string {
  return LAMP_COLOR[lamp];
}
