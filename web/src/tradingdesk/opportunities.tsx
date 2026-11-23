import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowUpRight, Crosshair, ListFilter, ScanLine, ShieldCheck, X } from "lucide-react";
import { t, type Key, type Lang } from "../i18n";
import { fmtCompact, fmtNumber, fmtPct, fmtPct01, fmtPrice, fmtTime, ratingLabel, sourceLabel } from "../format";
import { useLoad } from "../snapshot";
import { Badge, Button, Card, CardHead } from "../ui";
import { readLabel } from "./Evidence";
import { discovery, gateDecisions, journalEvidence, opportunities, researchFeed, type Observation } from "./opportunity-model";
import type { ReadInput } from "./types";

type Props = { lang: Lang; status: ReadInput };
type Gates = ReturnType<typeof gateDecisions>;
type History = ReturnType<typeof journalEvidence>;
const when = (v: string | null, lang: Lang) => fmtTime(v, lang, true) ?? t(lang, "unknown");

function reasonLabel(code: string | null, lang: Lang) {
  return code === "unlisted" ? t(lang, "unlistedCode") : code ?? t(lang, "unknown");
}

function useClock() {
  const [now, set] = useState(Date.now);
  useEffect(() => { const id = window.setInterval(() => set(Date.now()), 5000); return () => clearInterval(id); }, []);
  return now;
}
function State({ item, lang }: { item: Observation; lang: Lang }) {
  const warn = !["confirmed", "confirmed_empty"].includes(item.state);
  return <Badge tone={warn ? "warn" : "neutral"}>{readLabel(lang, item.state)}</Badge>;
}
function Facts({ rows, lang }: { rows: [Key, ReactNode][]; lang: Lang }) {
  return <dl className="p4-facts">{rows.map(([key, value]) =>
    <div key={key}><dt>{t(lang, key)}</dt><dd>{value ?? t(lang, "unknown")}</dd></div>)}</dl>;
}
function Chart({ pair, lang }: { pair: string | null; lang: Lang }) {
  if (!pair || !/^[A-Z0-9]+USDT$/.test(pair)) return null;
  return <a className="desk-link" href={'https://www.tradingview.com/chart/?symbol=BINANCE%3A' + encodeURIComponent(pair)}
    target="_blank" rel="noopener noreferrer">{pair} · {t(lang, "chart")} <ArrowUpRight size={14} /></a>;
}
const outcomes: Record<string, Key> = { passed: "marketPassed", rejected: "marketRejected", not_submitted: "notSubmitted",
  planned: "riskPlanned", skipped: "skippedRead", fail_closed: "blocked" };

function Checks({ history, pair, lang }: { history: History; pair?: string; lang: Lang }) {
  const rows = history.value?.filter(row => pair ? row.symbol === pair : row.stage === "market" || row.stage === "universe");
  return <section className="p4-checks"><h3>{t(lang, pair ? "evidenceLog" : "marketHistory")}</h3>
    <State item={history} lang={lang} />
    {rows?.map((row, i) => <div key={i} className="p4-record"><strong>{row.symbol ?? t(lang, "unknown")}</strong>
      <span>{row.stage} · {row.outcome ? t(lang, outcomes[row.outcome]) : t(lang, "unknown")}</span>
      <code>{row.reasons.map(r => reasonLabel(r, lang)).join(", ") || t(lang, "unknown")}</code>
      <time dateTime={row.at ?? undefined}>{when(row.at, lang)}</time></div>)}
    {rows?.length === 0 && ["confirmed", "confirmed_empty"].includes(history.state) && <p>{t(lang, "noChecks")}</p>}
  </section>;
}

export function DiscoveryPanel({ status, lang }: Props) {
  const config = useLoad<{ discovery?: string }>("/api/config", true);
  const journal = useLoad("/api/signals", true);
  const now = useClock();
  const worker = discovery(status, now);
  const mode = config.issue ? null : config.data?.discovery;
  return <Card className="p4-panel"><CardHead icon={ScanLine} title={t(lang, "exchangeDiscovery")}
    aside={<State item={worker.universe} lang={lang} />} />
    <Facts lang={lang} rows={[
      ["autoSource", mode === "auto" ? sourceLabel(worker.source) : null], ["discoveryMode", mode === "auto" ? t(lang, "autoUniverse") : mode === "static" ? t(lang, "staticUniverse") : mode === "screener" ? "Screener" : null],
      ["roomState", worker.stage === "ready" || worker.stage === "blocked" ? t(lang, worker.stage) : null],
      ["roomPublication", when(worker.publication.sourceAt, lang)], ["clientReceipt", when(worker.universe.receivedAt, lang)],
      ["reason", reasonLabel(worker.reason, lang)],
    ]} />
    <h3>{t(lang, "universeMembers")}</h3>
    <div className="symbols">{worker.universe.value?.length ? worker.universe.value.map((v, i) => <code key={i}>{v}</code>) :
      readLabel(lang, worker.universe.state)}</div>
    {mode === "auto" && <p className="panel-note">{t(lang, "noSubstitution")}</p>}
    <p className="panel-note">{t(lang, "pairGaps")}</p>
    <Checks lang={lang} history={journalEvidence(journal, worker, now)} />
  </Card>;
}

export function ResearchPanel({ lang, status, feed }: Props & { feed: ReadInput }) {
  const now = useClock();
  const result = researchFeed(feed, discovery(status, now), now);
  return <Card className="p4-panel"><CardHead icon={ListFilter} title={t(lang, "manualResearch")}
    aside={<State item={result.feed} lang={lang} />} /><p>{t(lang, "manualOnly")}</p>
    <Facts lang={lang} rows={[
      ["source", sourceLabel(result.source) || null], ["readTime", when(result.feed.sourceAt, lang)],
      ["clientReceipt", when(result.feed.receivedAt, lang)],
    ]} />
    {result.failed && <p className="alert">{t(lang, "feedFailed")} {result.error}</p>}
    {result.feed.value?.map((hit, i) => {
      const matched = hit.source === "tradingview" && result.source === hit.source;
      return <details className="p4-link" key={i}>
        <summary>{hit.symbol ?? t(lang, "unknown")}{!matched && " · " + t(lang, "manualMismatch")}</summary>
        {matched && <>
          <Facts lang={lang} rows={[
            ["price", fmtPrice(hit.price, lang)], ["momentum", fmtPct(hit.change, lang)],
            ["volume", fmtCompact(hit.volume, lang)], ["rsi", fmtNumber(hit.rsi, lang, 1)],
            ["recommendation", ratingLabel(hit.recommendation, lang)?.text],
          ]} />
          <Chart pair={hit.symbol} lang={lang} />
        </>}
      </details>;
    })}
  </Card>;
}

const gateExplanation: Record<string, [string, string]> = {
  wrong_account: ["Account mismatch", "Akun tidak cocok"], stale: ["Evidence has expired", "Bukti kedaluwarsa"],
  risk_off: ["Market risk filter blocked entry", "Filter risiko pasar memblokir entri"],
  daily_loss: ["Daily loss limit reached", "Batas rugi harian tercapai"], drawdown: ["Drawdown limit reached", "Batas drawdown tercapai"],
  not_binance_spot: ["Outside Binance spot", "Di luar spot Binance"], not_trading: ["Market closed for trading", "Pasar tidak diperdagangkan"],
  listing_unknown: ["Listing age is unavailable", "Usia listing belum tersedia"], listing_young: ["Listing is too recent", "Listing terlalu baru"],
  spread: ["Spread check failed", "Pemeriksaan spread gagal"], thin_book: ["Insufficient liquidity", "Likuiditas kurang"],
  surge: ["Price surge filter triggered", "Filter lonjakan harga aktif"], model_not_buy: ["Model did not select BUY", "Model tidak memilih BUY"],
  score: ["Rubric threshold was not met", "Ambang skor rubrik belum tercapai"], stop_streak: ["Stop-loss streak limit reached", "Batas rentetan stop-loss tercapai"],
  stop_cooldown: ["Cooldown after stop-loss", "Masa jeda setelah stop-loss"], tp_cooldown: ["Cooldown after take-profit", "Masa jeda setelah take-profit"],
  max_deals: ["Open-deal limit reached", "Batas posisi terbuka tercapai"], duplicate_symbol: ["Symbol already open", "Posisi simbol sudah terbuka"],
  alt_cap: ["Altcoin cap reached", "Batas altcoin tercapai"], correlation_unknown: ["Correlation data unavailable", "Data korelasi belum tersedia"],
  correlation_cluster: ["Correlation limit reached", "Batas korelasi tercapai"], no_capital: ["No eligible capital", "Modal yang memenuhi syarat tidak tersedia"],
  no_feasible_ladder: ["No ladder fits the limits", "Tidak ada ladder yang memenuhi batas"], missing_stop: ["Stop is missing", "Stop tidak tersedia"],
  market_data: ["Market data incomplete", "Data pasar belum lengkap"], rules: ["Risk rules unavailable", "Aturan risiko belum tersedia"],
  no_sl: ["Stop-loss disabled", "Stop-loss nonaktif"], manual_exit: ["Manual exit configured", "Keluar secara manual"],
  score_below_80: ["Configured threshold below 80", "Ambang terkonfigurasi di bawah 80"],
};
function Reasons({ codes, lang }: { codes: (string | null)[] | null; lang: Lang }) {
  if (!codes?.length) return <>{t(lang, "unknown")}</>;
  return <ul>{codes.map((code, i) => <li key={i}><code>{reasonLabel(code, lang)}</code>
    {code && gateExplanation[code] && <span> · {gateExplanation[code][lang === "en" ? 0 : 1]}</span>}</li>)}</ul>;
}

function Assessment({ pair, gates, journal, lang, close, halt }: {
  pair: string; gates: Gates; journal: History; lang: Lang; halt: boolean; close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const trigger = document.activeElement;
    const el = ref.current!;
    el.showModal();
    return () => { el.close(); if (trigger instanceof HTMLElement) trigger.focus({ preventScroll: true }); };
  }, []);
  const matches = gates.value?.filter(g => g.symbol === pair);
  return <dialog ref={ref} className="room-dialog p4-dialog" aria-labelledby="p4-title"
    onCancel={event => { event.preventDefault(); close(); }}>
    <header><div><strong id="p4-title">AEGIS · {pair}</strong><p>{t(lang, "symbolAssessment")}</p></div>
      <button autoFocus aria-label={t(lang, "roomClose")} onClick={close}><X size={20} /></button></header>
    {halt && <p role="alert" className="room-halt">HALT · {t(lang, "gateBlocked")}</p>}
    <h3>{t(lang, "gateReasons")}</h3><code>/api/decisions</code> <State item={gates} lang={lang} />
    <p>{t(lang, "timeUnlinked")}</p>
    {matches?.map((g, i) => <Facts key={i} lang={lang} rows={[
      ["verdict", g.verdict], ["codeLabel", <Reasons codes={g.reasons} lang={lang} />],
      ["warningCode", <Reasons codes={g.warnings} lang={lang} />], ["assessmentTime", null],
    ]} />)}
    {!matches?.length && <p>{t(lang, "unknown")}</p>}
    <p>{t(lang, "clientReceipt")} · {when(gates.receivedAt, lang)}</p>
    <p>{t(lang, "sideEvidence")}</p><code>/api/signals</code><Checks history={journal} pair={pair} lang={lang} />
  </dialog>;
}

export function Opportunities({ status, lang }: Props) {
  const loaded = useLoad("/api/opportunities", true), decisions = useLoad("/api/decisions", true);
  const log = useLoad("/api/signals", true);
  const now = useClock();
  const worker = discovery(status, now), gates = gateDecisions(decisions, worker, now);
  const cards = opportunities(loaded, worker, gates, now), journal = journalEvidence(log, worker, now);
  const [pair, select] = useState<string | null>(null);
  const na = t(lang, "unknown");
  const other = gates.value?.filter(g => g.symbol && !cards.value?.some(card => card.symbol === g.symbol)) ?? [];
  return <section className="p4-workbench">
    <Card className="p4-band"><CardHead icon={Crosshair} title={t(lang, "workerRank")} help={t(lang, "rankHelp")}
      aside={<State item={cards} lang={lang} />} />
      <div className="p4-context"><span>{t(lang, "roomPublication")} · {when(worker.publication.sourceAt, lang)}</span>
      <span>{t(lang, "clientReceipt")} · {when(cards.receivedAt, lang)}</span><a className="desk-link" href="#screener">{t(lang, "openDiscovery")} ↗</a></div>
    </Card>
    {cards.state === "confirmed_empty" && <p className="p4-empty">{t(lang, "noRanked")}</p>}
    <div className="p4-grid">{cards.value?.map((card, i) => {
      const confirmed = card.evidence.state === "confirmed";
      const components = (["weightTrend", "weightReversal", "weightLiquidity", "weightStructure", "weightEvent"] as const)
        .map((k, n) => t(lang, k) + " " + (card.components[n] ?? na)).join(" · ");
      return <Card className="p4-card" key={i}>
        <div className="p4-card-head"><div><span className="rank">#{i + 1}</span><h2>{card.symbol ?? na}</h2></div>
          <Badge tone={confirmed && card.action === "BUY" ? "good" : "neutral"}>{card.action ?? na}</Badge>
        </div><div className="p4-status">
          {card.freshAtPublication ? <span>{t(lang, "freshAtPublication")} <time dateTime={card.freshAtPublication}>{when(card.freshAtPublication, lang)}</time></span> :
            <State lang={lang} item={card.evidence} />}
          <span>{t(lang, "evidenceAge")} {card.evidenceAgeMs === null ? na : Math.floor(card.evidenceAgeMs / 60_000) + " " + t(lang, "ageMinutes")}</span>
          {card.proposed && <strong>{t(lang, "proposedRead")}</strong>}</div>
        <h3>{t(lang, "thesis")}</h3><p>{card.thesis ?? na}</p>
        <h3>{t(lang, "levelsWorker")}</h3><div className="p4-levels">{([["entry", card.entry], ["stop", card.stop], ["target", card.take]] as const)
          .map(([k,v]) => <div key={k}><small>{t(lang,k)}</small><b>{fmtPrice(v, lang) ?? na}</b></div>)}</div>
        <Facts lang={lang} rows={[
          ["rubricScore", card.rubric === null ? null : <>{card.rubric} / 100{card.scoreWarning && <small className="p4-gate-read">{t(lang, "scoreWarning")}</small>}</>],
          ["modelVerdict", card.modelAction], ["source", card.source], ["model", null],
          ["backtest", [card.backtest === null ? na : t(lang, card.backtest ? "passed" : "failed"), card.backtestReason].filter(Boolean).join(" · ")],
          ["historyOnly", <>{card.gate.map(g => g.verdict ?? na).join(" · ") || na}<small className="p4-gate-read">AEGIS · {readLabel(lang, gates.state)} · {t(lang, "freshnessUnknown")}</small></>],
          ["signalClock", when(card.signalAt, lang)], ["marketTime", when(card.evidenceAt, lang)], ["modelClock", null],
        ]} />
        <details><summary>{t(lang, "why")}</summary><p>{(lang === "id" ? card.whyId : card.why) ?? na}</p>
          <Facts lang={lang} rows={[["scoreComponents", components], ["scoreThreshold", card.minScore], ["modelSelfConfidence", fmtPct01(card.confidence, lang)],
            ["regime", card.regime], ["rsi", fmtNumber(card.rsi, lang, 1)]]} /></details>
        <div className="p4-actions"><Button variant="outline" disabled={!card.symbol} onClick={() => select(card.symbol)}>
          <ShieldCheck size={16} />{t(lang, "gateDetail")}</Button><Chart pair={card.symbol} lang={lang} /></div>
      </Card>;
    })}</div>
    {other.length > 0 && <Card className="p4-panel"><h2>{t(lang, "gateHistory")}</h2><State lang={lang} item={gates}/>
      {other.map((g,i) => <Button variant="ghost" key={i} onClick={() => select(g.symbol)}>{g.symbol} · AEGIS</Button>)}</Card>}
    {pair && <Assessment pair={pair} lang={lang} gates={gates} journal={journal} halt={worker.halted === true} close={() => select(null)} />}
  </section>;
}
