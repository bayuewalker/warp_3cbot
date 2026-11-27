/**
 * Idle offsets for the render clock. Amplitude only.
 * This module does not read desk state.
 * Original timing helper. No third-party art.
 */
const DEFAULT_PERIOD_S = 1 / 30;

/**
 * Delay until the next draw.
 * A deadline that is already due waits a full period, so a late draw does not schedule the next one immediately.
 */
export function ambientFrameDelay(nowSeconds: number, deadlineSeconds: number, periodSeconds = DEFAULT_PERIOD_S): { waitMs: number; deadline: number } {
  const now = Number.isFinite(nowSeconds) ? nowSeconds : 0;
  const due = Number.isFinite(deadlineSeconds) ? deadlineSeconds : 0;
  const period = Number.isFinite(periodSeconds) && periodSeconds > 0 ? periodSeconds : DEFAULT_PERIOD_S;
  const deadline = due > now ? due : now + period;
  return { waitMs: (deadline - now) * 1000, deadline };
}

export function ambientShift(seconds: number): { bob: number; sway: number; flicker: number; blink: boolean; step: number } {
  const t = Number.isFinite(seconds) ? seconds : 0;
  return {
    bob: Math.sin(t * 2.1) * 1,
    sway: Math.sin(t * 1.3) * 1,
    flicker: 0.72 + Math.sin(t * 5.5) * 0.28,
    blink: t % 3.4 < 0.12,
    step: Math.floor(t * 6),
  };
}
