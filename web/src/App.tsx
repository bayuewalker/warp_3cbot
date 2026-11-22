import { useCurrentRead } from "./account-read";
import {
  Activity,
  ArrowUpRight,
  Bell,
  Bot,
  Boxes,
  Cpu,
  FileSliders,
  Settings2,
  ShieldCheck,
  Shapes,
  ChevronRight,
  Clock3,
  Crosshair,
  LayoutDashboard,
  ListFilter,
  LogOut,
  RefreshCw,
  SlidersHorizontal,
} from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import {
  api,
  AuthError,
  type CardRow,
  type Filters,
  type Hit,
  type Position,
  type Preview,
  type Status,
} from "./api";
import { Key, Lang, t } from "./i18n";
import { AccountsPanel, BudgetForm, ForwardPanel, PaperCheck, RulesPage, WarpBotsPanel } from "./desk";
import { Badge, Button, Card, CardHead, Choice, Empty, Field, KV, Loading, Note, Problem, inputClass } from "./ui";
import { Overview, Providers } from "./monitor";
import { Age, ReceivedAge } from "./age";
import { settingLabel, settingValue } from "./labels";
import { zoneLabel, fmtCompact, fmtMoney, fmtPct01, fmtNumber, fmtPct, fmtPrice, fmtTime, ratingLabel, sourceLabel } from "./format";
import { stopLive } from "./live";
import { useLiveState, useLoad } from "./snapshot";

const nav = [
  { id: "overview", short: "overview", icon: LayoutDashboard },
  { id: "signals", short: "minisignals", icon: Crosshair },
  { id: "assets", short: "minibots", icon: Bot },
  { id: "screener", short: "screener", icon: ListFilter },
  { id: "preferences", short: "minisettings", icon: SlidersHorizontal },
] as const;
type Page = (typeof nav)[number]["id"];
const WORKER_STALE_MS = 15 * 60 * 1000;

function WorkerStamp({ lang, publishedAt }: { lang: Lang; publishedAt?: string | null }) {
  const parsed = publishedAt ? Date.parse(publishedAt) : Number.NaN;
  const known = Number.isFinite(parsed);
  const stale = known && Date.now() - parsed > WORKER_STALE_MS;
  return (
    <span>
      {t(lang, "workerSnapshot")}{" "}
      {known ? (
        <time dateTime={publishedAt ?? undefined}>{fmtTime(parsed, lang)}</time>
      ) : (
        t(lang, "workerTimeUnavailable")
      )}
      {stale ? <Badge tone="warn">{t(lang, "workerStale")}</Badge> : null}
    </span>
  );
}

function Brand() {
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
function pageFromHash(): Page {
  return nav.find((item) => item.id === location.hash.slice(1))?.id ?? "overview";
}
export function App() {
  const [lang, setLang] = useState<Lang>(() => (localStorage.getItem("warp.lang") === "id" ? "id" : "en"));
  const [authed, setAuthed] = useState(false);
  const session = useLoad<{ authenticated: boolean }>("/api/session");
  useEffect(() => {
    setAuthed(session.data?.authenticated === true);
  }, [session.data]);
  useEffect(() => {
    function expire() {
      setAuthed(false);
    }
    window.addEventListener("warp:session-expired", expire);
    return () => window.removeEventListener("warp:session-expired", expire);
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
    localStorage.setItem("warp.lang", lang);
  }, [lang]);
  const language = (
    <button
      className="tool-button language-button"
      aria-label={lang === "en" ? "EN · Switch to Indonesian" : "ID · Ganti ke bahasa Inggris"}
      onClick={() => setLang(lang === "en" ? "id" : "en")}
    >
      <span>{lang.toUpperCase()}</span>
    </button>
  );
  if (!authed)
    return (
      <main className="login-wrap">
        <div className="login-head">
          <Brand />
          {language}
        </div>
        {session.error ? (
          <Problem lang={lang} retry={session.reload} />
        ) : !session.data ? (
          <Loading lang={lang} />
        ) : (
          <Login
            lang={lang}
            onSuccess={() => {
              location.hash = "overview";
              setAuthed(true);
            }}
          />
        )}
      </main>
    );
  return (
    <Workspace
      lang={lang}
      language={language}
      logout={async () => {
        await api("/api/logout", { method: "POST" });
        stopLive();
        setAuthed(false);
      }}
    />
  );
}
function Login({ lang, onSuccess }: { lang: Lang; onSuccess: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!password) {
      setError(t(lang, "emptyPassword"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api("/api/login", { method: "POST", body: JSON.stringify({ password }) });
      onSuccess();
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status === 429) setError(t(lang, "locked"));
      else if (status === 503) setError(t(lang, "unconfigured"));
      else setError(t(lang, err instanceof AuthError ? "badLogin" : "error"));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card className="login-card">
      <form className="form-stack" onSubmit={submit}>
        <p className="eyebrow">WARP 3C · {t(lang, "desk")}</p>
        <h1 className="login-title">{t(lang, "login")}</h1>
        <p className="card-help">{t(lang, "loginHelp")}</p>
        <Field label={t(lang, "password")}>
          <input
            className={inputClass}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        {error ? (
          <p role="alert" className="form-note form-note-bad">
            {error}
          </p>
        ) : null}
        <Button className="w-full" type="submit" disabled={busy}>
          {busy ? t(lang, "signingIn") : t(lang, "signIn")}
        </Button>
      </form>
    </Card>
  );
}

function Workspace({
  lang,
  language,
  logout,
}: {
  lang: Lang;
  language: React.ReactNode;
  logout: () => Promise<void>;
}) {
  const [page, setPage] = useState<Page>(pageFromHash);
  const [logoutError, setLogoutError] = useState(false);
  const status = useLoad<Status>("/api/status", true);
  const row = status.error ? null : status.data;
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    function navigate() {
      setPage(pageFromHash());
      window.scrollTo(0, 0);
      requestAnimationFrame(() => heading.current?.focus({ preventScroll: true }));
    }
    window.addEventListener("hashchange", navigate);
    return () => window.removeEventListener("hashchange", navigate);
  }, []);
  const links = (mobile: boolean) =>
    nav.map(({ id, short, icon: Icon }) => (
      <a key={id} href={`#${id}`} aria-current={page === id ? "page" : undefined} aria-label={t(lang, id)}>
        <Icon size={18} />
        <span>{t(lang, mobile ? short : id)}</span>
      </a>
    ));
  const title = t(lang, page === "overview" ? "portfolio" : page);
  return (
    <div className="terminal">
      <a
        className="skip-link"
        href="#workspace"
        onClick={(e) => {
          e.preventDefault();
          heading.current?.focus();
        }}
      >
        {t(lang, "skipNav")}
      </a>
      <aside className="rail">
        <Brand />
        <div className="rail-label">{t(lang, "desk")}</div>
        <nav aria-label={t(lang, "navigation")}>{links(false)}</nav>
      </aside>
      <div className="workarea">
        <div className="header-group">
          <header className="topbar">
            <div className="breadcrumb">
              <Activity size={16} />
              <span>{t(lang, "desk")}</span>
              <ChevronRight size={14} />
              <strong>{t(lang, page)}</strong>
            </div>
            <div className="mobile-brand">
              <Brand />
            </div>
            <div className="toolbar">
              <LiveBadge lang={lang} />
              <Badge tone={row?.halt || row?.mode === "live" ? "bad" : row?.mode === "paper" ? "warn" : "info"}>
                {row?.mode?.toUpperCase() ?? "—"}
              </Badge>
              <Alerts lang={lang} row={row} halted={status.data?.halt === true} error={status.error} />
              {language}
              <button
                className="icon-button"
                aria-label={t(lang, "logout")}
                title={t(lang, "logout")}
                onClick={() => void logout().catch(() => setLogoutError(true))}
              >
                <LogOut size={17} />
              </button>
            </div>
          </header>
          {status.data?.halt && <div className="halt-strip" role="alert"><strong>HALT</strong> {t(lang, "haltHelp")}</div>}
        </div>
        <main id="workspace" className="workspace">
          {(status.error || logoutError) && (
            <div className="alert" role="alert">
              {status.error && (
                <button onClick={status.reload}>
                  {t(lang, "noSnapshot")} · {t(lang, "retry")}
                </button>
              )}
              {logoutError && t(lang, "error")}
            </div>
          )}
          <div className="page-heading">
            <div>
              <p className="eyebrow">
                WARP 3C · {t(lang, "timesIn")} {zoneLabel(lang)}
              </p>
              <h1 tabIndex={-1} ref={heading}>
                {title}
              </h1>
            </div>
            <div className="snapshot-clock">
              <Clock3 size={14} />
              <div className="snapshot-facts">
                <span>
                  {t(lang, "received")}{" "}
                  <time>{fmtTime(status.received, lang) ?? t(lang, "unknown")}</time>
                </span>
                <WorkerStamp lang={lang} publishedAt={status.data?.published_at} />
              </div>
              <button
                className="icon-button"
                onClick={status.reload}
                aria-label={t(lang, "refresh") + " · " + t(lang, "status")}
              >
                <RefreshCw size={15} />
              </button>
            </div>
          </div>
          {page === "overview" && <Overview lang={lang} status={status} />}
          {page === "signals" && (
            <div className="monitor-col">
              <Signals lang={lang} />
              <ForwardPanel lang={lang} />
            </div>
          )}
          {page === "assets" && <AssetDesk lang={lang} status={row} />}
          {page === "screener" && <ScreenerPage lang={lang} marketFresh={row?.market_data_fresh} />}
          {page === "preferences" && <Preferences lang={lang} status={row} />}
        </main>
      </div>
      <nav className="bottom-nav" aria-label={t(lang, "navigation")}>
        {links(true)}
      </nav>
    </div>
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

function Sections<K extends Key>({
  lang,
  items,
  value,
  change,
}: {
  lang: Lang;
  items: readonly K[];
  value: K;
  change: (v: K) => void;
}) {
  return (
    <div className="sections">
      {items.map((key) => (
        <button key={key} aria-pressed={key === value} onClick={() => change(key)}>
          {t(lang, key)}
        </button>
      ))}
    </div>
  );
}
function AssetDesk({ lang, status }: { lang: Lang; status: Status | null }) {
  const [tab, setTab] = useState<"manage" | "positions" | "template">("manage");
  return (
    <>
      <Sections
        lang={lang}
        items={["manage", "positions", "template"] as const}
        value={tab}
        change={setTab}
      />
      {tab === "manage" ? (
        <WarpBotsPanel lang={lang} status={status} />
      ) : tab === "positions" ? (
        <Positions lang={lang} />
      ) : (
        <Bots lang={lang} />
      )}
    </>
  );
}
function Preferences({ lang, status }: { lang: Lang; status: Status | null }) {
  const [tab, setTab] = useState<"venues" | "rules" | "configuration">("venues");
  const [selected, setSelected] = useState("");
  return (
    <>
      <Sections
        lang={lang}
        items={["venues", "rules", "configuration"] as const}
        value={tab}
        change={setTab}
      />
      {tab === "venues" ? (
        <div className="form-grid">
          <AccountsPanel lang={lang} onSelect={setSelected} />
          <BudgetForm key={selected} lang={lang} code={selected} />
        </div>
      ) : tab === "rules" ? (
        <RulesPage lang={lang} />
      ) : (
        <div className="monitor-col">
          <Card>
            <CardHead icon={Cpu} title={t(lang, "providers")} help={t(lang, "providersHelp")} />
            <Providers lang={lang} data={status} heading={false} />
          </Card>
          <Settings lang={lang} paper={status?.mode === "paper" && status.halt === false} />
        </div>
      )}
    </>
  );
}
function Signals({ lang }: { lang: Lang }) {
  const loaded = useLoad<{ cards: CardRow[] }>("/api/opportunities", true);
  if (loaded.error) return <Problem lang={lang} retry={loaded.reload} />;
  if (!loaded.data) return <Loading lang={lang} />;
  const fresh = (
    <p className="panel-note page-fresh">
      <ReceivedAge at={loaded.received} limit={90} lang={lang} />
    </p>
  );
  if (!loaded.data.cards.length)
    return (
      <div className="stack">
        {fresh}
        <Empty icon={Crosshair} text={t(lang, "noSignals")} />
      </div>
    );
  const na = t(lang, "unknown");
  return (
    <div className="stack">
    {fresh}
    <div className="opportunity-grid">
      {loaded.data.cards.map((card, index) => {
        const confidence = fmtPct01(card.confidence, lang);
        return (
          <Card key={card.symbol} className="signal-card">
            <CardHead
              title={
                <>
                  <span className="rank">#{index + 1}</span>
                  {card.symbol}
                </>
              }
              help={`${card.regime ? card.regime[0].toUpperCase() + card.regime.slice(1) : na} · RSI ${fmtNumber(card.rsi, lang, 1) ?? na}`}
              aside={
                <>
                  <Badge tone={card.action === "BUY" ? "good" : card.action === "SKIP" ? "bad" : "warn"}>
                    {card.action ?? na}
                  </Badge>
                  {card.fresh === false ? <Badge tone="warn">{t(lang, "staleEvidence")}</Badge> : null}
                </>
              }
            />
            <div className="metric-grid">
              <Metric label={t(lang, "entry")} value={fmtPrice(card.entry, lang)} empty={na} />
              <Metric label={t(lang, "stop")} value={fmtPrice(card.stop, lang)} empty={na} />
              <Metric label={t(lang, "target")} value={fmtPrice(card.take, lang)} empty={na} />
            </div>
            <KV
              rows={[
                [
                  t(lang, "score"),
                  card.score ? (
                    <>
                      <b>{card.score.total}</b> / 100
                      {card.score_warning ? <span className="text-warn"> · {t(lang, "scoreWarning")}</span> : null}
                    </>
                  ) : (
                    na
                  ),
                  card.score
                    ? `${t(lang, "weightTrend")} ${card.score.trend} · ${t(lang, "weightReversal")} ${card.score.reversal} · ${t(lang, "weightLiquidity")} ${card.score.liquidity} · ${t(lang, "weightStructure")} ${card.score.structure} · ${t(lang, "weightEvent")} ${card.score.event}`
                    : undefined,
                ],
                [
                  t(lang, "confidence"),
                  <>
                    {confidence ?? na}
                    {card.model_action && card.model_action !== card.action
                      ? ` · ${t(lang, "model")} ${card.model_action}`
                      : ""}
                  </>,
                ],
                [
                  t(lang, "backtest"),
                  <>
                    {card.backtest_passed == null ? na : card.backtest_passed ? t(lang, "passed") : t(lang, "failed")}
                    {card.backtest_reason ? ` · ${card.backtest_reason.replaceAll("_", " ")}` : ""}
                  </>,
                ],
                [t(lang, "source"), sourceLabel(card.source) || na],
                [t(lang, "signalTime"), <time>{fmtTime(card.as_of, lang, true) ?? na}</time>],
                [t(lang, "marketTime"), <time>{fmtTime(card.evidence_as_of, lang, true) ?? na}</time>],
                [t(lang, "exchange"), <span className="na">{t(lang, "unavailable")} · {t(lang, "notInSnapshot")}</span>],
                [t(lang, "riskScore"), <span className="na">{t(lang, "unavailable")} · {t(lang, "notInSnapshot")}</span>],
              ]}
            />
            <details className="disclosure">
              <summary>{t(lang, "why")}</summary>
              <p>{lang === "id" ? card.why_id : card.why}</p>
            </details>
            {/^[A-Z0-9]+USDT$/.test(card.symbol) && (
              <a
                className="btn btn-ghost link-button"
                href={`https://www.tradingview.com/chart/?symbol=BINANCE%3A${encodeURIComponent(card.symbol)}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                {t(lang, "chart")} · TradingView <ArrowUpRight size={15} />
              </a>
            )}
          </Card>
        );
      })}
    </div>
    </div>
  );
}

function Metric({ label, value, empty }: { label: string; value: string | null; empty: string }) {
  return (
    <div className="metric-cell">
      <p>{label}</p>
      <strong className={value == null ? "kpi-missing" : undefined}>{value ?? empty}</strong>
    </div>
  );
}

function helpKey(preset: "conservative" | "moderate" | "aggressive"): Key {
  if (preset === "conservative") return "conservativeHelp";
  if (preset === "aggressive") return "aggressiveHelp";
  return "moderateHelp";
}

function Bots({ lang }: { lang: Lang }) {
  const loaded = useLoad<{
    template: {
      kind: string;
      preset: string;
      market?: string;
      account_code?: string;
      stop_enabled?: boolean;
      take_profit_enabled?: boolean;
      trailing_enabled?: boolean;
      min_score?: number;
      weights?: { trend: number; reversal: number; liquidity: number; structure: number; event: number };
    };
    preview: Preview;
  }>("/api/bots");
  const [kind, setKind] = useState("dca");
  const [preset, setPreset] = useState("moderate");
  const [stopOn, setStopOn] = useState(true);
  const [takeOn, setTakeOn] = useState(true);
  const [trailingOn, setTrailingOn] = useState(false);
  const [minScore, setMinScore] = useState(80);
  const [weights, setWeights] = useState({
    trend: 25,
    reversal: 25,
    liquidity: 20,
    structure: 20,
    event: 10,
  });
  const [accountCode, setAccountCode] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [note, setNote] = useState("");
  const [ask, setAsk] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  useEffect(() => {
    if (!loaded.data) return;
    setKind(loaded.data.template.kind);
    setPreset(loaded.data.template.preset);
    setStopOn(loaded.data.template.stop_enabled !== false);
    setTakeOn(loaded.data.template.take_profit_enabled !== false);
    setTrailingOn(loaded.data.template.trailing_enabled === true);
    setMinScore(loaded.data.template.min_score || 80);
    setWeights(
      loaded.data.template.weights || { trend: 25, reversal: 25, liquidity: 20, structure: 20, event: 10 },
    );
    setAccountCode(loaded.data.template.account_code || "");
    setPreview(loaded.data.preview);
  }, [loaded.data]);
  function templateBody(nextKind = kind, nextPreset = preset) {
    return {
      kind: nextKind,
      preset: nextPreset,
      market: "spot",
      stop_enabled: stopOn,
      take_profit_enabled: takeOn,
      trailing_enabled: trailingOn,
      min_score: minScore,
      weights,
      account_code: accountCode,
    };
  }
  async function refresh(nextKind: string, nextPreset: string) {
    if (previewBusy) return;
    setPreviewBusy(true);
    try {
      const row = await api<{ preview: Preview }>("/api/bots/preview", {
        method: "POST",
        body: JSON.stringify(templateBody(nextKind, nextPreset)),
      });
      setKind(nextKind);
      setPreset(nextPreset);
      setPreview(row.preview);
      setAsk(false);
      setNote("");
    } finally {
      setPreviewBusy(false);
    }
  }
  async function save() {
    if (preset === "aggressive" && !ask) {
      setAsk(true);
      return;
    }
    const weightSum = Object.values(weights).reduce((sum, value) => sum + Number(value), 0);
    if (weightSum !== 100 || minScore < 50 || minScore > 100) {
      setNote(t(lang, "weightsSum"));
      return;
    }
    await api("/api/bots/template", {
      method: "POST",
      body: JSON.stringify(templateBody()),
    });
    setAsk(false);
    setNote(t(lang, "saved"));
  }
  if (loaded.error) return <Problem lang={lang} retry={loaded.reload} />;
  if (!preview) return <Loading lang={lang} />;
  const weightSum = Object.values(weights).reduce((sum, value) => sum + Number(value), 0);
  const na = t(lang, "unknown");
  const weightLabel = (key: keyof typeof weights) =>
    t(
      lang,
      key === "trend"
        ? "weightTrend"
        : key === "reversal"
          ? "weightReversal"
          : key === "liquidity"
            ? "weightLiquidity"
            : key === "structure"
              ? "weightStructure"
              : "weightEvent",
    );
  return (
    <div className="form-grid">
      <Card>
        <CardHead icon={FileSliders} title={t(lang, "template")} help={t(lang, "places")} />
        <fieldset className="form-section">
          <legend>{t(lang, "exits")}</legend>
          <div className="choice-grid">
            {(
              [
                ["stop", stopOn, setStopOn, "stopToggle"],
                ["take", takeOn, setTakeOn, "takeToggle"],
                ["trail", trailingOn, setTrailingOn, "trailToggle"],
              ] as const
            ).map(([key, value, setter, label]) => (
              <Choice
                key={key}
                pressed={value}
                title={t(lang, label)}
                state={<Badge tone={value ? "good" : "neutral"}>{value ? t(lang, "on") : t(lang, "off")}</Badge>}
                onClick={() => setter(!value)}
              />
            ))}
          </div>
          {!stopOn || (!takeOn && !trailingOn) ? (
            <div className="badge-row">
              {!stopOn ? <Badge tone="warn">{t(lang, "noSl")}</Badge> : null}
              {!takeOn && !trailingOn ? <Badge tone="warn">{t(lang, "manualExit")}</Badge> : null}
            </div>
          ) : null}
        </fieldset>
        <fieldset className="form-section">
          <legend>{t(lang, "scoring")}</legend>
          <Field label={t(lang, "minScore")} hint="50–100">
            <input
              className={inputClass}
              inputMode="numeric"
              value={minScore}
              onChange={(event) => setMinScore(Number(event.target.value))}
            />
          </Field>
          {minScore < 80 ? <Badge tone="warn">{t(lang, "scoreWarning")}</Badge> : null}
          <div className="field-pair">
            {(Object.keys(weights) as (keyof typeof weights)[]).map((key) => (
              <Field key={key} label={weightLabel(key)}>
                <input
                  className={inputClass}
                  inputMode="numeric"
                  value={weights[key]}
                  onChange={(event) => setWeights({ ...weights, [key]: Number(event.target.value) })}
                />
              </Field>
            ))}
          </div>
          <Note tone={weightSum === 100 ? "neutral" : "warn"}>
            {t(lang, "weightsSum")} {t(lang, "currentSum")} <b>{weightSum}</b>
          </Note>
        </fieldset>
        <fieldset className="form-section">
          <legend>{t(lang, "botType")}</legend>
          <div className="choice-grid">
            {(["dca", "grid"] as const).map((item) => (
              <Choice
                key={item}
                pressed={kind === item}
                disabled={previewBusy}
                title={t(lang, item)}
                detail={t(lang, item === "dca" ? "dcaHelp" : "gridHelp")}
                onClick={() => void refresh(item, preset).catch(() => setNote(t(lang, "error")))}
              />
            ))}
          </div>
        </fieldset>
        <fieldset className="form-section">
          <legend>{t(lang, "riskPreset")}</legend>
          <div className="choice-grid choice-grid-3">
            {(["conservative", "moderate", "aggressive"] as const).map((item) => (
              <Choice
                key={item}
                pressed={preset === item}
                disabled={previewBusy}
                title={t(lang, item)}
                detail={t(lang, helpKey(item))}
                onClick={() => void refresh(kind, item).catch(() => setNote(t(lang, "error")))}
              />
            ))}
          </div>
        </fieldset>
      </Card>
      <Card className="sticky-card">
        <CardHead icon={ShieldCheck} title={t(lang, "preview")} help={t(lang, "capital")} />
        <p className="big-figure">
          {fmtMoney(preview.full_commitment, lang) == null ? (
            <span className="kpi-missing">{t(lang, "unavailable")}</span>
          ) : (
            <>
              {fmtMoney(preview.full_commitment, lang)} <small>USDT</small>
            </>
          )}
        </p>
        <KV
          rows={[
            [
              t(lang, "share"),
              fmtPct01(preview.share_of_equity, lang) ?? na,
              preview.equity_is_live
                ? undefined
                : `${t(lang, "equityReference")} ${fmtMoney(preview.equity_reference, lang) ?? na} USDT`,
            ],
            [t(lang, "ordersOrGrids"), fmtNumber(preview.orders_or_grids, lang, 0) ?? na],
            [t(lang, "stop"), fmtPct01(preview.stop, lang, 2) ?? na],
            [t(lang, "takeProfitShort"), fmtPct01(preview.take_profit, lang, 2) ?? na],
          ]}
        />
        <Note tone={preview.fits_caps ? "good" : "warn"}>{preview.fits_caps ? t(lang, "fits") : t(lang, "tight")}</Note>
        <Button
          className="w-full"
          type="button"
          disabled={previewBusy}
          onClick={() => void save().catch(() => setNote(t(lang, "error")))}
        >
          {t(lang, "save")}
        </Button>
        {ask ? <Note tone="warn">{t(lang, "aggressiveConfirm")}</Note> : null}
        {note ? <Note>{note}</Note> : null}
      </Card>
    </div>
  );
}

function Positions({ lang }: { lang: Lang }) {
  const loaded = useLoad<{ positions: Position[]; as_of?: string | null; error?: string | null }>("/api/positions", true);
  const fresh = useCurrentRead(loaded.data?.as_of, 300);
  if (loaded.error) return <Problem lang={lang} retry={loaded.reload} />;
  if (!loaded.data) return <Loading lang={lang} />;
  const reported = Array.isArray(loaded.data.positions);
  const complete = reported && fresh && !loaded.data.error && !loaded.refreshFailed;
  const head = (
    <Card>
      <CardHead
        icon={Boxes}
        title={t(lang, "positions")}
        help={t(lang, "listPositions")}
        aside={<Age at={loaded.data.error ? null : loaded.data.as_of} limit={300} lang={lang} reason={loaded.refreshFailed ? t(lang, "error") : loaded.data.error} />}
      />
    </Card>
  );
  if (!reported || !loaded.data.positions.length)
    return (
      <div className="stack">
        {head}
        <Empty icon={Boxes} text={t(lang, complete ? "noPositions" : "unavailable")} />
      </div>
    );
  const na = t(lang, "unknown");
  return (
    <div className="stack">
      {head}
      <div className="card-grid">
        {loaded.data.positions.map((row) => (
          <Card key={row.client_id || row.strategy_id || row.symbol || "row"}>
            <CardHead
              as="h3"
              title={row.symbol || na}
              aside={<Badge tone={row.paper ? "warn" : "neutral"}>{row.status ? row.status.replaceAll("_", " ") : na}</Badge>}
            />
            <KV
              rows={[
                [t(lang, "account"), row.paper == null ? na : t(lang, row.paper ? "demo" : "real")],
                [t(lang, "strategyId"), <code className="mono-break">{row.strategy_id || na}</code>],
              ]}
            />
          </Card>
        ))}
      </div>
    </div>
  );
}

function LiveBadge({ lang }: { lang: Lang }) {
  const on = useLiveState();
  return (
    <span className="live-badge" title={t(lang, on ? "streamOn" : "reconnecting")}>
      <Badge tone={on ? "good" : "warn"}>
        <span className="live-text">{t(lang, on ? "streamOn" : "reconnecting")}</span>
      </Badge>
    </span>
  );
}

function ScreenerPage({ lang, marketFresh }: { lang: Lang; marketFresh?: boolean }) {
  const loaded = useLoad<{
    filters: Filters;
    hits: Hit[];
    error: string | null;
    source: string;
    fetched_at?: string | null;
  }>("/api/screener", true);
  const [draft, setForm] = useState<Filters | null>(null);
  const form = draft ?? loaded.data?.filters;
  const [note, setNote] = useState("");

  if (loaded.error) return <Problem lang={lang} retry={loaded.reload} />;
  if (!loaded.data || !form) return <Loading lang={lang} />;
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    await api("/api/screener/filters", { method: "POST", body: JSON.stringify(form) });
    setNote(t(lang, "saved"));
  }
  const result = loaded.data;
  return (
    <div className="monitor-col">
      <Card>
        <CardHead
          icon={ListFilter}
          title={t(lang, "candidates")}
          help={<Age at={result.fetched_at} limit={1800} lang={lang} reason={result.error} />}
          aside={<Badge tone="info">{sourceLabel(result.source) || t(lang, "unavailable")}</Badge>}
        />
        {result.error && (
          <p role="alert" className="alert">
            {result.error}
          </p>
        )}
        {marketFresh === false && <Note tone="warn">{t(lang, "stale")}</Note>}
        {result.hits.length ? (
          <div className="table-scroll" tabIndex={0} role="region" aria-label={t(lang, "candidates")}>
            <table className="data-table screener-table">
              <caption className="sr-only">{t(lang, "candidates")}</caption>
              <thead>
                <tr>
                  {[
                    t(lang, "rank"),
                    t(lang, "symbols"),
                    t(lang, "price"),
                    t(lang, "momentum"),
                    t(lang, "volume") + " (USDT)",
                    "RSI",
                    t(lang, "recommendation"),
                  ].map((label) => (
                    <th key={label} scope="col">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.hits.map((hit, index) => {
                  const na = t(lang, "unavailable");
                  const change = fmtPct(hit.change_pct, lang);
                  const rating = ratingLabel(hit.recommendation, lang);
                  return (
                    <tr key={hit.symbol}>
                      <td className="num-muted cell-rank">{index + 1}</td>
                      <th scope="row">{hit.symbol}</th>
                      <td data-label={t(lang, "price")}>{fmtPrice(hit.last, lang) ?? na}</td>
                      <td data-label={t(lang, "momentum")} className={change == null ? "na" : Number(hit.change_pct) > 0 ? "up" : Number(hit.change_pct) < 0 ? "down" : undefined}>
                        {change ?? na}
                      </td>
                      <td data-label={t(lang, "volume") + " (USDT)"}>{fmtCompact(hit.quote_volume, lang) ?? na}</td>
                      <td data-label="RSI">{fmtNumber(hit.rsi, lang, 1) ?? na}</td>
                      <td data-label={t(lang, "recommendation")}>
                        {rating ? (
                          <span className={`rating ${rating.tone}`} title={fmtNumber(rating.value, lang, 2) ?? undefined}>
                            {rating.text} <small>{fmtNumber(rating.value, lang, 2)}</small>
                          </span>
                        ) : (
                          na
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          !result.error && <p className="empty-line">{t(lang, "noHits")}</p>
        )}
      </Card>
      <Card>
        <CardHead icon={SlidersHorizontal} title={t(lang, "filters")} help={t(lang, "filtersHelp")} />
        <form
          className="form-stack"
          onSubmit={(event) => void save(event).catch(() => setNote(t(lang, "error")))}
        >
          <Field label={t(lang, "minVolume")} hint={fmtCompact(form.min_quote_volume, lang) ? `≈ ${fmtCompact(form.min_quote_volume, lang)} USDT` : undefined}>
            <input
              className={inputClass}
              value={form.min_quote_volume}
              onChange={(event) => setForm({ ...form, min_quote_volume: event.target.value })}
            />
          </Field>
          <div className="field-pair">
            <Field label={t(lang, "minChange")}>
              <input
                className={inputClass}
                value={form.min_change_pct}
                onChange={(event) => setForm({ ...form, min_change_pct: event.target.value })}
              />
            </Field>
            <Field label={t(lang, "maxChange")}>
              <input
                className={inputClass}
                value={form.max_change_pct}
                onChange={(event) => setForm({ ...form, max_change_pct: event.target.value })}
              />
            </Field>
          </div>
          <div className="field-pair">
            <Field label={t(lang, "rsi")}>
              <input
                className={inputClass}
                value={form.rsi_min}
                onChange={(event) => setForm({ ...form, rsi_min: event.target.value })}
              />
            </Field>
            <Field label={t(lang, "rsiMax")}>
              <input
                className={inputClass}
                value={form.rsi_max}
                onChange={(event) => setForm({ ...form, rsi_max: event.target.value })}
              />
            </Field>
          </div>
          <Field label={t(lang, "recommend")} hint={t(lang, "recommendHint")}>
            <input
              className={inputClass}
              value={form.min_recommendation}
              onChange={(event) => setForm({ ...form, min_recommendation: event.target.value })}
            />
          </Field>
          <Field label={t(lang, "limit")}>
            <input
              className={inputClass}
              value={form.limit}
              onChange={(event) => setForm({ ...form, limit: Number(event.target.value) })}
            />
          </Field>
          <Button type="submit">{t(lang, "apply")}</Button>
          {note ? <Note>{note}</Note> : null}
        </form>
      </Card>
    </div>
  );
}

function Settings({ lang, paper }: { lang: Lang; paper: boolean }) {
  const loaded = useLoad<Record<string, string | number | boolean | string[]>>("/api/config", true);
  if (loaded.error) return <Problem lang={lang} retry={loaded.reload} />;
  if (!loaded.data) return <Loading lang={lang} />;
  const rows = Object.entries(loaded.data);
  return (
    <div className="stack">
      <PaperCheck lang={lang} enabled={paper} />
      <Card>
        <CardHead
          icon={Settings2}
          title={t(lang, "settings")}
          help={t(lang, "modeHelp")}
          aside={<ReceivedAge at={loaded.received} limit={90} lang={lang} />}
        />
        <dl className="kv kv-settings">
          {rows.map(([key, value]) => (
            <div key={key}>
              <dt>
                {settingLabel(key, lang)}
                <small>{key}</small>
              </dt>
              <dd>{settingValue(key, value, lang, t(lang, "unknown"))}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </div>
  );
}
