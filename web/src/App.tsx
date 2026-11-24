import { DiscoveryPanel, Opportunities, ResearchPanel } from "./tradingdesk/opportunities";
import { takeRequestedTab, type PreferencesTab } from "./tradingdesk/activity-model";
import { IncidentStrip } from "./tradingdesk/IncidentStrip";
import type { ReadInput } from "./tradingdesk/types";
import { useCurrentRead } from "./account-read";
import {
  Bot,
  Boxes,
  Cpu,
  FileSliders,
  Settings2,
  ShieldCheck,
  Shapes,
  Clock3,
  RefreshCw,
  SlidersHorizontal,
} from "lucide-react";
import { FormEvent, Suspense, lazy, useEffect, useRef, useState } from "react";
import {
  api,
  AuthError,
  type Filters,
  type Hit,
  type Position,
  type Preview,
  type Status,
} from "./api";
import { Key, Lang, t } from "./i18n";
import { AccountsPanel, BudgetForm, ForwardPanel, PaperCheck, RulesPage, WarpBotsPanel } from "./desk";
import { Badge, Button, Card, CardHead, Choice, Empty, Field, KV, Loading, Note, Problem, inputClass } from "./ui";
import { DeskPage, Overview, Providers } from "./monitor";
import { Age, ReceivedAge } from "./age";
import { settingLabel, settingValue } from "./labels";
import { zoneLabel, fmtCompact, fmtMoney, fmtPct01, fmtNumber, fmtTime } from "./format";
import { stopLive } from "./live";
import { useLoad } from "./snapshot";
import { Brand, CommandBar, pageFromHash, TradingDeskNav, type Page } from "./tradingdesk/Shell";

export function App() {
  const [lang, setLang] = useState<Lang>(() => (localStorage.getItem("warp.lang") === "id" ? "id" : "en"));
  const [authed, setAuthed] = useState(false);
  const [expired, setExpired] = useState(false);
  const session = useLoad<{ authenticated: boolean }>("/api/session");
  useEffect(() => {
    setAuthed(session.data?.authenticated === true);
  }, [session.data]);
  useEffect(() => {
    function expire() {
      stopLive();
      setExpired(true);
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
        {expired && <p role="alert">{t(lang, "sessionExpired")}</p>}
        {session.error ? (
          <Problem lang={lang} retry={session.reload} />
        ) : !session.data ? (
          <Loading lang={lang} />
        ) : (
          <Login
            lang={lang}
            onSuccess={() => {
              setExpired(false);
              if (!location.hash) location.hash = "overview";
              setAuthed(true);
            }}
          />
        )}
      </main>
    );
  return (
    <TradingDeskLayout
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

function TradingDeskLayout({
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
      <TradingDeskNav lang={lang} page={page} placement="rail" />
      <div className="workarea">
        <CommandBar lang={lang} page={page} row={status.issue ? null : row} halted={status.data?.halt === true} error={status.refreshFailed}
          language={language} onLogout={() => void logout().catch(() => setLogoutError(true))} />
        <main id="workspace" className={page === "desk" ? "workspace desk-workspace" : "workspace"}>
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
          {page === "overview" && <IncidentStrip lang={lang} status={status} />}
          {page === "overview" && <Overview lang={lang} status={status} />}
          {page === "desk" && <DeskPage lang={lang} status={status} />}
          {page === "signals" && (
            <div className="monitor-col">
              <Opportunities lang={lang} status={status} />
              <ForwardPanel lang={lang} />
            </div>
          )}
          {page === "assets" && <AssetDesk lang={lang} status={row} />}
          {page === "screener" && <ScreenerPage lang={lang} status={status} />}
          {page === "preferences" && <Preferences lang={lang} status={row} read={status} />}
        </main>
      </div>
      <TradingDeskNav lang={lang} page={page} placement="bottom" />
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
const ActivityPanel = lazy(() => import("./tradingdesk/Activity").then((m) => ({ default: m.ActivityPanel })));
const RiskHealthPanel = lazy(() => import("./tradingdesk/Activity").then((m) => ({ default: m.RiskHealthPanel })));

function Preferences({ lang, status, read }: { lang: Lang; status: Status | null; read: ReturnType<typeof useLoad<Status>> }) {
  const [tab, setTab] = useState<PreferencesTab>(() => takeRequestedTab() ?? "venues");
  const [selected, setSelected] = useState("");
  return (
    <>
      <Sections
        lang={lang}
        items={["venues", "rules", "configuration", "activity", "risk"] as const}
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
      ) : tab === "activity" || tab === "risk" ? (
        <Suspense fallback={<Loading lang={lang} />}>
          {tab === "activity" ? <ActivityPanel lang={lang} status={read} /> : <RiskHealthPanel lang={lang} status={read} />}
        </Suspense>
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

function ScreenerPage({ lang, status }: { lang: Lang; status: ReadInput }) {
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

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    await api("/api/screener/filters", { method: "POST", body: JSON.stringify(form) });
    setNote(t(lang, "saved"));
  }
  return (
    <div className="monitor-col">
      <DiscoveryPanel lang={lang} status={status} />
      <ResearchPanel lang={lang} status={status} feed={loaded} />
      {loaded.error ? <Problem lang={lang} retry={loaded.reload} /> : !form ? <Loading lang={lang} /> : <Card>
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
      </Card>}
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
          aside={<ReceivedAge at={loaded.received} lang={lang} />}
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
