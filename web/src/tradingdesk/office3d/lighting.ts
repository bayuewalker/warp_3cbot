/** WIB day/night. Day is fully bright 06:00–18:00. Dawn and dusk are 30-minute blends. */

export type LightPhase = "night" | "dawn" | "day" | "dusk";

export function wibMinutes(now: number): number | null {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(now));
  let hour = Number(parts.find((part) => part.type === "hour")?.value);
  const minute = Number(parts.find((part) => part.type === "minute")?.value);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return null;
  if (hour === 24) hour = 0;
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return hour * 60 + minute;
}

/** `day` is 0 at full night and 1 at full day. */
export function wibLight(now: number): { phase: LightPhase; day: number } {
  const mins = wibMinutes(now);
  if (mins == null) return { phase: "day", day: 1 };
  const dawnStart = 5 * 60 + 30;
  const dayStart = 6 * 60;
  const dayEnd = 18 * 60;
  const duskEnd = 18 * 60 + 30;
  if (mins >= dayStart && mins < dayEnd) return { phase: "day", day: 1 };
  if (mins >= dawnStart && mins < dayStart) return { phase: "dawn", day: (mins - dawnStart) / 30 };
  if (mins >= dayEnd && mins < duskEnd) return { phase: "dusk", day: 1 - (mins - dayEnd) / 30 };
  return { phase: "night", day: 0 };
}
