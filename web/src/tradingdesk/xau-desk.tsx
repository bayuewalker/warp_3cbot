import { Activity, CandlestickChart } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { Status } from "../api";
import { fmtPct, fmtPrice, fmtTime } from "../format";
import { t, type Lang } from "../i18n";
import { useLoad } from "../snapshot";
import { Card, CardHead } from "../ui";
import { requestPreferencesTab } from "./activity-model";
import { roomLife } from "./room-life";
import {
  agentCards, analysisChip, basisRead, chipLabel, deskCopy, deskRows, historyRows, openSignalFields,
  playbookLines, postponedShowsLog, quoteChange, stripLine, tickerChip, whyQuiet, xauCard, xauLabels, xauReason, xauTape,
  type LogInput, type LogStation, type StationName,
} from "./xau-lane";
import "./xau-board.css";

export function XauDesk({
  lang, row, full, now, children,
}: {
  lang: Lang; row: Status; full?: boolean; now?: number; children?: ReactNode;
}) {
  if (full) {
    const page = typeof location !== "undefined" && location.hash === "#desk" ? "desk" : "overview";
    return <XauBoard lang={lang} row={row} now={now ?? Date.now()} page={page}>{children}</XauBoard>;
  }
  const card = xauCard(row);
  const copy = xauLabels(lang);
  const basis = basisRead(lang, row);
  const signal = card.open ? card.signal : copy.none;
  const today = card.sent === "unknown"
    ? t(lang, "unknown")
    : `${copy.sent} ${card.sent} · ${copy.journal} ${card.journal} · R ${card.r}`;
  const funnel = card.funnel.length
    ? card.funnel.map(item => xauReason(lang, item.reason) + (item.count > 1 ? ` × ${item.count}` : "")).join(", ")
    : t(lang, "unknown");
  const closed = card.spotState === "market_closed";
  const spot = card.spot === "unknown" ? t(lang, "unknown") : card.spot;
  return <Card>
    <CardHead icon={CandlestickChart} title={copy.desk} help={copy.source}
      aside={<time>{fmtTime(card.updated, lang, true) ?? t(lang, "unknown")}</time>} />
    <ul className="symbols">
      <li>{chipLabel(lang, analysisChip(row))}</li>
      <li>{t(lang, "officeXauLive")}: {card.window} · {card.engine}</li>
      <li>{copy.next}: {card.next}</li>
      <li>{closed ? copy.closed : "XAUUSD"}: {spot}</li>
      <li>{copy.basis}: {basis.text}</li>
      <li>{signal}</li>
      <li>{copy.today}: {today}</li>
      <li>{copy.funnel}: {funnel}</li>
    </ul>
  </Card>;
}

export function XauQuote({ lang, now, status, symbols, onSelect }: {
  lang: Lang;
  now: number;
  status?: Status | null;
  symbols: string[];
  onSelect: (symbol: string) => void;
}) {
  const tape = xauTape(status, now);
  const copy = xauLabels(lang);
  const fresh = tape.fresh === true;
  const state = tape.state === "market_closed" ? copy.closed : tape.state === "stale" ? t(lang, "readStale") : fresh ? t(lang, "readFresh") : t(lang, "unknown");
  const closed = tape.state === "market_closed";
  return <section className="market-wall" aria-labelledby="market-wall-title">
    <div className="market-wall-head">
      <h3 id="market-wall-title"><Activity size={15} />{t(lang, "priceTape")} <small>{copy.source}</small></h3>
      <span data-fresh={fresh}>{state}</span>
    </div>
    {tape.last ? <div className="market-quote">
      <label><span className="sr-only">{t(lang, "symbols")}</span>
        <select value="XAUUSD" onChange={event => onSelect(event.target.value)}>
          {symbols.map(symbol => <option key={symbol}>{symbol}</option>)}
        </select>
      </label>
      <div><strong>{fmtPrice(tape.last, lang)}</strong>
        <small>{fmtPct(null, lang) ?? t(lang, "unknown")}</small></div>
    </div> : <>
      <label><span className="sr-only">{t(lang, "symbols")}</span>
        <select value="XAUUSD" onChange={event => onSelect(event.target.value)}>
          {symbols.map(symbol => <option key={symbol}>{symbol}</option>)}
        </select>
      </label>
      <p className="market-wait">{copy.unavailable} · {tape.reason ?? t(lang, "unknown")}</p>
    </>}
    <footer>{(closed ? copy.closed + " · " + copy.last : t(lang, "sourceTime"))} · {fmtTime(tape.at, lang, true) ?? t(lang, "unknown")}</footer>
  </section>;
}

const ROLES: StationName[] = ["SCAN", "ANALYST", "AEGIS", "EXECUTOR", "PORTFOLIO", "WATCHER"];

function FieldList({ rows }: { rows: [string, string][] }) {
  return <ul className="xau-fields">{rows.map(([label, value]) => <li key={label}><span>{label}</span><b>{value}</b></li>)}</ul>;
}

function SignalCard({ lang, row }: { lang: Lang; row: Status }) {
  const copy = deskCopy(lang);
  const card = xauCard(row);
  const basis = basisRead(lang, row);
  const chip = analysisChip(row);
  const open = openSignalFields(lang, row);
  const quiet = whyQuiet(lang, row);
  const windowName = card.window === "unknown" ? copy.notReported : card.window;
  const engine = card.engine === "unknown" ? copy.notReported : card.engine;
  return <Card className="xau-card">
    <CardHead icon={CandlestickChart} title="XAUUSD" help={copy.cardHelp}
      aside={<span className="xau-chip" data-chip={chip}>{chipLabel(lang, chip)}</span>} />
    <FieldList rows={[
      [copy.window, windowName],
      [copy.next, card.next === "unknown" ? copy.notReported : card.next],
      [copy.engine, engine],
      [copy.basis, basis.text],
    ]} />
    {basis.bar != null && <div className="xau-bar" aria-hidden="true"><i style={{ width: `${basis.bar}%` }} /></div>}
    {open ? <FieldList rows={open} /> : <p className="xau-status">{copy.noSignal}</p>}
    {quiet.length > 0 && <details>
      <summary>{copy.why}</summary>
      <ul className="xau-fields">{quiet.map(item => <li key={item.label}><span>{item.label}</span><b>{item.count}</b></li>)}</ul>
    </details>}
    <time dateTime={card.updated ?? undefined}>{fmtTime(card.updated, lang, true) ?? copy.notReported}</time>
  </Card>;
}

export function XauBoard({
  lang, row, now, page, children,
}: {
  lang: Lang; row: Status; now: number; page: "desk" | "overview"; children?: ReactNode;
}) {
  const copy = deskCopy(lang);
  const tape = xauTape(row, now);
  const quote = tickerChip(lang, row, now);
  const prices = useLoad<{ symbols?: Record<string, { last?: string; change_pct?: string | null }> }>("/api/prices", true);
  const alerts = useLoad<{ alerts?: LogInput[] }>("/api/alerts", true);
  const loadedEvents = useLoad<{ events?: LogInput[] }>("/api/events", true);
  const life = roomLife({ agents: {}, status: row, events: loadedEvents.data?.events, now, radar: false });
  const moods = Object.fromEntries(ROLES.map(role => [role, life.stations[role].mood])) as Partial<Record<StationName, "working" | "idle" | "alert" | "sleep">>;
  const alertRows = Array.isArray(alerts.data?.alerts) ? alerts.data.alerts : [];
  const cards = agentCards(lang, row, now, moods, alerts.data ? alertRows.length : null);
  const history = historyRows(lang, row);
  const open = openSignalFields(lang, row);
  const book = playbookLines(lang, row);
  const merged = [...(loadedEvents.data?.events ?? []), ...alertRows];
  const symbols = prices.data?.symbols ?? {};
  return <div className="xau-board">
    <section data-section="ticker" aria-label="XAUUSD">
      <div className="xau-ticker">
        <div className="xau-tick">
          <b>XAUUSD</b>
          <strong>{tape.last ?? copy.unavailable}</strong>
          <span className="xau-chip" data-chip={quote.id}>{quote.label}</span>
        </div>
        {Object.entries(symbols).filter(([symbol]) => symbol !== "XAUUSD").map(([symbol, item]) => {
          const change = quoteChange(item?.change_pct);
          return <div className="xau-tick" key={symbol}>
            <b>{symbol}</b>
            <strong>{item?.last || copy.unavailable}</strong>
            {change ? <small>{change}</small> : <small aria-label={copy.changeMissing}>{copy.dash}</small>}
          </div>;
        })}
      </div>
      <p className="xau-source">{copy.source}</p>
    </section>
    <div data-section="signal"><SignalCard lang={lang} row={row} /></div>
    <div data-section="room">{children}</div>
    <p className="xau-strip" data-section="strip">{stripLine(lang, row)}</p>
    <div className="xau-agents" data-section="agents">
      {cards.map(item => <Card key={item.role} className="xau-card">
        <h3>{item.role}</h3>
        <p className="xau-role">{item.subtitle}</p>
        <p className="xau-status"><i className="xau-dot" data-mood={moods[item.role] ?? "idle"} aria-hidden="true" />{item.state}</p>
        <p>{item.status}</p>
        <p className="xau-metric"><span>{item.metric}</span> <b>{item.value}</b></p>
        {item.bar != null && <div className="xau-bar" aria-hidden="true"><i style={{ width: `${item.bar}%` }} /></div>}
        <time>{fmtTime(xauCard(row).updated, lang, true) ?? copy.notReported}</time>
      </Card>)}
    </div>
    {page === "overview" && <a className="desk-link" href="#desk">{copy.openDesk}</a>}
    {page === "desk" && <>
      <div data-section="history"><Card>
        <CardHead title={copy.history} help={copy.historyHelp} />
        {open ? <FieldList rows={open} /> : <p className="xau-status">{copy.noOpen}</p>}
        {history && history.length > 0 ? <ul className="xau-history">{history.map((item, index) => (
          <li key={index}><span>{item.time} · {item.direction}</span><b>{item.entry} · {item.result}</b></li>
        ))}</ul> : <p className="xau-status">{copy.noSignalsYet}</p>}
      </Card></div>
      <DeskLog lang={lang} rows={merged} link />
      <div data-section="playbook"><Card>
        <CardHead title={copy.playbook} />
        {book ? <ul className="xau-fields xau-playbook">{book.map(([label, value]) => (
          <li key={label}><span>{label}</span><b>{value}</b></li>
        ))}</ul> : <p className="xau-status">{copy.playbookMissing}</p>}
        <p className="xau-note">{copy.playbookCaption}</p>
      </Card></div>
    </>}
  </div>;
}

function DeskLog({ lang, rows, link }: { lang: Lang; rows: LogInput[]; link: boolean }) {
  const copy = deskCopy(lang);
  const [station, setStation] = useState<LogStation>("all");
  const log = deskRows(lang, rows, station);
  return <div data-section="log"><Card>
    <CardHead title={copy.log} help={copy.logHelp} />
    <div className="xau-filters" role="group" aria-label={copy.log}>
      {(["all", ...ROLES] as LogStation[]).map(item => (
        <button type="button" key={item} aria-pressed={station === item} onClick={() => setStation(item)}>
          {item === "all" ? copy.all : item}
        </button>
      ))}
    </div>
    {log.length ? <ul className="xau-log">{log.map((item, index) => (
      <li key={item.event + item.time + index}><time>{item.when}</time> <span>{item.label}</span></li>
    ))}</ul> : <p className="xau-status">{copy.emptyFilter}</p>}
    {link && <a className="desk-link" href="#preferences" onClick={() => requestPreferencesTab("activity")}>{copy.openActivity}</a>}
  </Card></div>;
}

function ActivityFeed({ lang }: { lang: Lang }) {
  const events = useLoad<{ events?: LogInput[] }>("/api/events", true);
  const alerts = useLoad<{ alerts?: LogInput[] }>("/api/alerts", true);
  const rows = [...(events.data?.events ?? []), ...(Array.isArray(alerts.data?.alerts) ? alerts.data.alerts : [])];
  return <DeskLog lang={lang} rows={rows} link={false} />;
}

export function XauSettings({ lang, row, showLog = false }: { lang: Lang; row: Status | null; showLog?: boolean }) {
  const copy = deskCopy(lang);
  const alerts = useLoad<{ alerts?: unknown[] }>("/api/alerts", true);
  const count = Array.isArray(alerts.data?.alerts) ? String(alerts.data.alerts.length) : copy.notReported;
  const basis = basisRead(lang, row);
  const xau = row?.xau;
  const age = typeof xau?.last_bar_age_s === "number" ? `${Math.round(xau.last_bar_age_s)}s` : copy.notReported;
  const halt = xau?.halt;
  const yesNo = (value: boolean | undefined) => value == null ? copy.notReported : value ? copy.yes : copy.no;
  return <div className="xau-board">
    {postponedShowsLog(showLog ? "activity" : null) && <ActivityFeed lang={lang} />}
    <p className="xau-note" data-section="settings">{copy.settingsHelp}</p>
    <Card>
      <CardHead title={copy.mode} />
      <FieldList rows={[
        [copy.mode, row?.mode ? row.mode.toUpperCase() : copy.notReported],
        [copy.alerts, count],
      ]} />
      <p>{copy.password}</p>
      <a className="desk-link" href="#desk">{copy.playbookLink}</a>
    </Card>
    <div data-section="risk"><Card>
      <CardHead title={copy.risk} help={copy.riskHelp} />
      <FieldList rows={[
        [copy.feedState, chipLabel(lang, analysisChip(row))],
        [copy.feedAge, age],
        [copy.haltGlobal, yesNo(halt?.global)],
        [copy.haltXau, yesNo(halt?.xau)],
        [copy.basis, basis.text],
      ]} />
    </Card></div>
  </div>;
}
