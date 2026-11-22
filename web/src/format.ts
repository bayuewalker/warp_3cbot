/** Human-readable formatting for values that come from the API. Null never becomes 0. */
import { t, type Lang } from "./i18n";

function toNumber(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function locale(lang: Lang) {
  return lang === "id" ? "id-ID" : "en-US";
}

/** Plain number with thousands separators. */
export function fmtNumber(value: string | number | null | undefined, lang: Lang, digits = 2) {
  const n = toNumber(value);
  if (n == null) return null;
  return new Intl.NumberFormat(locale(lang), { maximumFractionDigits: digits }).format(n);
}

/** Money amount: two decimals, thousands separators. */
export function fmtMoney(value: string | number | null | undefined, lang: Lang) {
  const n = toNumber(value);
  if (n == null) return null;
  return new Intl.NumberFormat(locale(lang), { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
}

/** Price with decimals that suit its size (BTC 2dp, small caps up to 6 significant digits). */
export function fmtPrice(value: string | number | null | undefined, lang: Lang) {
  const n = toNumber(value);
  if (n == null) return null;
  const abs = Math.abs(n);
  const opts: Intl.NumberFormatOptions =
    abs >= 1000
      ? { minimumFractionDigits: 2, maximumFractionDigits: 2 }
      : abs >= 1
        ? { minimumFractionDigits: 2, maximumFractionDigits: 4 }
        : { maximumSignificantDigits: 6 };
  return new Intl.NumberFormat(locale(lang), opts).format(n);
}

/** Coin quantity: up to 8 decimals, trailing zeros dropped. */
export function fmtQty(value: string | number | null | undefined, lang: Lang) {
  const n = toNumber(value);
  if (n == null) return null;
  return new Intl.NumberFormat(locale(lang), {
    maximumFractionDigits: Math.abs(n) >= 1000 ? 2 : Math.abs(n) >= 1 ? 4 : 8,
  }).format(n);
}

/** Signed percentage with two decimals. */
export function fmtPct(value: string | number | null | undefined, lang: Lang) {
  const n = toNumber(value);
  if (n == null) return null;
  return (
    new Intl.NumberFormat(locale(lang), {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
      signDisplay: "exceptZero",
    }).format(n) + "%"
  );
}

/** Large amounts as 2.81B / 271.4M. */
export function fmtCompact(value: string | number | null | undefined, lang: Lang) {
  const n = toNumber(value);
  if (n == null) return null;
  return new Intl.NumberFormat(locale(lang), { notation: "compact", maximumFractionDigits: 2 }).format(n);
}

/** One time format for the whole desk: browser-local time with its zone label. */
export function fmtTime(value: string | number | Date | null | undefined, lang: Lang, withDate = false) {
  if (value == null || value === "") return null;
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat(locale(lang), {
    ...(withDate ? { day: "2-digit", month: "short" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).format(d);
}

/** Zone label of the browser, for headings such as "Today (GMT+7)". */
export function zoneLabel(lang: Lang) {
  const part = new Intl.DateTimeFormat(locale(lang), { timeZoneName: "short" })
    .formatToParts(new Date())
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? "";
}

const SOURCES: Record<string, string> = {
  tradingview: "TradingView",
  binance_spot_public: "Binance Spot",
  binance: "Binance",
};

/** Data source ids from the API shown as product names. */
export function sourceLabel(source: string | null | undefined) {
  if (!source) return null;
  return SOURCES[source.toLowerCase()] ?? source.replaceAll("_", " ");
}

/**
 * TradingView technical rating (-1..1) in its own words.
 * Bands follow TradingView: strong sell < -0.5 ≤ sell < -0.1 ≤ neutral ≤ 0.1 < buy ≤ 0.5 < strong buy.
 */
export function ratingLabel(value: string | number | null | undefined, lang: Lang) {
  const n = toNumber(value);
  if (n == null) return null;
  const key =
    n > 0.5 ? "strongBuy" : n > 0.1 ? "ratingBuy" : n >= -0.1 ? "neutral" : n >= -0.5 ? "ratingSell" : "strongSell";
  return { text: t(lang, key), tone: n > 0.1 ? "good" : n < -0.1 ? "bad" : "neutral", value: n } as const;
}
