import { currentPublication } from "./tradingdesk/room";
import { currentResumeAck, forgetResumeAck, recordResumeAck, requestPreferencesTab, resumeGate } from "./tradingdesk/activity-model";
import { instant, sourceInstant } from "./tradingdesk/frames";
import { TradingRoom } from "./tradingdesk/TradingRoom";
import { overviewTelemetry } from "./tradingdesk/adapters";
import { Evidence, readLabel, roleLabel } from "./tradingdesk/Evidence";
import {
  Activity,
  ChevronRight,
  Bot,
  Boxes,
  CandlestickChart,
  ScrollText,
  CircleDollarSign,
  Cpu,
  Crosshair,
  Layers3,
  Radio,
  ScanLine,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api, type CardRow, type Status } from "./api";
import { t, type Lang, type Key } from "./i18n";
import { Badge, Button, Card, CardHead, Field, Loading, Problem, inputClass } from "./ui";
import { Age } from "./age";
import { useLoad } from "./snapshot";
import { fmtMoney, fmtNumber, fmtPct, fmtPrice, fmtTime, sourceLabel } from "./format";

type StatusRead = ReturnType<typeof useLoad<Status>>;
export function stateLabel(lang: Lang, state?: string) {
  const labels: Record<string, Key> = {
    ready: "ready",
    pass: "pass",
    blocked: "blocked",
    pending: "pending",
    ok: "healthy",
    cooldown: "cooldown",
    unconfigured: "unconfiguredProvider",
  };
  return state ? (labels[state] ? t(lang, labels[state]) : state.replaceAll("_", " ")) : "—";
}
export function Providers({ lang, data, heading = true, current = true }: { lang: Lang; data?: Status | null; heading?: boolean; current?: boolean }) {
  return (
    <div className="providers">
      {heading ? <h3>{t(lang, "providers")}</h3> : null}
      {data?.providers && Object.keys(data.providers).length ? (
        Object.entries(data.providers).map(([key, provider]) => {
          const until = provider.until ? fmtTime(provider.until, lang, true) : null;
          return (
            <div className="provider" key={key}>
              <strong>{key}</strong>
              <Badge tone={provider.state === "ok" && current ? "good" : provider.state === "ok" ? "neutral" : "warn"}>{stateLabel(lang, provider.state)}</Badge>
              <small>{[provider.model, provider.detail, until ? `${t(lang, "rkUntil")} ${until}` : null].filter(Boolean).join(" · ")}</small>
            </div>
          );
        })
      ) : (
        <p>{t(lang, "noProvider")}</p>
      )}
    </div>
  );
}

export function RiskControls({ lang, row, reload }: { lang: Lang; row: Status; reload: () => void }) {
  const [action, setAction] = useState<"halt" | "resume" | null>(null);
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Key | null>(null);
  const [, acknowledged] = useState(0);
  useEffect(() => {
    if (note === "haltAck") forgetResumeAck();
    else if (note === "resumeAck") recordResumeAck(Date.now(), row.published_at ?? null);
    else return;
    acknowledged((n) => n + 1);
  }, [note]);
  const awaiting = resumeGate(currentResumeAck(), row.published_at, Date.now()) === "awaiting";
  async function send() {
    if (!action || busy || word.trim().toUpperCase() !== action.toUpperCase()) return;
    setBusy(true);
    try {
      await api(action === "halt" ? "/api/pause" : "/api/resume", { method: "POST" });
      setAction(null);
      setNote(action === "halt" ? "haltAck" : "resumeAck");
      reload();
    } catch {
      setNote("error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <h2 className="panel-title">
        <ShieldCheck />
        {t(lang, "control")}
      </h2>
      <div className="ledger">
        <span>{t(lang, "riskGate")}</span>
        <b>
          {awaiting
            ? t(lang, "actAwaitingFresh")
            : row.halt
            ? "HALT"
            : row.gating === "awaiting_worker"
              ? t(lang, "awaitingWorker")
              : t(lang, row.risk_permitted ? "riskOn" : "riskOff")}
        </b>
      </div>
      <div className="ledger">
        <span>{t(lang, "execution")}</span>
        <b>{awaiting ? t(lang, "actAwaitingFresh") : t(lang, row.halt || !row.execution_permitted ? "off" : "on")}</b>
      </div>
      {awaiting && <p role="status" className="panel-note risk-awaiting">{t(lang, "actAwaitingFreshHelp")}</p>}
      <p className="panel-note">{t(lang, "haltHelp")}</p>
      <div className="action-row">
        {(["halt", "resume"] as const).map((a) => (
          <Button
            key={a}
            variant={a === "halt" ? "danger" : "outline"}
            disabled={busy}
            onClick={() => {
              setAction(a);
              setWord("");
              setNote(null);
            }}
          >
            {t(lang, a)}
          </Button>
        ))}
      </div>
      {action && (
        <form
          className="confirm-box"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <Field label={t(lang, action === "halt" ? "confirmHalt" : "confirmResume")}>
            <input
              autoFocus
              className={inputClass}
              autoComplete="off"
              value={word}
              onChange={(e) => setWord(e.target.value)}
            />
          </Field>
          <div className="action-row">
            <Button disabled={busy || word.trim().toUpperCase() !== action.toUpperCase()}>
              {action.toUpperCase()}
            </Button>
            <Button type="button" variant="ghost" disabled={busy} onClick={() => setAction(null)}>
              {t(lang, "cancel")}
            </Button>
          </div>
        </form>
      )}
      {note && (
        <p role="status" className="panel-note">
          {t(lang, note)}
        </p>
      )}
    </Card>
  );
}

function Pipeline({ lang, row }: { lang: Lang; row: Status }) {
  const current = currentPublication(row.published_at, Date.now());
  const p = row.pipeline;
  const stages = [
    { key: "stepScreen", icon: ScanLine, stage: p?.screening, source: p?.screening?.source },
    { key: "stepAnalysis", icon: Cpu, stage: p?.analysis, source: p?.analysis?.mode },
    {
      key: "stepRisk",
      icon: ShieldCheck,
      stage: p?.risk,
      source: p?.risk && "AEGIS",
    },
    {
      key: "stepDesk",
      icon: Crosshair,
      stage: p?.opportunities,
      source: p?.opportunities && t(lang, "research"),
    },
  ] as const;
  return (
    <Card className="pipeline">
      <div className="panel-heading">
        <h2 className="panel-title">
          <Layers3 />
          {t(lang, "pipeline")}
        </h2>
        <small>
          {stages.length} {t(lang, "stages")}
        </small>
      </div>
      <ol>
        {stages.map(({ key, icon: Icon, stage, source }) => (
          <li key={key}>
            <details className="stage-details">
              <summary>
                <span className="stage-icon">
                  <Icon size={19} />
                </span>
                <div className="stage-main">
                  <div className="stage-top">
                    <h3>{t(lang, key)}</h3>
                    <Badge
                      tone={
                        !current || stage?.state === "blocked"
                          ? "bad"
                          : stage?.state === "pending"
                            ? "warn"
                            : stage?.state === "pass" || stage?.state === "ready"
                              ? "good"
                              : "neutral"
                      }
                    >
                      {current ? stateLabel(lang, stage?.state) : t(lang, "readStale")}
                    </Badge>
                  </div>
                  <div className="stage-meta">
                    <span className="stage-source">{sourceLabel(source) || t(lang, "unknown")}</span>
                    {current && stage && stage.state !== "blocked" && "count" in stage && (
                      <span>
                        {t(lang, "outputs")} <b>{fmtNumber(stage.count, lang, 0)}</b>
                      </span>
                    )}
                    {stage && "reason" in stage && (
                      <span>
                        {stage.reason === "awaiting_worker"
                          ? t(lang, "awaitingWorker")
                          : stage.reason.replaceAll("_", " ")}
                      </span>
                    )}
                    {key === "stepScreen" && (
                      <span>{t(lang, current && row.market_data_fresh ? "fresh" : "stale")}</span>
                    )}
                  </div>
                </div>
                <span className="disclose" aria-hidden="true">
                  ⌄
                </span>
              </summary>
              <dl className="stage-facts">
                <dt>{t(lang, "stageTime")}</dt>
                <dd>{t(lang, "unknown")}</dd>
                <dt>{t(lang, "freshness")}</dt>
                <dd>
                  {key === "stepScreen"
                    ? t(lang, current && row.market_data_fresh ? "fresh" : "stale")
                    : t(lang, "unknown")}
                </dd>
                <dt>{t(lang, "inputCount")}</dt>
                <dd>
                  {t(lang, "unavailable")} · {t(lang, "notInSnapshot")}
                </dd>
                <dt>{t(lang, "outputs")}</dt>
                <dd>
                  {current && stage && stage.state !== "blocked" && "count" in stage
                    ? fmtNumber(stage.count, lang, 0)
                    : `${t(lang, "unavailable")} · ${t(lang, "notInSnapshot")}`}
                </dd>
              </dl>
            </details>
          </li>
        ))}
      </ol>
      <a className="desk-link" href="#signals">{t(lang, "signals")} <ChevronRight size={16} /></a>
    </Card>
  );
}

function ScannerSummary({ lang, row }: { lang: Lang; row: Status }) {
  const current = currentPublication(row.published_at, Date.now());
  const screen = row.pipeline?.screening;
  const analysis = row.pipeline?.analysis;
  const source = sourceLabel(screen?.source);
  const feed = useLoad<{ source: string; fetched_at?: string | null; error: string | null; hits: unknown[] }>(
    "/api/screener",
    true,
  );
  const feedSource = sourceLabel(feed.data?.source);
  const risk = row.halt ? "HALT" : !current ? t(lang, "readStale") : row.gating === "awaiting_worker" ? t(lang, "awaitingWorker")
    : row.risk_permitted === false ? t(lang, "gateBlocked") : stateLabel(lang, row.pipeline?.risk?.state);
  return (
    <section className="scan-card" aria-labelledby="scan-title">
      <div className="scan-body">
        <div className="scan-copy">
          <p className="scan-source">
            <ScanLine size={16} />
            <span>
              {t(lang, "workerDiscovery")} · <b>{source || t(lang, "unknown")}</b>
            </span>
          </p>
          <h2 id="scan-title">{t(lang, "scanTitle")} <em>{t(lang, "scanAccent")}</em></h2>
          <p className="scan-count">
            <strong>{current && screen?.state === "ready" ? fmtNumber(screen.count, lang, 0) ?? t(lang, "unknown") : t(lang, current && screen?.state === "blocked" ? "blocked" : "unknown")}</strong> {current && screen?.state === "ready" && t(lang, "symbols")}
          </p>
          <dl className="scan-facts">
            <div>
              <dt>{t(lang, "feedStatus")}</dt>
              <dd>
                {feed.data ? (
                  <Age at={feed.data.fetched_at} limit={1800} lang={lang} reason={feed.data.error} />
                ) : (
                  t(lang, "unknown")
                )}
              </dd>
            </div>
            <div>
              <dt>{t(lang, "manualResearch")}</dt>
              <dd>{feedSource || t(lang, "unknown")}</dd>
            </div>
          </dl>
        </div>
        <div className="scan-dial">
          <strong>{current ? fmtNumber(analysis?.count, lang, 0) ?? "—" : "—"}</strong>
          <span>{t(lang, "analysisOutputs")}</span>
        </div>
      </div>
      <div className="scan-foot">
        <span className={row.halt || row.risk_permitted === false ? "risk-warning" : undefined}>
          <ShieldCheck size={16} /><span><span className="sr-only">{t(lang, "riskStatus")} · </span><b>{risk}</b></span>
        </span>
        <a href="#screener">{t(lang, "openScreener")} <ChevronRight size={15} /></a>
      </div>
    </section>
  );
}

function useDeskTelemetry(status: StatusRead) {
  const positions = useLoad("/api/positions", true);
  const bots = useLoad("/api/warp-bots", true);
  const book = useLoad("/api/account-positions", true);
  const accounts = useLoad("/api/accounts", true);
  const events = useLoad<EventPayload>("/api/events", true);
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => tick(n => n + 1), 5000);
    return () => window.clearInterval(timer);
  }, []);
  const now = Date.now();
  const model = overviewTelemetry({ status, positions, bots, accounts, book, events }, now);
  return { now, model, events };
}

export function DeskPage({ lang, status }: { lang: Lang; status: StatusRead }) {
  const { now, model, events } = useDeskTelemetry(status);
  return <div className="control-desk">
    <TradingRoom lang={lang} agents={model.agents} status={status.data} events={events.data?.events} now={now} />
    <DeskMetrics lang={lang} model={model} />
    <div className="monitor-grid">
      <DeskResearch lang={lang} status={status} now={now} />
      <EventLog lang={lang} loaded={events} now={now} />
    </div>
  </div>;
}

function DeskResearch({ lang, status, now }: { lang: Lang; status: StatusRead; now: number }) {
  const loaded = useLoad<{ cards: CardRow[] }>("/api/opportunities", true);
  const card = loaded.data?.cards?.[0];
  const [checkedAt, setCheckedAt] = useState(0);
  const clock = Math.max(now, checkedAt);
  const current = !status.issue && currentPublication(status.data?.published_at, clock);
  const evidenceAt = sourceInstant(card?.evidence_as_of, clock);
  const signalAt = sourceInstant(card?.as_of, clock);
  const expiresAt = instant(card?.expires_at);
  const expired = expiresAt !== null && clock > expiresAt;
  const fresh = current && !loaded.issue && card?.fresh === true && signalAt !== null &&
    evidenceAt !== null && expiresAt !== null && !expired;
  useEffect(() => {
    if (!fresh || expiresAt === null) return;
    const delay = Math.max(0, expiresAt + 1 - Date.now());
    // Longer deadlines are still checked by the desk clock.
    if (delay > 2_147_483_647) return;
    const timer = window.setTimeout(() => setCheckedAt(Date.now()), delay);
    return () => window.clearTimeout(timer);
  }, [fresh, expiresAt]);
  const verdict = card && ["BUY", "WAIT", "SKIP"].includes(card.action) ? card.action : t(lang, "unknown");
  const state = loaded.issue ? t(lang, "refreshError") : !loaded.data ? t(lang, "loading") :
    !current || card?.fresh === false || expired ? t(lang, "readStale") : !card ? t(lang, "readEmpty") :
    fresh ? t(lang, "readFresh") : t(lang, "freshnessUnknown");
  return <Card className="desk-research">
    <CardHead icon={Cpu} title={t(lang, "deskResearch")} aside={<Badge tone={fresh ? "info" : "neutral"}>{state}</Badge>} />
    {card ? <div className="desk-research-body">
      <strong>{card.symbol} · {verdict}</strong>
      <p>{(lang === "id" ? card.why_id : card.thesis) || t(lang, "unknown")}</p>
      <small>{t(lang, "marketTime")} · {fmtTime(evidenceAt, lang, true) ?? t(lang, "unknown")}</small>
    </div> : loaded.data && !loaded.issue ? <p>{t(lang, "deskNoResearch")}</p> : null}
    <a className="desk-link" href="#signals">{t(lang, "signals")} <ChevronRight size={16} /></a>
  </Card>;
}

function DeskMetrics({ lang, model }: { lang: Lang; model: ReturnType<typeof overviewTelemetry> }) {
  const metrics = [
    { title: "equity", icon: Wallet, detail: "accountMark", unit: "USDT" },
    { title: "pnl", icon: CircleDollarSign, detail: "todayUTC", unit: "USDT" },
    { title: "activeBots", icon: Bot, detail: "owned" },
    { title: "open", icon: Boxes, detail: "listPositions" },
  ] as const;
  return (
      <div className="metrics">
        {metrics.map((m) => {
          const observation = model.metrics[m.title];
          const value = observation.value;
          return (
            <Card key={m.title} className="kpi">
              <div>
                <span>{t(lang, m.title)}</span>
                <m.icon size={16} />
              </div>
              <strong className={value == null ? "kpi-missing" : undefined}>
                {value == null
                  ? t(lang, "unknown")
                  : "unit" in m
                    ? fmtMoney(value, lang) ?? t(lang, "unknown")
                    : fmtNumber(value, lang, 0)}{" "}
                {value != null && "unit" in m && <small>{m.unit}</small>}
              </strong>
              <small>{t(lang, m.detail)} · {readLabel(lang, observation.state)}</small>
            </Card>
          );
        })}
      </div>
  );
}

export function Overview({ lang, status }: { lang: Lang; status: StatusRead }) {
  const { now, model, events } = useDeskTelemetry(status);
  if (status.issue === "awaiting_first_snapshot") return <p role="status">{t(lang, "awaitingWorker")}</p>;
  if (status.error) return <Problem lang={lang} retry={status.reload} />;
  if (!status.data) return <Loading lang={lang} />;
  const row = status.data;
  return (
    <div className="overview">
      <DeskMetrics lang={lang} model={model} />
      <a className="desk-link overview-room-link" href="#desk">{t(lang, "roomEntry")} <ChevronRight size={16} /></a>
      <Evidence lang={lang} model={model} />
      <div className="monitor-grid">
        <div className="monitor-col">
          <ScannerSummary lang={lang} row={row} />
          <Pipeline lang={lang} row={row} />
          <Card>
            <div className="panel-heading">
              <h2 className="panel-title">
                <Radio />
                {t(lang, "coverage")}
              </h2>
              <small>
                {t(lang, "symbols")} · {row.universe?.length ?? t(lang, "unknown")}
              </small>
            </div>
            <div className="symbols">
              {row.universe?.length
                ? row.universe.map((symbol) => <code key={symbol}>{symbol}</code>)
                : t(lang, "noSymbols")}
            </div>
          </Card>
        </div>
        <div className="monitor-col">
          <RiskControls lang={lang} row={row} reload={status.reload} />
          <Card>
            <h2 className="panel-title"><Wallet />{t(lang, "venues")}</h2>
            <div className="ledger">
              <span>{t(lang, "open")}</span>
              <b>{model.metrics.open.value ?? readLabel(lang, model.metrics.open.state)}
                {model.metrics.open.value != null && model.metrics.open.state !== "confirmed" &&
                  model.metrics.open.state !== "confirmed_empty" && " · " + readLabel(lang, model.metrics.open.state)}
              </b>
            </div>
            <div className="ledger"><span>PORTFOLIO</span><b>{roleLabel(lang, model.agents.PORTFOLIO)}</b></div>
            <a className="desk-link" href="#preferences">{t(lang, "venues")} <ChevronRight size={16} /></a>
          </Card>
          <Card>
            <h2 className="panel-title">
              <Activity />
              {t(lang, "system")}
            </h2>
            {(
              [
                ["worker", row.alive],
                ["dependencies", row.dependencies_healthy],
              ] as const
            ).map(([key, value]) => (
              <div className="ledger" key={key}>
                <span>{t(lang, key)}</span>
                <Badge tone={value == null ? "neutral" : value ? "good" : "warn"}>
                  {t(
                    lang,
                    value == null
                      ? "unknown"
                      : value
                        ? key === "worker"
                          ? "online"
                          : "healthy"
                        : "degraded",
                  )}
                </Badge>
              </div>
            ))}
            <Providers lang={lang} data={row} />
          </Card>
          <PriceTape lang={lang} />
          <EventLog lang={lang} loaded={events} now={now} />
        </div>
      </div>
    </div>
  );
}

function PriceTape({ lang }: { lang: Lang }) {
  const loaded = useLoad<{
    symbols: Record<string, { last: string; change_pct: string | null }>;
    as_of: string | null;
    error: string | null;
    limit_seconds?: number;
  }>("/api/prices", true);
  const row = loaded.data;
  const symbols = Object.entries(row?.symbols || {});
  return (
    <Card>
      <CardHead
        icon={CandlestickChart}
        title={t(lang, "priceTape")}
        help={<Age at={row?.as_of} limit={row?.limit_seconds || 30} lang={lang} reason={row?.error} />}
      />
      {loaded.error ? <Problem lang={lang} retry={loaded.reload} /> : null}
      {symbols.length ? (
        <div className="tape">
          {symbols.map(([symbol, quote]) => (
            <div key={symbol}>
              <span>{symbol}</span>
              <b>{fmtPrice(quote.last, lang) ?? t(lang, "unavailable")}</b>
              <small
                className={
                  quote.change_pct == null ? "na" : Number(quote.change_pct) > 0 ? "up" : Number(quote.change_pct) < 0 ? "down" : undefined
                }
              >
                {fmtPct(quote.change_pct, lang) ?? t(lang, "unavailable")}
              </small>
            </div>
          ))}
        </div>
      ) : (
        <p className="panel-note">
          {t(lang, "unavailable")} · {row?.error || t(lang, "noPrices")}
        </p>
      )}
    </Card>
  );
}

type EventPayload = { events: { event?: string; ts?: string }[]; as_of?: string | null };
function EventLog({ lang, loaded, now }: { lang: Lang; loaded: ReturnType<typeof useLoad<EventPayload>>; now: number }) {
  const events = loaded.data?.events || [];
  return (
    <Card>
      <CardHead
        icon={ScrollText}
        title={t(lang, "events")}
        help={<Age at={loaded.data?.as_of} limit={60} lang={lang} reason={loaded.issue ? t(lang, "refreshError") : null} />}
      />
      {events.length ? (
        <>
          <ul className="event-log">
            {events.slice(-8).map((row, index) => (
              <li key={`${row.ts}-${index}`}>
                <time dateTime={row.ts}>{fmtTime(sourceInstant(row.ts, now), lang) ?? t(lang, "noTimestamp")}</time>
                <span>{row.event ? row.event.replaceAll("_", " ") : t(lang, "unavailable")}</span>
              </li>
            ))}
          </ul>
          <p className="panel-note event-note">
            {t(lang, "actLatest")} {Math.min(8, events.length)} {t(lang, "actOf")} {events.length} {t(lang, "actReported")} · {t(lang, "actEventsNote")}
          </p>
        </>
      ) : (
        <p className="panel-note">
          {t(lang, "unavailable")} · {t(lang, "noSnapshot")}
        </p>
      )}
      <a className="desk-link event-open" href="#preferences" onClick={() => requestPreferencesTab("activity")}>
        {t(lang, "actOpen")} <ChevronRight size={16} />
      </a>
    </Card>
  );
}
