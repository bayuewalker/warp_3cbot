import type { Lang } from "../i18n";
import type { ObservedValue } from "./types";

type XauStatus = {
  lanes?: { primary?: string };
  published_at?: string | null;
  xau?: {
    state?: string;
    ready?: boolean;
    ready_reason?: string | null;
    feed?: string | null;
    basis?: string | null;
    basis_samples?: number | null;
    spot_last?: string | null;
    spot_at?: string | null;
    spot_state?: string | null;
    cycle_seconds?: number;
    window?: { name?: string; start_wib?: string; end_wib?: string };
    engine?: string | null;
    next_window?: { name?: string; start_wib?: string } | null;
    open_signal?: {
      direction?: string; entry?: string; sl?: string; tp1?: string; tp2?: string | null;
      grade?: string; valid_until?: string | null;
    } | null;
    today?: { signals_sent?: number; journal_only?: number; realized_r?: string };
    funnel?: {
      today?: Record<string, number>;
      recent?: { reason?: string; count?: number; wib?: string }[];
    };
    last_signal_at?: string | null;
    heartbeat_at?: string | null;
    warming?: boolean;
    basis_value?: string | null;
    basis_healthy?: boolean;
    last_bar_age_s?: number | null;
    halt?: { global?: boolean; xau?: boolean };
    recent?: Record<string, unknown>[];
    playbook?: Record<string, unknown> | null;
  } | null;
  mode?: string;
} | null | undefined;

export type StationName = "SCAN" | "ANALYST" | "AEGIS" | "EXECUTOR" | "PORTFOLIO" | "WATCHER";

const text = (value: unknown) => value == null || value === "" ? "unknown" : String(value);

const REASONS: Record<string, [string, string]> = {
  warming_up: ["Warming up", "Pemanasan"],
  warming: ["Warming up", "Pemanasan"],
  market_closed: ["Market closed", "Pasar tutup"],
  dirty_basis: ["Basis not ready", "Basis belum siap"],
  dirty_gaps: ["Candle gaps", "Celah candle"],
  stale_m5: ["Stale 5-minute candles", "Candle 5 menit basi"],
  news_block: ["News block", "Blokir berita"],
  halt: ["Halted", "Dihentikan"],
  no_direction: ["No direction", "Tanpa arah"],
  engine_none: ["No engine", "Tanpa mesin"],
  entry_closed: ["Entry window closed", "Jendela entri tutup"],
  no_setup: ["No setup", "Tanpa setup"],
  feed_unavailable: ["Feed unavailable", "Feed tidak tersedia"],
  stalled: ["Feed stalled", "Feed macet"],
  config_error: ["Configuration error", "Kesalahan konfigurasi"],
  few_samples: ["Warming up", "Pemanasan"],
  basis_wide: ["Basis too wide", "Basis terlalu lebar"],
  basis_noisy: ["Basis too noisy", "Basis terlalu berisik"],
  ok: ["Ready", "Siap"],
};

export function xauLabels(lang: Lang) {
  const id = lang === "id";
  return {
    desk: "XAUUSD",
    source: "OANDA:XAUUSD via TradingView (spot)",
    unavailable: id ? "XAUUSD tidak tersedia" : "XAUUSD unavailable",
    next: id ? "Jendela berikut" : "Next window",
    today: id ? "Hari ini" : "Today",
    funnel: id ? "Tanpa sinyal" : "No signal",
    closed: id ? "Pasar tutup" : "Market closed",
    last: id ? "terakhir" : "last",
    sent: id ? "Terkirim" : "Sent",
    journal: id ? "Jurnal" : "Journal",
    none: id ? "Tidak ada sinyal terbuka" : "No open signal",
    basis: "Basis",
  };
}

export function xauReason(lang: Lang, code: string) {
  const row = REASONS[code];
  if (row) return row[lang === "id" ? 1 : 0];
  if (code.startsWith("gate_fail:")) return (lang === "id" ? "Gerbang " : "Gate ") + code.slice("gate_fail:".length);
  if (code.startsWith("risk_block:")) return (lang === "id" ? "Risiko " : "Risk ") + code.slice("risk_block:".length);
  return code.replaceAll("_", " ");
}

export function xauCard(status: XauStatus) {
  const xau = status?.xau;
  if (!xau || typeof xau !== "object") {
    return {
      state: "unknown", reason: "missing", ready: false, window: "unknown", engine: "unknown",
      next: "unknown", spot: "unknown", spotAt: null as string | null, spotState: "unavailable",
      basis: "unknown", basisSamples: null as number | null, signal: "unknown", open: false,
      sent: "unknown", journal: "unknown", r: "unknown",
      funnel: [] as { reason: string; count: number }[],
      updated: null as string | null,
    };
  }
  const open = xau.open_signal;
  const samples = typeof xau.basis_samples === "number" && Number.isFinite(xau.basis_samples) ? xau.basis_samples : null;
  return {
    state: text(xau.state),
    reason: text(xau.ready_reason),
    ready: xau.ready === true,
    window: text(xau.window?.name),
    engine: text(xau.engine),
    next: xau.next_window?.name ? `${xau.next_window.name} ${text(shortClock(xau.next_window.start_wib))}` : "unknown",
    spot: text(xau.spot_last),
    spotAt: xau.spot_at ?? null,
    spotState: xau.spot_state ?? (xau.spot_last ? "live" : "unavailable"),
    basis: text(xau.basis),
    basisSamples: samples,
    signal: open ? [open.direction, open.entry, open.sl, open.tp1, open.grade].map(text).join(" ") : "unknown",
    open: Boolean(open),
    sent: text(xau.today?.signals_sent),
    journal: text(xau.today?.journal_only),
    r: text(xau.today?.realized_r),
    funnel: (xau.funnel?.recent ?? []).flatMap(row => {
      const reason = typeof row.reason === "string" ? row.reason : "";
      if (!reason) return [];
      const count = typeof row.count === "number" && row.count > 0 ? row.count : 1;
      return [{ reason, count }];
    }).slice(0, 5),
    updated: xau.spot_at ?? xau.heartbeat_at ?? status?.published_at ?? null,
  };
}

function seat(value: string, at: string | null, fresh: boolean): ObservedValue<string | null> {
  const missing = value === "unknown";
  return {
    value: missing ? null : value,
    availability: missing ? "unavailable" : "confirmed",
    freshness: missing ? "unknown" : fresh ? "fresh" : "stale",
    state: missing ? "unavailable" : "confirmed",
    source: "worker",
    sourceAt: at,
    receivedAt: at,
    reason: missing ? "unknown" : null,
    traceId: null,
  };
}

export function xauAgents(status: XauStatus, now: number): Record<StationName, ObservedValue<string | null>> {
  const card = xauCard(status);
  const at = card.updated;
  const time = at ? Date.parse(at) : NaN;
  const cycle = status?.xau?.cycle_seconds;
  const limit = (typeof cycle === "number" && cycle > 0 ? cycle : 30) * 2 * 1000;
  const fresh = card.spotState !== "market_closed" && Number.isFinite(time) && Math.max(0, now - time) <= limit;
  const book = card.sent === "unknown" ? "unknown" : `${card.sent} ${card.journal} ${card.r}`;
  return {
    SCAN: seat(card.spot, at, fresh),
    ANALYST: seat(card.engine + " " + card.window, at, fresh),
    AEGIS: seat(card.ready ? "pass" : card.reason, at, fresh),
    EXECUTOR: seat(card.open ? card.signal : "unknown", at, fresh),
    PORTFOLIO: seat(book, at, fresh),
    WATCHER: seat(text(status?.xau?.last_signal_at ?? status?.xau?.state), at, fresh),
  };
}

export function xauTape(status: XauStatus, now: number) {
  const xau = status?.xau;
  const last = xau && typeof xau.spot_last === "string" && Number(xau.spot_last) > 0 ? xau.spot_last : null;
  const at = xau?.spot_at ?? null;
  const time = at ? Date.parse(at) : NaN;
  const cycle = xau?.cycle_seconds;
  const limit = (typeof cycle === "number" && cycle > 0 ? cycle : 30) * 2 * 1000;
  const closed = xau?.spot_state === "market_closed";
  const recent = last !== null && Number.isFinite(time) && Math.max(0, now - time) <= limit;
  const fresh = !closed && recent;
  const quoteState = !last ? "unavailable" : closed ? "market_closed" : fresh ? "fresh" : "stale";
  return {
    symbol: "XAUUSD",
    last,
    at,
    fresh,
    state: quoteState,
    reason: xau?.ready_reason || xau?.state || "missing",
    source: "OANDA:XAUUSD via TradingView (spot)",
    active: status?.lanes?.primary === "xau" || last !== null,
  };
}

const pair = (en: string, id: string): [string, string] => [en, id];

export const EVENT_STATION: Record<string, StationName> = {
  market_data_blocked: "SCAN",
  research_blocked: "ANALYST",
  llm_provider_unhealthy: "ANALYST",
  halt: "AEGIS",
  entries_paused: "AEGIS",
  observe: "EXECUTOR",
  observe_candidate: "EXECUTOR",
  duplicate_skipped: "EXECUTOR",
  strategy_confirmed: "EXECUTOR",
  alert_failed: "WATCHER",
  cycle_blocked: "WATCHER",
  xau_cycle_failed: "WATCHER",
  alert_skipped: "WATCHER",
  bad_json: "WATCHER",
};

const EVENT_LABEL: Record<string, [string, string]> = {
  market_data_blocked: pair("Market data blocked", "Data pasar terblokir"),
  research_blocked: pair("Research blocked", "Riset terblokir"),
  llm_provider_unhealthy: pair("Provider unhealthy", "Penyedia tidak sehat"),
  halt: pair("Halted", "Dihentikan"),
  entries_paused: pair("Entries paused", "Entri dijeda"),
  observe: pair("Observing", "Mengamati"),
  observe_candidate: pair("Candidate observed", "Kandidat teramati"),
  duplicate_skipped: pair("Duplicate skipped", "Duplikat dilewati"),
  strategy_confirmed: pair("Strategy confirmed", "Strategi terkonfirmasi"),
  alert_failed: pair("Alert failed", "Peringatan gagal"),
  cycle_blocked: pair("Cycle blocked", "Siklus terblokir"),
  xau_cycle_failed: pair("XAU cycle failed", "Siklus XAU gagal"),
  alert_skipped: pair("Alert skipped", "Peringatan dilewati"),
  bad_json: pair("Unreadable message", "Pesan tidak terbaca"),
};

export const MAIN_PREFERENCE_TABS = ["venues", "rules", "configuration", "activity", "risk"] as const;

export function preferenceTabs(postponed: boolean) {
  return postponed ? [] : [...MAIN_PREFERENCE_TABS];
}

export function deskCopy(lang: Lang) {
  const id = lang === "id";
  return {
    waiting: id ? "Menunggu setup" : "Waiting for a setup",
    open: id ? "Sinyal terbuka" : "Signal open",
    closed: id ? "Pasar tutup" : "Market closed",
    warming: id ? "Pemanasan" : "Warming up",
    halted: id ? "Dihentikan" : "Halted",
    feed: id ? "Feed tidak tersedia" : "Feed unavailable",
    ready: id ? "Siap" : "Ready",
    unavailable: id ? "Tidak tersedia" : "Unavailable",
    notReported: id ? "Belum dilaporkan" : "Not reported",
    live: id ? "Live" : "Live",
    stale: id ? "Basi" : "Stale",
    healthy: id ? "Sehat" : "Healthy",
    unhealthy: id ? "Tidak sehat" : "Unhealthy",
    window: id ? "Jendela" : "Window",
    next: id ? "Jendela berikut" : "Next window",
    engine: id ? "Mesin" : "Engine",
    basis: "Basis",
    direction: id ? "Arah" : "Direction",
    entry: id ? "Entri" : "Entry",
    sl: "SL",
    tp1: "TP1",
    tp2: "TP2",
    grade: id ? "Nilai" : "Grade",
    valid: id ? "Berlaku sampai" : "Valid until",
    noSignal: id ? "Belum ada sinyal di jendela ini" : "No signal yet in this window",
    why: id ? "Mengapa belum ada sinyal" : "Why no signal",
    cardHelp: id
      ? "Setiap bar yang ditutup, mesin aktif memeriksa setup; AEGIS menjalankan setiap aturan sebelum sinyal dikirim."
      : "Each closed bar, the active engine checks the setup; AEGIS runs every rule before a signal is sent.",
    allChecks: id ? "Semua pemeriksaan lolos" : "All checks passing",
    funnelToday: id ? "Corong hari ini" : "Funnel today",
    feedAge: id ? "Usia feed" : "Feed age",
    samples: id ? "Sampel basis" : "Basis samples",
    rToday: id ? "R hari ini" : "R today",
    alerts: id ? "Peringatan" : "Alerts",
    sent: id ? "Terkirim" : "Sent",
    journal: id ? "Jurnal" : "Journal",
    noOpen: id ? "Tidak ada sinyal terbuka" : "No open signal",
    noYet: id ? "Belum ada sinyal hari ini" : "No signal yet today",
    lastSignal: id ? "Sinyal terakhir" : "Last signal",
    idle: id ? "Diam" : "Idle",
    working: id ? "Bekerja" : "Working",
    alert: id ? "Waspada" : "Alert",
    off: id ? "Mati" : "Off",
    roles: {
      SCAN: id ? "memantau feed emas" : "watches the gold feed",
      ANALYST: id ? "memilih setup untuk jendela ini" : "picks the setup for this window",
      AEGIS: id ? "memeriksa setiap aturan" : "checks every rule",
      EXECUTOR: id ? "menyusun sinyal, tidak pernah mengirim order" : "drafts the signal, never orders",
      PORTFOLIO: id ? "menyimpan skor hari ini" : "keeps today's score",
      WATCHER: id ? "mencatat dan memberi peringatan" : "logs and alerts",
    } as Record<StationName, string>,
    dash: "—",
    changeMissing: id ? "Belum dilaporkan" : "Not reported",
    source: "OANDA:XAUUSD via TradingView (spot)",
    stripSignals: id ? "sinyal hari ini" : "signals today",
    wlMissing: id ? "W/L belum dilaporkan" : "W/L not reported",
    history: id ? "Riwayat sinyal" : "Signal history",
    historyHelp: id ? "Sinyal terbuka dan yang baru dikirim." : "The open signal and the ones just sent.",
    noSignalsYet: id ? "Belum ada sinyal terkirim" : "No signals sent yet",
    result: id ? "Hasil (R)" : "Result (R)",
    time: id ? "Waktu" : "Time",
    log: id ? "Catatan meja" : "Desk log",
    logHelp: id ? "Peristiwa pekerja dan peringatan, terbaru di atas." : "Worker events and alerts, newest first.",
    all: id ? "Semua" : "All",
    emptyFilter: id ? "Tidak ada baris untuk filter ini" : "No rows for this filter",
    openActivity: id ? "Buka Aktivitas" : "Open Activity",
    playbook: id ? "Playbook XAU" : "XAU playbook",
    playbookMissing: id ? "Playbook belum dilaporkan" : "Playbook not reported",
    playbookCaption: id
      ? "Ini aturan yang dijalankan meja. Ubah di repositori, bukan di sini."
      : "These are the rules the desk runs. Change them in the repo, not here.",
    rulesVersion: id ? "Versi aturan" : "Rules version",
    engines: id ? "Mesin" : "Engines",
    windows: id ? "Jendela (WIB)" : "Windows (WIB)",
    cycle: id ? "Siklus" : "Cycle",
    basisMax: id ? "Batas basis" : "Basis max",
    spread: id ? "Spread yang diasumsikan" : "Spread assumed",
    profile: id ? "Profil" : "Profile",
    profileType: id ? "Jenis profil" : "Profile type",
    maxRisk: id ? "Risiko maksimum per trade" : "Max risk per trade",
    dailyDd: id ? "Drawdown harian" : "Daily drawdown",
    maxDd: id ? "Drawdown maksimum" : "Max drawdown",
    news: id ? "Aturan berita" : "News rule",
    newsWindow: id ? "Jendela berita" : "News window",
    checks: id ? "Pemeriksaan gerbang" : "Gate checks",
    example: "example",
    openDesk: id ? "Buka Trading Desk" : "Open Trading Desk",
    mode: id ? "Mode" : "Mode",
    password: id ? "Masuk memakai kata sandi dasbor." : "Sign-in uses the dashboard password.",
    playbookLink: id ? "Buka playbook di meja" : "Open the playbook on the desk",
    risk: id ? "Kesehatan XAU" : "XAU health",
    riskHelp: id ? "Status feed, basis, dan penghentian XAU." : "XAU feed, basis, and halt.",
    feedState: id ? "Status feed" : "Feed state",
    haltGlobal: id ? "Penghentian global" : "Global halt",
    haltXau: id ? "Penghentian XAU" : "XAU halt",
    yes: id ? "Ya" : "Yes",
    no: id ? "Tidak" : "No",
    settingsHelp: id
      ? "Lane spot ditunda. Yang tampil hanya mode, peringatan, dan playbook XAU."
      : "The spot lane is postponed. This page shows mode, alerts, and the XAU playbook.",
    minutes: id ? "menit" : "min",
    until: id ? "sampai" : "until",
    quoted: id ? "Emas dikutip" : "Gold quoted",
    logged: id ? "Peristiwa tercatat" : "Logged event",
  };
}

function lane(status: XauStatus) {
  return status?.xau && typeof status.xau === "object" ? status.xau : null;
}

export function isWarming(status: XauStatus) {
  const xau = lane(status);
  return xau?.warming === true || xau?.state === "warming" || xau?.ready_reason === "warming";
}

export function basisRead(lang: Lang, status: XauStatus): { text: string; bar: number | null } {
  const copy = deskCopy(lang);
  const xau = lane(status);
  const samples = typeof xau?.basis_samples === "number" && Number.isFinite(xau.basis_samples) ? xau.basis_samples : null;
  if (isWarming(status)) {
    if (samples == null) return { text: copy.warming, bar: null };
    return { text: `${copy.warming} (${samples}/10)`, bar: Math.min(100, (samples / 10) * 100) };
  }
  const raw = xau?.basis_value ?? xau?.basis;
  const value = typeof raw === "string" && raw !== "" && Number.isFinite(Number(raw)) ? raw : null;
  if (!value) return { text: copy.unavailable, bar: null };
  return { text: `${value} · ${xau?.basis_healthy === true ? copy.healthy : copy.unhealthy}`, bar: null };
}

export type AnalysisChip = "halted" | "closed" | "warming" | "feed" | "open" | "waiting";

export function analysisChip(status: XauStatus): AnalysisChip {
  const xau = lane(status);
  const halt = xau?.halt;
  if ((halt && (halt.global === true || halt.xau === true)) || xau?.state === "halted" || xau?.ready_reason === "halt") return "halted";
  if (xau?.spot_state === "market_closed" || xau?.state === "market_closed" || xau?.ready_reason === "market_closed") return "closed";
  if (isWarming(status)) return "warming";
  const reason = xau?.ready_reason ?? "";
  if (xau?.state === "feed_unavailable" || xau?.state === "stalled" || xau?.state === "config_error" ||
    reason === "feed_unavailable" || reason === "stalled" || reason === "config_error" || xau?.spot_state === "unavailable") {
    return "feed";
  }
  if (xau?.open_signal && typeof xau.open_signal === "object") return "open";
  return "waiting";
}

export function chipLabel(lang: Lang, chip: AnalysisChip) {
  return deskCopy(lang)[chip];
}

export function tickerChip(lang: Lang, status: XauStatus, now: number) {
  const tape = xauTape(status, now);
  const copy = deskCopy(lang);
  if (tape.state === "market_closed") return { id: "closed" as const, label: copy.closed };
  if (tape.state === "stale") return { id: "stale" as const, label: copy.stale };
  if (tape.state === "fresh") return { id: "live" as const, label: copy.live };
  return { id: "unavailable" as const, label: copy.unavailable };
}

function shown(value: unknown, missing: string) {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string" && value.trim() !== "") return value.trim();
  return missing;
}

function wibMinutes(now: number) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(now));
  const hour = Number(parts.find(part => part.type === "hour")?.value);
  const minute = Number(parts.find(part => part.type === "minute")?.value);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  return hour * 60 + minute;
}

function clockMinutes(value: string | undefined) {
  const match = /(\d{2}):(\d{2})$/.exec(value ?? "");
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function shortClock(value: string | undefined) {
  const match = /(\d{2}:\d{2})$/.exec(value ?? "");
  return match ? match[1] : value;
}

export function windowProgress(status: XauStatus, now: number) {
  const xau = lane(status);
  const start = clockMinutes(xau?.window?.start_wib);
  const end = clockMinutes(xau?.window?.end_wib);
  const cursor = wibMinutes(now);
  if (start == null || end == null || cursor == null || start === end) return null;
  const span = (end - start + 1440) % 1440;
  const elapsed = (cursor - start + 1440) % 1440;
  if (elapsed > span) return null;
  return Math.min(100, (elapsed / span) * 100);
}

function feedAge(status: XauStatus, now: number) {
  const xau = lane(status);
  if (typeof xau?.last_bar_age_s === "number" && Number.isFinite(xau.last_bar_age_s)) return `${Math.max(0, Math.round(xau.last_bar_age_s))}s`;
  const time = xau?.spot_at ? Date.parse(xau.spot_at) : NaN;
  if (!Number.isFinite(time)) return null;
  return `${Math.max(0, Math.round((now - time) / 1000))}s`;
}

function funnelCount(status: XauStatus) {
  const today = lane(status)?.funnel?.today;
  if (!today || typeof today !== "object") return null;
  let sum = 0;
  let seen = false;
  for (const value of Object.values(today)) {
    if (typeof value === "number" && Number.isFinite(value)) {
      sum += value;
      seen = true;
    }
  }
  return seen ? sum : null;
}

export type MoodName = "working" | "idle" | "alert" | "sleep";

export function agentCards(
  lang: Lang,
  status: XauStatus,
  now: number,
  moods: Partial<Record<StationName, MoodName>>,
  alerts: number | null,
) {
  const copy = deskCopy(lang);
  const xau = lane(status);
  const missing = copy.notReported;
  const card = xauCard(status);
  const warming = isWarming(status);
  const samples = card.basisSamples;
  const age = feedAge(status, now);
  const closed = analysisChip(status) === "closed";
  const scanStatus = closed ? copy.closed : card.spot === "unknown" ? copy.feed : copy.quoted;
  const scanMetric = warming && samples != null
    ? { label: copy.samples, value: `${samples}/10`, bar: Math.min(100, (samples / 10) * 100) }
    : { label: copy.feedAge, value: age ?? missing, bar: null as number | null };
  const windowName = xau?.window?.name || missing;
  const until = shortClock(xau?.window?.end_wib);
  const nextName = xau?.next_window?.name;
  const nextAt = shortClock(xau?.next_window?.start_wib);
  const analyst = `${windowName}${until ? ` ${copy.until} ${until}` : ""}`;
  const next = nextName ? `${copy.next} ${nextName}${nextAt ? ` ${nextAt}` : ""}` : `${copy.next} ${missing}`;
  const open = xau?.open_signal;
  const executor = open
    ? [open.direction, open.entry, open.sl, open.tp1].map(item => shown(item, missing)).join(" · ")
    : copy.noOpen;
  const sent = xau?.today?.signals_sent;
  const journal = xau?.today?.journal_only;
  const realized = xau?.today?.realized_r;
  const book = sent == null && journal == null
    ? missing
    : `${copy.sent} ${shown(sent, missing)} · ${copy.journal} ${shown(journal, missing)}`;
  const last = wibStamp(xau?.last_signal_at);
  const funnel = funnelCount(status);
  const progress = windowProgress(status, now);
  const word = (role: StationName) => {
    const mood = moods[role];
    if (mood === "working") return copy.working;
    if (mood === "alert") return copy.alert;
    if (mood === "sleep") return copy.off;
    if (mood === "idle") return copy.idle;
    return missing;
  };
  const rows: {
    role: StationName; subtitle: string; state: string; status: string; metric: string; value: string; bar: number | null;
  }[] = [
    { role: "SCAN", subtitle: copy.roles.SCAN, state: word("SCAN"), status: scanStatus, metric: scanMetric.label, value: scanMetric.value, bar: scanMetric.bar },
    { role: "ANALYST", subtitle: copy.roles.ANALYST, state: word("ANALYST"), status: `${analyst} · ${next}`, metric: copy.engine, value: card.engine === "unknown" ? missing : card.engine, bar: progress },
    {
      role: "AEGIS", subtitle: copy.roles.AEGIS, state: word("AEGIS"),
      status: card.ready ? copy.allChecks : (card.reason === "unknown" ? missing : xauReason(lang, card.reason)),
      metric: copy.funnelToday, value: funnel == null ? missing : String(funnel), bar: null,
    },
    {
      role: "EXECUTOR", subtitle: copy.roles.EXECUTOR, state: word("EXECUTOR"), status: executor,
      metric: copy.grade, value: open?.grade ? shown(open.grade, missing) : copy.dash, bar: null,
    },
    {
      role: "PORTFOLIO", subtitle: copy.roles.PORTFOLIO, state: word("PORTFOLIO"), status: book,
      metric: copy.rToday, value: realized == null || realized === "" ? missing : String(realized), bar: null,
    },
    {
      role: "WATCHER", subtitle: copy.roles.WATCHER, state: word("WATCHER"),
      status: last ? `${copy.lastSignal} ${last}` : copy.noYet,
      metric: copy.alerts, value: alerts == null ? missing : String(alerts), bar: null,
    },
  ];
  return rows;
}

export function wibStamp(iso: string | null | undefined) {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(date) + " WIB";
}

export function whyQuiet(lang: Lang, status: XauStatus) {
  const recent = lane(status)?.funnel?.recent ?? [];
  return recent.flatMap(row => {
    const reason = typeof row.reason === "string" ? row.reason : "";
    if (!reason) return [];
    const count = typeof row.count === "number" && row.count > 0 ? row.count : 1;
    return [{ label: xauReason(lang, reason), count }];
  }).slice(0, 3);
}

export type LogStation = StationName | "all";

export function stationFor(event: string): StationName | null {
  if (event.startsWith("XAU FILLED") || event.startsWith("XAU BUY") || event.startsWith("XAU SELL")) return "EXECUTOR";
  if (event.startsWith("XAU ")) return "WATCHER";
  return EVENT_STATION[event] ?? null;
}

export function eventLabel(lang: Lang, event: string) {
  const row = EVENT_LABEL[event];
  if (row) return row[lang === "id" ? 1 : 0];
  if (event.startsWith("XAU ")) return event;
  return deskCopy(lang).logged;
}

export type LogInput = { event?: unknown; ts?: unknown; created_at?: unknown; detail?: unknown };

export function deskRows(lang: Lang, rows: LogInput[], station: LogStation) {
  const copy = deskCopy(lang);
  const stamped = rows.flatMap(row => {
    const event = typeof row.event === "string" ? row.event : "";
    if (!event) return [];
    const iso = typeof row.ts === "string" ? row.ts : typeof row.created_at === "string" ? row.created_at : null;
    const time = iso ? Date.parse(iso) : NaN;
    const owner = stationFor(event);
    if (station !== "all" && owner !== station) return [];
    return [{
      event,
      station: owner,
      label: eventLabel(lang, event),
      when: wibStamp(iso) ?? copy.notReported,
      time: Number.isFinite(time) ? time : 0,
    }];
  });
  return stamped.sort((a, b) => b.time - a.time).slice(0, 20);
}

export function historyRows(lang: Lang, status: XauStatus) {
  const copy = deskCopy(lang);
  const recent = lane(status)?.recent;
  if (!Array.isArray(recent)) return null;
  return recent.slice(0, 10).map(row => {
    const time = typeof row.created_at === "string" ? row.created_at : typeof row.valid_until === "string" ? null : null;
    const result = typeof row.realized_r === "string" ? row.realized_r : null;
    return {
      time: wibStamp(time) ?? copy.notReported,
      direction: shown(row.direction, copy.notReported),
      entry: shown(row.entry, copy.notReported),
      result: result ?? copy.notReported,
    };
  });
}

export function openSignalFields(lang: Lang, status: XauStatus) {
  const copy = deskCopy(lang);
  const open = lane(status)?.open_signal;
  if (!open || typeof open !== "object") return null;
  return [
    [copy.direction, shown(open.direction, copy.notReported)],
    [copy.entry, shown(open.entry, copy.notReported)],
    [copy.sl, shown(open.sl, copy.notReported)],
    [copy.tp1, shown(open.tp1, copy.notReported)],
    [copy.tp2, shown(open.tp2, copy.notReported)],
    [copy.grade, shown(open.grade, copy.notReported)],
    [copy.valid, wibStamp(open.valid_until) ?? copy.notReported],
  ] as [string, string][];
}

export function stripLine(lang: Lang, status: XauStatus) {
  const copy = deskCopy(lang);
  const sent = lane(status)?.today?.signals_sent;
  const mode = typeof status?.mode === "string" && status.mode ? status.mode.toUpperCase() : copy.notReported;
  const count = typeof sent === "number" && Number.isFinite(sent) ? String(sent) : copy.notReported;
  return `${mode} · ${count} ${copy.stripSignals} · ${copy.wlMissing} · ${chipLabel(lang, analysisChip(status))}`;
}

function bookText(value: unknown, missing: string) {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string" && value.trim() !== "") return value.trim();
  if (Array.isArray(value)) {
    const parts = value.flatMap(item => typeof item === "string" && item ? [item] : []);
    return parts.length ? parts.join(", ") : missing;
  }
  return missing;
}

export function playbookLines(lang: Lang, status: XauStatus) {
  const copy = deskCopy(lang);
  const book = lane(status)?.playbook;
  if (!book || typeof book !== "object") return null;
  const windows = Array.isArray(book.windows)
    ? book.windows.flatMap(row => {
      if (!row || typeof row !== "object") return [];
      const item = row as { name?: unknown; engine?: unknown; start_wib?: unknown; end_wib?: unknown };
      const name = bookText(item.name, "");
      if (!name) return [];
      const engine = bookText(item.engine, copy.notReported);
      const start = bookText(item.start_wib, "");
      const end = bookText(item.end_wib, "");
      return [`${name} ${start}–${end} · ${engine}`];
    }).join("\n")
    : copy.notReported;
  const profileType = bookText(book.profile_type, copy.notReported);
  return [
    [copy.rulesVersion, bookText(book.rules_version, copy.notReported)],
    [copy.engines, bookText(book.engines, copy.notReported)],
    [copy.windows, windows || copy.notReported],
    [copy.cycle, bookText(book.cycle_seconds, copy.notReported)],
    [copy.basisMax, bookText(book.basis_max, copy.notReported)],
    [copy.spread, bookText(book.spread_assumed, copy.notReported)],
    [copy.profile, bookText(book.profile_name, copy.notReported)],
    [copy.profileType, profileType],
    [copy.maxRisk, bookText(book.max_risk_per_trade_pct, copy.notReported)],
    [copy.dailyDd, bookText(book.daily_dd_pct, copy.notReported)],
    [copy.maxDd, bookText(book.max_dd_pct, copy.notReported)],
    [copy.news, bookText(book.news_rule, copy.notReported)],
    [copy.newsWindow, bookText(book.news_window_min, copy.notReported)],
    [copy.checks, bookText(book.gate_checks, copy.notReported)],
  ] as [string, string][];
}

export function quoteChange(change: string | null | undefined) {
  if (change == null || change === "" || !Number.isFinite(Number(change))) return null;
  const value = Number(change);
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}
