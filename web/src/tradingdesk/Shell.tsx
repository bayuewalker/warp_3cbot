import { Activity, Bell, Bot, ChevronRight, Clock3, Crosshair, LayoutDashboard, ListFilter, LogOut, Monitor, Radio, ShieldCheck, ShieldOff, SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Status } from "../api";
import { t, type Lang } from "../i18n";
import { fmtTime } from "../format";
import { useLiveState } from "../snapshot";
import { Badge } from "../ui";
import { sourceInstant } from "./frames";

const nav = [
  { id: "overview", short: "overview", icon: LayoutDashboard },
  { id: "desk", short: "miniDesk", icon: Monitor },
  { id: "signals", short: "minisignals", icon: Crosshair },
  { id: "assets", short: "minibots", icon: Bot },
  { id: "screener", short: "screener", icon: ListFilter },
  { id: "preferences", short: "minisettings", icon: SlidersHorizontal },
] as const;
export type Page = (typeof nav)[number]["id"];
export function Brand() {
  return (
    <div className="brand">
      <span className="mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" width="16" height="16" fill="none" strokeLinejoin="round">
          <path d="M6 8 L16 24 L26 8" stroke="#00a8ff" strokeWidth="3" />
          <path d="M11 14 L16 22 L21 14" stroke="#00d4aa" strokeWidth="2" />
        </svg>
      </span>
      <strong>
        WARP <b>3C</b>
      </strong>
    </div>
  );
}
export function pageFromHash(): Page {
  return nav.find((item) => item.id === location.hash.slice(1))?.id ?? "overview";
}

export function TradingDeskNav({ lang, page, placement }: { lang: Lang; page: Page; placement: "rail" | "bottom" }) {
  const mobile = placement === "bottom";
  const menu = (
    <nav aria-label={t(lang, "navigation")} className={mobile ? "bottom-nav" : undefined}>
      {nav.map(({ id, short, icon: Icon }) => (
        <a key={id} href={`#${id}`} aria-current={page === id ? "page" : undefined} aria-label={t(lang, id)}>
          <Icon size={18} aria-hidden="true" /><span>{t(lang, mobile ? short : id)}</span>
        </a>
      ))}
    </nav>
  );
  if (mobile) return menu;
  return <aside className="rail"><Brand /><div className="rail-label">{t(lang, "desk")}</div>{menu}</aside>;
}

function StatusStrip({ lang, row, halted }: { lang: Lang; row: Status | null; halted: boolean }) {
  const connected = useLiveState();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const published = sourceInstant(row?.published_at, now);
  const age = published === null ? null : Math.max(0, now - published);
  const stale = age !== null && age > 900_000;
  const current = age !== null && !stale;
  const unknown = t(lang, "unknown");
  let permission = unknown;
  if (halted || row?.gating === "halt" || row?.gating === "awaiting_worker" || row?.mode === "observe" || row?.execution_permitted === false) {
    permission = t(lang, "blocked");
  } else if (current && row?.halt === false && row.execution_permitted === true && (row.mode === "paper" || row.mode === "live")) {
    permission = t(lang, "gatePermitted");
  }
  const ageLabel = age === null ? unknown : new Intl.NumberFormat(lang, {
    style: "unit", unit: age < 60_000 ? "second" : "minute", unitDisplay: "narrow",
  }).format(Math.floor(age / (age < 60_000 ? 1000 : 60_000)));
  const stream = t(lang, connected ? "streamOn" : "reconnecting");
  return (
    <dl className="status-strip">
      <div><dt><ShieldOff />HALT</dt><dd data-tone={halted ? "bad" : undefined}>
        {halted ? t(lang, "on") : current && row?.halt === false ? t(lang, "off") : unknown}
      </dd></div>
      <div><dt><ShieldCheck />{t(lang, "execution")}</dt><dd>{permission}</dd></div>
      <div><dt><Clock3 />{t(lang, "workerAge")}</dt><dd data-tone={stale ? "warn" : undefined}>
        {age === null ? unknown : <time dateTime={row?.published_at ?? undefined} title={fmtTime(published, lang, true) ?? undefined}>
          {ageLabel}{stale && " · " + t(lang, "readStale")}
        </time>}
      </dd></div>
      <div><dt><Radio />{t(lang, "streamLabel")}</dt><dd className="live-badge" title={stream} data-tone={connected ? "good" : "warn"}>{stream}</dd></div>
    </dl>
  );
}

export function CommandBar({ lang, page, row, halted, error, language, onLogout }: {
  lang: Lang; page: Page; row: Status | null; halted: boolean; error: boolean; language: ReactNode; onLogout: () => void;
}) {
  const mode = row?.mode;
  return (
    <header className="header-group">
      {halted && <div className="halt-strip" role="alert"><strong>HALT</strong> {t(lang, "haltHelp")}</div>}
      <div className="topbar">
        <div className="command-identity">
          <div className="breadcrumb"><Activity size={16} /><span>{t(lang, "desk")}</span><ChevronRight size={14} /><strong>{t(lang, page)}</strong></div>
          <div className="mobile-brand"><Brand /></div>
          <Badge tone={mode === "observe" ? "info" : mode === "paper" ? "warn" : mode === "live" ? "bad" : "neutral"}>
            <span className="sr-only">{t(lang, "mode")} · </span>{mode === "observe" || mode === "paper" || mode === "live" ? t(lang, mode) : t(lang, "unknown")}
          </Badge>
        </div>
        <div className="toolbar">
          <Alerts lang={lang} row={row} halted={halted} error={error} />
          {language}
          <button className="icon-button" aria-label={t(lang, "logout")} title={t(lang, "logout")} onClick={onLogout}><LogOut size={17} /></button>
        </div>
      </div>
      <StatusStrip lang={lang} row={row} halted={halted} />
    </header>
  );
}

function Alerts({ lang, row, halted, error }: { lang: Lang; row: Status | null; halted: boolean; error: boolean }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    function dismiss(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const messages: string[] = [];
  if (halted) messages.push("HALT · " + t(lang, "haltHelp"));
  if (error) messages.push(t(lang, "noSnapshot"));
  if (row?.market_data_fresh === false) messages.push(t(lang, "stale"));
  if (!halted && row?.risk_permitted === false) messages.push(t(lang, "gateBlocked"));
  return (
    <div className="alerts-tool" ref={root} onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
    }}>
      <button ref={trigger} type="button" className="tool-button" aria-controls="desk-alerts"
        aria-expanded={open} aria-label={t(lang, "statusAlerts") + (messages.length ? " (" + messages.length + ")" : "")}
        onClick={() => setOpen(!open)}>
        <span><Bell size={16} />{messages.length > 0 && <i className="alert-dot" />}</span>
      </button>
      <section id="desk-alerts" className="alerts-list" hidden={!open} aria-label={t(lang, "statusAlerts")}>
        <h2>{t(lang, "statusAlerts")}</h2>
        {messages.length ? <ul>{messages.map(message => <li key={message}>{message}</li>)}</ul>
          : <p>{t(lang, row ? "noDeskAlerts" : "loading")}</p>}
      </section>
    </div>
  );
}
