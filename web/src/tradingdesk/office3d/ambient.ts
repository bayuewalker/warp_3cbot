/**
 * Idle offsets for the office render clock. Amplitude only.
 * This module does not read desk state.
 */
export function ambientShift(seconds: number): { bob: number; head: number; flicker: number } {
  const t = Number.isFinite(seconds) ? seconds : 0;
  return {
    bob: Math.sin(t * 1.7) * 0.012,
    head: Math.sin(t * 0.45) * 0.06,
    flicker: 0.86 + Math.sin(t * 3.1) * 0.08,
  };
}
