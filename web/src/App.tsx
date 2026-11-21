import {
  Activity,
  ArrowUpRight,
  Bot,
  ChartNoAxesCombined,
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
import { Badge, Button, Card, Field, Loading, Problem, inputClass } from "./ui";
import { Overview, Providers } from "./monitor";
import { Age } from "./age";
import { useLiveState, useLoad } from "./snapshot";

const nav = [
  { id: "overview", short: "overview", icon: LayoutDashboard },
  { id: "signals", short: "signals", icon: Crosshair },
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
        <time dateTime={publishedAt ?? undefined}>
          {new Date(parsed).toLocaleTimeString(lang, { hour12: false, timeZone: "UTC" })} UTC
        </time>
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
      <span className="mark">
        <ChartNoAxesCombined size={24} />
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
    <div role="group" aria-label={t(lang, "language")} className="lang-switch">
      {(["en", "id"] as const).map((item) => (
        <button key={item} aria-pressed={lang === item} onClick={() => setLang(item)}>
          {item.toUpperCase()}
        </button>
      ))}
    </div>
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
    <Card>
      <form className="space-y-4" onSubmit={submit}>
        <h1 className="text-xl font-semibold">{t(lang, "login")}</h1>
        <p className="text-sm text-zinc-400">{t(lang, "loginHelp")}</p>
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
          <p role="alert" className="text-sm text-red-300">
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
            {status.data?.halt && <Badge tone="bad">HALT</Badge>}
            <Badge tone={row?.halt || row?.mode === "live" ? "bad" : row?.mode === "paper" ? "warn" : "info"}>
              {row?.mode?.toUpperCase() ?? "—"}
            </Badge>
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
        <main id="workspace" className="workspace">
          {(status.data?.halt || status.error || logoutError) && (
            <div className="alert" role="alert">
              {status.data?.halt && <strong>HALT · {t(lang, "haltHelp")} </strong>}
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
              <h1 tabIndex={-1} ref={heading}>
                {title}
              </h1>
            </div>
            <div className="snapshot-clock">
              <Clock3 size={14} />
              <div className="snapshot-facts">
                <span>
                  {t(lang, "received")}{" "}
                  <time>{status.received?.toLocaleTimeString(lang, { hour12: false }) ?? "—"}</time>
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
            <Providers lang={lang} data={status} />
          </Card>
          <Settings lang={lang} paper={status?.mode === "paper" && status.halt === false} />
        </div>
      )}
    </>
  );
}
function Empty({ text }: { lang: Lang; text: string }) {
  return (
    <Card className="empty">
      <Crosshair size={22} />
      <p>{text}</p>
    </Card>
  );
}
function pretty(value: string | null, digits = 8) {
  return value == null || value === ""
    ? "—"
    : new Intl.NumberFormat("en", { maximumFractionDigits: digits }).format(Number(value));
}
function compact(value: string | null) {
  return value == null
    ? "—"
    : new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(Number(value));
}
function Signals({ lang }: { lang: Lang }) {
  const loaded = useLoad<{ cards: CardRow[] }>("/api/opportunities", true);
  if (loaded.error) return <Problem lang={lang} retry={loaded.reload} />;
  if (!loaded.data) return <Loading lang={lang} />;
  if (!loaded.data.cards.length) return <Empty lang={lang} text={t(lang, "noSignals")} />;
  return (
    <div className="opportunity-grid">
      {loaded.data.cards.map((card, index) => (
        <Card key={card.symbol}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">
                <small className="rank">#{index + 1}</small>
                {card.symbol}
              </h2>
              <p className="text-xs text-zinc-500">
                {card.regime ?? "—"} · RSI {card.rsi ?? "—"}
              </p>
            </div>
            <div className="flex flex-col items-end gap-1">
              <Badge tone={card.action === "BUY" ? "good" : card.action === "SKIP" ? "bad" : "warn"}>
                {card.action ?? "—"}
              </Badge>
              {card.fresh === false ? <Badge tone="warn">{t(lang, "staleEvidence")}</Badge> : null}
            </div>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-sm">
            <Metric label={t(lang, "entry")} value={card.entry} />
            <Metric label={t(lang, "stop")} value={card.stop} />
            <Metric label={t(lang, "target")} value={card.take} />
          </div>
          {card.score ? (
            <p className="mt-3 text-sm text-zinc-300">
              {t(lang, "score")} {card.score.total}
              {card.score_warning ? ` · ${t(lang, "scoreWarning")}` : ""}
              <span className="mt-1 block text-xs text-zinc-500">
                {card.score.trend}/{card.score.reversal}/{card.score.liquidity}/{card.score.structure}/
                {card.score.event}
              </span>
            </p>
          ) : null}
          <p className="mt-3 text-sm text-zinc-300">
            {t(lang, "confidence")} {card.confidence ?? "—"}
            {card.model_action && card.model_action !== card.action
              ? ` · ${t(lang, "model")} ${card.model_action}`
              : ""}
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            {t(lang, "backtest")}:{" "}
            {card.backtest_passed == null
              ? "—"
              : card.backtest_passed
                ? t(lang, "passed")
                : t(lang, "failed")}{" "}
            {card.backtest_reason && `(${card.backtest_reason})`}
          </p>
          <div className="metadata">
            <span>
              {t(lang, "source")} <b>{card.source || "—"}</b>
            </span>
            <span>
              {t(lang, "exchange")}{" "}
              <b>
                {t(lang, "unavailable")} · {t(lang, "notInSnapshot")}
              </b>
            </span>
            <span>
              {t(lang, "riskScore")}{" "}
              <b>
                {t(lang, "unavailable")} · {t(lang, "notInSnapshot")}
              </b>
            </span>
            <span>
              {t(lang, "signalTime")} <time>{card.as_of ?? "—"}</time>
            </span>
            <span>
              {t(lang, "marketTime")} <time>{card.evidence_as_of ?? "—"}</time>
            </span>
          </div>
          <details>
            <summary>{t(lang, "why")}</summary>
            <p>{lang === "id" ? card.why_id : card.why}</p>
          </details>
          {/^[A-Z0-9]+USDT$/.test(card.symbol) && (
            <a
              className="chart-link"
              href={`https://www.tradingview.com/chart/?symbol=BINANCE%3A${encodeURIComponent(card.symbol)}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              {t(lang, "chart")} · TradingView <ArrowUpRight size={14} />
            </a>
          )}
        </Card>
      ))}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="rounded-xl bg-black/30 p-2">
      <p className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="font-medium">{value ?? "—"}</p>
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
  return (
    <div className="form-grid">
      <Card>
        <h2 className="font-semibold">{t(lang, "bots")}</h2>
        <p className="mt-1 text-xs text-zinc-500">{t(lang, "places")}</p>
        <div className="mt-4 grid grid-cols-2 gap-2">
          {(
            [
              ["stop", stopOn, setStopOn, "stopToggle"],
              ["take", takeOn, setTakeOn, "takeToggle"],
              ["trail", trailingOn, setTrailingOn, "trailToggle"],
            ] as const
          ).map(([key, value, setter, label]) => (
            <button
              key={key}
              className={`rounded-xl border p-3 text-left ${value ? "border-emerald-400" : "border-amber-400"}`}
              aria-pressed={value}
              onClick={() => setter(!value)}
              type="button"
            >
              <p className="font-medium">{t(lang, label)}</p>
              <p className="text-xs text-zinc-500">{value ? t(lang, "on") : t(lang, "off")}</p>
            </button>
          ))}
        </div>
        {!stopOn ? (
          <p className="mt-3">
            <Badge tone="warn">{t(lang, "noSl")}</Badge>
          </p>
        ) : null}
        {!takeOn && !trailingOn ? (
          <p className="mt-3">
            <Badge tone="warn">{t(lang, "manualExit")}</Badge>
          </p>
        ) : null}
        <div className="mt-4">
          <Field label={t(lang, "minScore")} hint="50–100">
            <input
              className={inputClass}
              value={minScore}
              onChange={(event) => setMinScore(Number(event.target.value))}
            />
          </Field>
          {minScore < 80 ? (
            <p className="mt-2">
              <Badge tone="warn">{t(lang, "scoreWarning")}</Badge>
            </p>
          ) : null}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2">
          {(Object.keys(weights) as (keyof typeof weights)[]).map((key) => (
            <Field
              key={key}
              label={t(
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
              )}
            >
              <input
                className={inputClass}
                value={weights[key]}
                onChange={(event) => setWeights({ ...weights, [key]: Number(event.target.value) })}
              />
            </Field>
          ))}
        </div>
        <p className={`mt-2 text-xs ${weightSum === 100 ? "text-zinc-500" : "text-amber-200"}`}>
          {t(lang, "weightsSum")} {weightSum}
        </p>
        <div className="mt-4 grid grid-cols-2 gap-2">
          {(["dca", "grid"] as const).map((item) => (
            <button
              key={item}
              className={`rounded-xl border p-3 text-left ${kind === item ? "border-emerald-400" : "border-white/10"}`}
              disabled={previewBusy}
              aria-pressed={kind === item}
              onClick={() => void refresh(item, preset).catch(() => setNote(t(lang, "error")))}
              type="button"
              title={t(lang, item === "dca" ? "dcaHelp" : "gridHelp")}
            >
              <p className="font-medium">{t(lang, item)}</p>
              <p className="text-xs text-zinc-500">{t(lang, item === "dca" ? "dcaHelp" : "gridHelp")}</p>
            </button>
          ))}
        </div>
        <div className="mt-3 grid gap-2">
          {(["conservative", "moderate", "aggressive"] as const).map((item) => (
            <button
              key={item}
              className={`rounded-xl border p-3 text-left ${preset === item ? "border-emerald-400" : "border-white/10"}`}
              disabled={previewBusy}
              aria-pressed={preset === item}
              onClick={() => void refresh(kind, item).catch(() => setNote(t(lang, "error")))}
              type="button"
              title={t(lang, helpKey(item))}
            >
              <p className="font-medium">{t(lang, item)}</p>
              <p className="text-xs text-zinc-500">{t(lang, helpKey(item))}</p>
            </button>
          ))}
        </div>
      </Card>
      <Card>
        <h2 className="font-semibold">{t(lang, "preview")}</h2>
        <p className="mt-2 text-sm text-zinc-400">{t(lang, "capital")}</p>
        <p className="text-2xl font-semibold">{pretty(preview.full_commitment)} USDT</p>
        <p className="mt-2 text-sm text-zinc-300">
          {t(lang, "share")} {(Number(preview.share_of_equity) * 100).toFixed(1)}%
          {preview.equity_is_live ? "" : ` · ${preview.equity_reference}`}
        </p>
        <p className="mt-1 text-xs text-zinc-500">
          {preview.orders_or_grids} · SL {preview.stop} · TP {preview.take_profit}
        </p>
        <p className={`mt-3 text-sm ${preview.fits_caps ? "text-emerald-300" : "text-amber-200"}`}>
          {preview.fits_caps ? t(lang, "fits") : t(lang, "tight")}
        </p>
        <Button
          className="mt-4 w-full"
          type="button"
          disabled={previewBusy}
          onClick={() => void save().catch(() => setNote(t(lang, "error")))}
        >
          {t(lang, "save")}
        </Button>
        {ask ? <p className="mt-3 text-sm text-amber-200">{t(lang, "aggressiveConfirm")}</p> : null}
        {note ? <p className="mt-3 text-sm text-zinc-400">{note}</p> : null}
      </Card>
    </div>
  );
}

function Positions({ lang }: { lang: Lang }) {
  const loaded = useLoad<{ positions: Position[] }>("/api/positions", true);
  if (loaded.error) return <Problem lang={lang} retry={loaded.reload} />;
  if (!loaded.data) return <Loading lang={lang} />;
  if (!loaded.data.positions.length) return <Empty lang={lang} text={t(lang, "noPositions")} />;
  return (
    <div className="space-y-3">
      {loaded.data.positions.map((row) => (
        <Card key={row.client_id || row.strategy_id || row.symbol || "row"}>
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">{row.symbol || "—"}</h2>
            <Badge tone={row.paper ? "warn" : "neutral"}>{row.status || "—"}</Badge>
          </div>
          <p className="panel-note">{row.paper == null ? "—" : t(lang, row.paper ? "demo" : "real")}</p>
          <p className="mt-2 break-all text-xs text-zinc-500">{row.strategy_id}</p>
        </Card>
      ))}
    </div>
  );
}

function LiveBadge({ lang }: { lang: Lang }) {
  const on = useLiveState();
  return <Badge tone={on ? "good" : "warn"}>{t(lang, on ? "streamOn" : "reconnecting")}</Badge>;
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
        <div className="panel-heading">
          <h2 className="panel-title">
            <ListFilter />
            {t(lang, "candidates")}
          </h2>
          <Badge>{result.source || t(lang, "unavailable")}</Badge>
        </div>
        <p className="panel-note">
          {t(lang, "fetched")} <Age at={result.fetched_at} limit={60} lang={lang} reason={result.error} />
        </p>
        {result.error && (
          <p role="alert" className="alert">
            {result.error}
          </p>
        )}
        {marketFresh === false && <p className="panel-note text-amber-200">{t(lang, "stale")}</p>}
        {result.hits.length ? (
          <div className="table-scroll" tabIndex={0} role="region" aria-label={t(lang, "candidates")}>
            <table>
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
                {result.hits.map((hit, index) => (
                  <tr key={hit.symbol}>
                    <td>{index + 1}</td>
                    <th scope="row">{hit.symbol}</th>
                    <td>{pretty(hit.last)}</td>
                    <td>{hit.change_pct == null ? t(lang, "unavailable") : pretty(hit.change_pct) + "%"}</td>
                    <td>{hit.quote_volume == null ? t(lang, "unavailable") : compact(hit.quote_volume)}</td>
                    <td>{hit.rsi == null ? t(lang, "unavailable") : pretty(hit.rsi)}</td>
                    <td>{hit.recommendation == null ? t(lang, "unavailable") : pretty(hit.recommendation)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          !result.error && <p className="panel-note">{t(lang, "noHits")}</p>
        )}
      </Card>
      <Card>
        <h2 className="panel-title">
          <SlidersHorizontal />
          {t(lang, "filters")}
        </h2>
        <form
          className="mt-4 space-y-3"
          onSubmit={(event) => void save(event).catch(() => setNote(t(lang, "error")))}
        >
          <Field label={t(lang, "minVolume")}>
            <input
              className={inputClass}
              value={form.min_quote_volume}
              onChange={(event) => setForm({ ...form, min_quote_volume: event.target.value })}
            />
          </Field>
          <div className="grid grid-cols-2 gap-2">
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
          <Field label={t(lang, "limit")}>
            <input
              className={inputClass}
              value={form.limit}
              onChange={(event) => setForm({ ...form, limit: Number(event.target.value) })}
            />
          </Field>
          <Button type="submit">{t(lang, "apply")}</Button>
          {note ? <p className="text-sm text-zinc-400">{note}</p> : null}
        </form>
      </Card>
    </div>
  );
}

function Settings({ lang, paper }: { lang: Lang; paper: boolean }) {
  const loaded = useLoad<Record<string, string | number | boolean | string[]>>("/api/config");
  if (loaded.error) return <Problem lang={lang} retry={loaded.reload} />;
  if (!loaded.data) return <Loading lang={lang} />;
  const rows = Object.entries(loaded.data);
  return (
    <div className="space-y-4">
      <PaperCheck lang={lang} enabled={paper} />
      <Card>
        <h2 className="font-semibold">{t(lang, "settings")}</h2>
        <p className="mt-1 text-sm text-zinc-400">{t(lang, "modeHelp")}</p>
        <dl className="mt-4 space-y-2">
          {rows.map(([key, value]) => (
            <div
              key={key}
              className="flex items-start justify-between gap-3 border-b border-white/5 py-2 text-sm"
            >
              <dt className="text-zinc-500">{key}</dt>
              <dd className="max-w-[60%] break-all text-right">
                {Array.isArray(value) ? value.join(", ") : String(value)}
              </dd>
            </div>
          ))}
        </dl>
      </Card>
    </div>
  );
}
