/** Plain-language names for rule and configuration keys returned by /api/rules and /api/config. */
import type { Lang } from "./i18n";
import { fmtNumber } from "./format";

type Kind = "fraction" | "usdt" | "count" | "hours" | "days" | "seconds" | "bps" | "score" | "bool" | "list" | "text";
const L: Record<string, [string, string, Kind]> = {
  sleeve_usdt: ["WARP budget cap (USDT, 0 = no cap)", "Batas anggaran WARP (USDT, 0 = tanpa batas)", "usdt"],
  sleeve_pct: ["Share of account WARP may use", "Porsi akun yang boleh dipakai WARP", "fraction"],
  allocation_ceiling: ["Most of the budget in open deals", "Batas anggaran di deal terbuka", "fraction"],
  reserve_floor: ["Cash kept in reserve", "Kas cadangan minimum", "fraction"],
  per_deal_ceiling: ["Largest single deal", "Deal tunggal terbesar", "fraction"],
  max_deals: ["Open deals at once", "Deal terbuka bersamaan", "count"],
  deal_stop_risk: ["Loss allowed per deal", "Rugi maksimal per deal", "fraction"],
  aggregate_stop_risk: ["Loss allowed across all deals", "Rugi maksimal semua deal", "fraction"],
  daily_loss_halt: ["Daily loss that triggers HALT", "Rugi harian pemicu HALT", "fraction"],
  drawdown_halt: ["Drawdown that triggers HALT", "Drawdown pemicu HALT", "fraction"],
  trade_daily_loss: ["Daily loss that triggers HALT", "Rugi harian pemicu HALT", "fraction"],
  trade_drawdown: ["Drawdown that triggers HALT", "Drawdown pemicu HALT", "fraction"],
  stop_slippage: ["Expected slippage on a stop", "Perkiraan slippage saat stop", "fraction"],
  virtual_drop: ["Virtual stop drop", "Penurunan stop virtual", "fraction"],
  stress_shock: ["Stress-test price shock", "Guncangan harga uji stres", "fraction"],
  stress_slippage: ["Stress-test slippage", "Slippage uji stres", "fraction"],
  stress_cap: ["Loss allowed under stress", "Rugi maksimal saat stres", "fraction"],
  corr_min: ["Correlation treated as the same bet", "Korelasi dianggap taruhan sama", "fraction"],
  max_alts: ["Correlated altcoins at once", "Altcoin berkorelasi bersamaan", "count"],
  min_score: ["Minimum setup score", "Skor setup minimum", "score"],
  tp_min: ["Smallest take-profit", "Take-profit terkecil", "fraction"],
  tp_fee_multiple: ["Take-profit as a multiple of fees", "Take-profit sebagai kelipatan biaya", "count"],
  min_quote_volume: ["Minimum 24h volume (USDT)", "Volume 24j minimum (USDT)", "usdt"],
  max_spread: ["Widest bid/ask spread", "Spread bid/ask terlebar", "fraction"],
  max_surge: ["Largest recent price surge", "Lonjakan harga terbesar", "fraction"],
  cooldown_stop_hours: ["Pause after a stop-out", "Jeda setelah kena stop", "hours"],
  cooldown_tp_hours: ["Pause after a take-profit", "Jeda setelah take-profit", "hours"],
  streak_pause_hours: ["Pause after a losing streak", "Jeda setelah rugi beruntun", "hours"],
  stop_streak: ["Stop-outs in a row before pausing", "Stop beruntun sebelum jeda", "count"],
  review_days: ["Review window", "Jendela tinjauan", "days"],
  paper_min_days: ["Paper days before going live", "Hari paper sebelum live", "days"],
  paper_min_deals: ["Paper deals before going live", "Deal paper sebelum live", "count"],
  mode: ["Trading mode", "Mode trading", "text"],
  discovery: ["Market discovery", "Penemuan pasar", "text"],
  symbols: ["Base symbols", "Simbol dasar", "list"],
  discovery_allow: ["Always include", "Selalu sertakan", "list"],
  deny_symbols: ["Never trade", "Jangan pernah trading", "list"],
  max_candidates: ["Symbols screened per pass", "Simbol dipindai per putaran", "count"],
  max_committed: ["Most of equity committed", "Ekuitas maksimal terpakai", "fraction"],
  max_pair: ["Most in one pair", "Maksimal di satu pair", "fraction"],
  max_group: ["Most in one group", "Maksimal di satu grup", "fraction"],
  min_reserve: ["Cash kept in reserve", "Kas cadangan minimum", "fraction"],
  max_positions: ["Open positions at once", "Posisi terbuka bersamaan", "count"],
  fee_bps: ["Fee estimate", "Perkiraan biaya", "bps"],
  slippage_bps: ["Slippage estimate", "Perkiraan slippage", "bps"],
  max_daily_loss: ["Account daily loss limit", "Batas rugi harian akun", "fraction"],
  max_drawdown: ["Account drawdown limit", "Batas drawdown akun", "fraction"],
  scan_seconds: ["Market scan every", "Pindai pasar tiap", "seconds"],
  research_seconds: ["Research pass every", "Riset tiap", "seconds"],
  monitor_seconds: ["Monitoring every", "Pemantauan tiap", "seconds"],
  reconcile_seconds: ["Reconcile with 3Commas every", "Rekonsiliasi 3Commas tiap", "seconds"],
  alerts_configured: ["Alerts webhook set", "Webhook peringatan terpasang", "bool"],
  analysis: ["AI analysis", "Analisis AI", "text"],
  require_backtest: ["Backtest required", "Wajib backtest", "bool"],
  blend_confidence: ["Blend model confidence", "Gabungkan keyakinan model", "bool"],
  password_login: ["Password sign-in enabled", "Login password aktif", "bool"],
};

export function settingLabel(key: string, lang: Lang) {
  const row = L[key];
  return row ? row[lang === "id" ? 1 : 0] : key.replaceAll("_", " ");
}

/** A readable echo of the raw value, e.g. 0.25 → "25%", 300 → "5 min". Returns null when nothing to add. */
export function settingHint(key: string, value: unknown, lang: Lang): string | null {
  const kind = L[key]?.[2];
  const n = typeof value === "number" ? value : typeof value === "string" && value.trim() !== "" ? Number(value) : NaN;
  if (!kind || !Number.isFinite(n)) return null;
  if (kind === "fraction") return `${fmtNumber(n * 100, lang, 2)}%`;
  if (kind === "usdt") return n === 0 && key === "sleeve_usdt" ? (lang === "id" ? "tanpa batas" : "no cap") : `${fmtNumber(n, lang, 2)} USDT`;
  if (kind === "hours") return `${fmtNumber(n, lang, 1)} ${lang === "id" ? "jam" : "h"}`;
  if (kind === "days") return `${fmtNumber(n, lang, 0)} ${lang === "id" ? "hari" : "days"}`;
  if (kind === "seconds") return n >= 60 ? `${fmtNumber(n / 60, lang, 1)} min` : `${n} s`;
  if (kind === "bps") return `${fmtNumber(n / 100, lang, 2)}%`;
  return null;
}

/** Display a config value without inventing one. */
export function settingValue(key: string, value: unknown, lang: Lang, none: string): string {
  if (value == null || value === "") return none;
  if (typeof value === "boolean") return value ? (lang === "id" ? "Ya" : "Yes") : lang === "id" ? "Tidak" : "No";
  if (Array.isArray(value)) return value.length ? value.join(", ") : lang === "id" ? "Tidak ada" : "None";
  const hint = settingHint(key, value, lang);
  return hint ? `${String(value)} · ${hint}` : String(value);
}
