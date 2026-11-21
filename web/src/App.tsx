import {
  Activity,
  Bot,
  LayoutDashboard,
  ListFilter,
  LogOut,
  Shield,
  SlidersHorizontal,
  Wallet,
} from "lucide-react";
import { FormEvent, ReactNode, useEffect, useState } from "react";
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
import { Badge, Button, Card, Field, inputClass } from "./ui";

type Page = "overview" | "signals" | "bots" | "positions" | "screener" | "rules" | "settings";

const nav: { id: Page; icon: typeof Activity }[] = [
  { id: "overview", icon: LayoutDashboard },
  { id: "signals", icon: Activity },
  { id: "bots", icon: Bot },
  { id: "positions", icon: Wallet },
  { id: "screener", icon: ListFilter },
  { id: "rules", icon: Shield },
];

function useLang(): [Lang, (lang: Lang) => void] {
  const stored = localStorage.getItem("warp.lang");
  const initial: Lang = stored === "id" ? "id" : "en";
  const [lang, setLang] = useState<Lang>(initial);
  return [
    lang,
    (next) => {
      localStorage.setItem("warp.lang", next);
      setLang(next);
    },
  ];
}

export function App() {
  const [lang, setLang] = useLang();
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [page, setPage] = useState<Page>("overview");

  useEffect(() => {
    api<{ authenticated: boolean }>("/api/session")
      .then((row) => setAuthed(row.authenticated))
      .catch(() => setAuthed(false));
  }, []);

  if (authed === null) {
    return <Shell lang={lang}>{t(lang, "loading")}</Shell>;
  }
  if (!authed) {
    return (
      <Shell lang={lang} onLang={setLang}>
        <Login
          lang={lang}
          onSuccess={() => {
            setAuthed(true);
            setPage("overview");
          }}
        />
      </Shell>
    );
  }
  return (
    <Shell lang={lang} onLang={setLang}>
      <Workspace
        lang={lang}
        page={page}
        onPage={setPage}
        onLogout={async () => {
          await api("/api/logout", { method: "POST" });
          setAuthed(false);
        }}
      />
    </Shell>
  );
}

function Shell({
  children,
  lang,
  onLang,
}: {
  children: ReactNode;
  lang: Lang;
  onLang?: (lang: Lang) => void;
}) {
  return (
    <div className="mx-auto min-h-screen max-w-3xl px-4 pb-28 pt-5">
      <header className="mb-5 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">{t(lang, "title")}</p>
          <h1 className="text-2xl font-semibold">{t(lang, "subtitle")}</h1>
        </div>
        {onLang ? (
          <div className="flex rounded-full border border-white/10 p-1 text-xs">
            {(["en", "id"] as Lang[]).map((item) => (
              <button
                key={item}
                className={`rounded-full px-2 py-1 ${lang === item ? "bg-white text-zinc-950" : "text-zinc-400"}`}
                onClick={() => onLang(item)}
                type="button"
              >
                {item.toUpperCase()}
              </button>
            ))}
          </div>
        ) : null}
      </header>
      {children}
    </div>
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
      else setError(t(lang, "badLogin"));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <form className="space-y-4" onSubmit={submit}>
        <h2 className="text-lg font-semibold">{t(lang, "login")}</h2>
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
        {error ? <p className="text-sm text-red-300">{error}</p> : null}
        <Button className="w-full" type="submit" disabled={busy}>
          {busy ? t(lang, "signingIn") : t(lang, "signIn")}
        </Button>
      </form>
    </Card>
  );
}

function Workspace({
  lang,
  page,
  onPage,
  onLogout,
}: {
  lang: Lang;
  page: Page;
  onPage: (page: Page) => void;
  onLogout: () => void;
}) {
  return (
    <>
      <div className="mb-4 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={() => onPage("settings")} type="button">
          <SlidersHorizontal className="mr-2 h-4 w-4" />
          {t(lang, "settings")}
        </Button>
        <Button variant="outline" size="sm" onClick={onLogout} type="button">
          <LogOut className="mr-2 h-4 w-4" />
          {t(lang, "logout")}
        </Button>
      </div>
      {page === "overview" ? <Overview lang={lang} /> : null}
      {page === "signals" ? (
        <div className="space-y-4">
          <ForwardPanel lang={lang} />
          <Signals lang={lang} />
        </div>
      ) : null}
      {page === "bots" ? <Bots lang={lang} /> : null}
      {page === "positions" ? <Positions lang={lang} /> : null}
      {page === "screener" ? <ScreenerPage lang={lang} /> : null}
      {page === "rules" ? <RulesPage lang={lang} /> : null}
      {page === "settings" ? <Settings lang={lang} /> : null}
      <nav className="fixed inset-x-0 bottom-0 border-t border-white/10 bg-ink/95 backdrop-blur">
        <div className="mx-auto grid max-w-3xl grid-cols-3">
          {nav.map((item) => {
            const Icon = item.icon;
            const active = page === item.id;
            return (
              <button
                key={item.id}
                className={`flex flex-col items-center gap-1 px-1 py-3 text-[11px] ${active ? "text-emerald-300" : "text-zinc-500"}`}
                onClick={() => onPage(item.id)}
                type="button"
              >
                <Icon className="h-5 w-5" />
                {t(lang, item.id)}
              </button>
            );
          })}
        </div>
      </nav>
    </>
  );
}

function useLoad<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    api<T>(path)
      .then((row) => {
        if (alive) {
          setData(row);
          setError("");
        }
      })
      .catch((err) => {
        if (!alive) return;
        if (err instanceof AuthError) window.location.reload();
        else setError("error");
      });
    return () => {
      alive = false;
    };
  }, [path, tick]);
  return { data, error, reload: () => setTick((value) => value + 1) };
}

function Empty({ lang, text }: { lang: Lang; text: string }) {
  return <Card><p className="text-sm text-zinc-400">{text || t(lang, "error")}</p></Card>;
}

function pretty(value: string | null, digits = 2): string {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return new Intl.NumberFormat("en", { maximumFractionDigits: digits }).format(number);
}

function compact(value: string | null): string {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(number);
}

function reasonText(reason: string, lang: Lang): string {
  if (reason === "observe_without_account") return lang === "id" ? "Observe, tanpa akun" : "Observe, no account";
  if (reason === "ok") return lang === "id" ? "Siap" : "Ready";
  return reason.replaceAll("_", " ");
}

function modeTone(mode: string): "info" | "warn" | "bad" {
  if (mode === "live") return "bad";
  if (mode === "paper") return "warn";
  return "info";
}

function Overview({ lang }: { lang: Lang }) {
  const status = useLoad<Status>("/api/status");
  const positions = useLoad<{ positions: Position[] }>("/api/positions");
  const [confirm, setConfirm] = useState<"halt" | "resume" | null>(null);
  const [word, setWord] = useState("");
  const [note, setNote] = useState("");
  if (status.error) return <Empty lang={lang} text={t(lang, "error")} />;
  if (!status.data) return <p className="text-sm text-zinc-400">{t(lang, "loading")}</p>;
  const row = status.data;
  const open = positions.data?.positions.length ?? 0;
  async function send() {
    const expected = confirm === "halt" ? "HALT" : "RESUME";
    if (word.trim().toUpperCase() !== expected) return;
    const path = confirm === "halt" ? "/api/pause" : "/api/resume";
    await api(path, { method: "POST" });
    setConfirm(null);
    setWord("");
    setNote(confirm === "halt" ? t(lang, "haltHelp") : t(lang, "confirmResume"));
    status.reload();
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={modeTone(row.mode)}>{row.mode === "live" ? t(lang, "live") : row.mode === "paper" ? t(lang, "paper") : t(lang, "observe")}</Badge>
        <Badge tone={row.halt ? "bad" : "neutral"}>{row.halt ? "HALT" : reasonText(row.reason, lang)}</Badge>
        <Badge tone={row.market_data_fresh ? "good" : "warn"}>{row.market_data_fresh ? t(lang, "fresh") : t(lang, "stale")}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Card>
          <p className="text-xs text-zinc-500">{t(lang, "equity")}</p>
          <p className="mt-1 text-xl font-semibold">{row.equity ?? t(lang, "noEquity")}</p>
        </Card>
        <Card>
          <p className="text-xs text-zinc-500">{t(lang, "pnl")}</p>
          <p className="mt-1 text-xl font-semibold">{row.pnl_today ?? "—"}</p>
        </Card>
        <Card>
          <p className="text-xs text-zinc-500">{t(lang, "open")}</p>
          <p className="mt-1 text-xl font-semibold">{open}</p>
        </Card>
        <Card>
          <p className="text-xs text-zinc-500">{t(lang, "execution")}</p>
          <p className="mt-1 text-xl font-semibold">{row.execution_permitted ? t(lang, "on") : t(lang, "off")}</p>
        </Card>
      </div>
      <Card>
        <div className="mb-3 flex items-center gap-2">
          <Shield className="h-4 w-4 text-emerald-300" />
          <h2 className="font-semibold">{t(lang, "status")}</h2>
        </div>
        <p className="text-sm text-zinc-300">{row.risk_permitted ? t(lang, "riskOn") : t(lang, "riskOff")}</p>
        <p className="mt-2 text-xs text-zinc-500">{t(lang, "haltHelp")}</p>
        <div className="mt-4 flex gap-2">
          <Button variant="danger" type="button" onClick={() => setConfirm("halt")}>{t(lang, "halt")}</Button>
          <Button variant="outline" type="button" onClick={() => setConfirm("resume")}>{t(lang, "resume")}</Button>
        </div>
        {note ? <p className="mt-3 text-sm text-zinc-400">{note}</p> : null}
      </Card>
      <Card>
        <h2 className="mb-3 font-semibold">{t(lang, "pipeline")}</h2>
        <ol className="space-y-3">
          {([
            ["stepScreen", "stepScreenBody", row.pipeline?.screening.state, row.pipeline?.screening.count],
            ["stepAnalysis", "stepAnalysisBody", row.pipeline?.analysis.state, row.pipeline?.analysis.count],
            ["stepRisk", "stepRiskBody", row.pipeline?.risk.state, undefined],
            ["stepDesk", "stepDeskBody", row.pipeline?.opportunities.state, row.pipeline?.opportunities.count],
          ] as const).map(([title, body, stage, count]) => (
            <li key={title} className="rounded-xl bg-black/20 p-3">
              <div className="flex items-center justify-between gap-3">
                <p className="font-medium">{t(lang, title)}</p>
                <Badge tone={stage === "ready" || stage === "pass" ? "good" : "warn"}>
                  {stage || "pending"}{count === undefined ? "" : ` · ${count}`}
                </Badge>
              </div>
              <p className="text-sm text-zinc-400">{t(lang, body)}</p>
            </li>
          ))}
        </ol>
      </Card>
      {confirm ? (
        <Card>
          <p className="text-sm">{confirm === "halt" ? t(lang, "confirmHalt") : t(lang, "confirmResume")}</p>
          <input className={`${inputClass} mt-3`} value={word} onChange={(event) => setWord(event.target.value)} />
          <div className="mt-3 flex gap-2">
            <Button type="button" onClick={send}>{t(lang, "save")}</Button>
            <Button variant="ghost" type="button" onClick={() => setConfirm(null)}>{t(lang, "cancel")}</Button>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function Signals({ lang }: { lang: Lang }) {
  const loaded = useLoad<{ cards: CardRow[] }>("/api/opportunities");
  if (loaded.error) return <Empty lang={lang} text={t(lang, "error")} />;
  if (!loaded.data) return <p className="text-sm text-zinc-400">{t(lang, "loading")}</p>;
  if (!loaded.data.cards.length) return <Empty lang={lang} text={t(lang, "noSignals")} />;
  return (
    <div className="space-y-3">
      {loaded.data.cards.map((card) => (
        <Card key={card.symbol}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">{card.symbol}</h2>
              <p className="text-xs text-zinc-500">{card.regime} · RSI {card.rsi ?? "—"}</p>
            </div>
            <div className="flex flex-col items-end gap-1">
              <Badge tone={card.action === "BUY" ? "good" : card.action === "SKIP" ? "bad" : "warn"}>{card.action}</Badge>
              {card.fresh === false ? <Badge tone="warn">Stale evidence</Badge> : null}
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
                {card.score.trend}/{card.score.reversal}/{card.score.liquidity}/{card.score.structure}/{card.score.event}
              </span>
            </p>
          ) : null}
          <p className="mt-3 text-sm text-zinc-300">
            {t(lang, "confidence")} {card.confidence}
            {card.model_action !== card.action ? ` · ${t(lang, "model")} ${card.model_action}` : ""}
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            {t(lang, "backtest")}: {card.backtest_passed ? t(lang, "passed") : t(lang, "failed")} ({card.backtest_reason})
          </p>
          {card.as_of ? (
            <p className="mt-2 text-xs text-zinc-500">
              {card.source || "research"} · {card.as_of}
            </p>
          ) : null}
          <p className="mt-3 text-sm leading-6 text-zinc-200">{lang === "id" ? card.why_id : card.why}</p>
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
  const [weights, setWeights] = useState({ trend: 25, reversal: 25, liquidity: 20, structure: 20, event: 10 });
  const [accountCode, setAccountCode] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [note, setNote] = useState("");
  const [ask, setAsk] = useState(false);
  useEffect(() => {
    if (!loaded.data) return;
    setKind(loaded.data.template.kind);
    setPreset(loaded.data.template.preset);
    setStopOn(loaded.data.template.stop_enabled !== false);
    setTakeOn(loaded.data.template.take_profit_enabled !== false);
    setTrailingOn(loaded.data.template.trailing_enabled === true);
    setMinScore(loaded.data.template.min_score || 80);
    setWeights(loaded.data.template.weights || { trend: 25, reversal: 25, liquidity: 20, structure: 20, event: 10 });
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
    setKind(nextKind);
    setPreset(nextPreset);
    const row = await api<{ preview: Preview }>("/api/bots/preview", {
      method: "POST",
      body: JSON.stringify({ ...templateBody(nextKind, nextPreset), kind: nextKind, preset: nextPreset }),
    });
    setPreview(row.preview);
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
  if (loaded.error) return <Empty lang={lang} text={t(lang, "error")} />;
  if (!preview) return <p className="text-sm text-zinc-400">{t(lang, "loading")}</p>;
  const weightSum = Object.values(weights).reduce((sum, value) => sum + Number(value), 0);
  return (
    <div className="space-y-4">
      <AccountsPanel lang={lang} />
      <BudgetForm lang={lang} code={accountCode} />
      <Card>
        <h2 className="font-semibold">{t(lang, "bots")}</h2>
        <p className="mt-1 text-xs text-zinc-500">{t(lang, "places")}</p>
        <div className="mt-4 grid grid-cols-2 gap-2">
          {([
            ["stop", stopOn, setStopOn, "stopToggle"],
            ["take", takeOn, setTakeOn, "takeToggle"],
            ["trail", trailingOn, setTrailingOn, "trailToggle"],
          ] as const).map(([key, value, setter, label]) => (
            <button
              key={key}
              className={`rounded-xl border p-3 text-left ${value ? "border-emerald-400" : "border-amber-400"}`}
              onClick={() => setter(!value)}
              type="button"
            >
              <p className="font-medium">{t(lang, label)}</p>
              <p className="text-xs text-zinc-500">{value ? t(lang, "on") : t(lang, "off")}</p>
            </button>
          ))}
        </div>
        {!stopOn ? <p className="mt-3"><Badge tone="warn">{t(lang, "noSl")}</Badge></p> : null}
        {!takeOn && !trailingOn ? <p className="mt-3"><Badge tone="warn">{t(lang, "manualExit")}</Badge></p> : null}
        <div className="mt-4">
          <Field label={t(lang, "minScore")} hint="50–100">
            <input className={inputClass} value={minScore} onChange={(event) => setMinScore(Number(event.target.value))} />
          </Field>
          {minScore < 80 ? <p className="mt-2"><Badge tone="warn">{t(lang, "scoreWarning")}</Badge></p> : null}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2">
          {(Object.keys(weights) as (keyof typeof weights)[]).map((key) => (
            <Field key={key} label={t(lang, key === "trend" ? "weightTrend" : key === "reversal" ? "weightReversal" : key === "liquidity" ? "weightLiquidity" : key === "structure" ? "weightStructure" : "weightEvent")}>
              <input
                className={inputClass}
                value={weights[key]}
                onChange={(event) => setWeights({ ...weights, [key]: Number(event.target.value) })}
              />
            </Field>
          ))}
        </div>
        <p className={`mt-2 text-xs ${weightSum === 100 ? "text-zinc-500" : "text-amber-200"}`}>{t(lang, "weightsSum")} {weightSum}</p>
        <div className="mt-4 grid grid-cols-2 gap-2">
          {(["dca", "grid"] as const).map((item) => (
            <button
              key={item}
              className={`rounded-xl border p-3 text-left ${kind === item ? "border-emerald-400" : "border-white/10"}`}
              onClick={() => refresh(item, preset)}
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
              onClick={() => refresh(kind, item)}
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
        <Button className="mt-4 w-full" type="button" onClick={save}>{t(lang, "save")}</Button>
        {ask ? <p className="mt-3 text-sm text-amber-200">{t(lang, "aggressiveConfirm")}</p> : null}
        {note ? <p className="mt-3 text-sm text-zinc-400">{note}</p> : null}
      </Card>
      <WarpBotsPanel lang={lang} />
    </div>
  );
}

function Positions({ lang }: { lang: Lang }) {
  const loaded = useLoad<{ positions: Position[] }>("/api/positions");
  if (loaded.error) return <Empty lang={lang} text={t(lang, "error")} />;
  if (!loaded.data) return <p className="text-sm text-zinc-400">{t(lang, "loading")}</p>;
  if (!loaded.data.positions.length) return <Empty lang={lang} text={t(lang, "noPositions")} />;
  return (
    <div className="space-y-3">
      {loaded.data.positions.map((row) => (
        <Card key={row.client_id || row.strategy_id || row.symbol || "row"}>
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">{row.symbol || "—"}</h2>
            <Badge tone={row.paper ? "warn" : "neutral"}>{row.status || "—"}</Badge>
          </div>
          <p className="mt-2 break-all text-xs text-zinc-500">{row.strategy_id}</p>
        </Card>
      ))}
    </div>
  );
}

function ScreenerPage({ lang }: { lang: Lang }) {
  const loaded = useLoad<{ filters: Filters; hits: Hit[]; error: string | null; source: string }>("/api/screener");
  const [form, setForm] = useState<Filters | null>(null);
  const [note, setNote] = useState("");
  useEffect(() => {
    if (loaded.data) setForm(loaded.data.filters);
  }, [loaded.data]);
  if (loaded.error) return <Empty lang={lang} text={t(lang, "error")} />;
  if (!loaded.data || !form) return <p className="text-sm text-zinc-400">{t(lang, "loading")}</p>;
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    // Old TradingView-backed configurations may retain unsupported RSI/rec
    // constraints. Normalize to what the Binance ticker API actually provides.
    const supported = { ...form, rsi_min: "0", rsi_max: "100", min_recommendation: "-1" };
    await api("/api/screener/filters", { method: "POST", body: JSON.stringify(supported) });
    setForm(supported);
    setNote(t(lang, "saved"));
  }
  return (
    <div className="space-y-4">
      <Card>
        <h2 className="font-semibold">{t(lang, "filters")}</h2>
        <p className="mt-1 text-xs text-zinc-500">{t(lang, "source")}: {loaded.data.source || "—"} {loaded.data.error || ""}</p>
        <p className="mt-2 text-xs text-amber-200/80">
          {lang === "id"
            ? "Ticker Binance tidak menyediakan RSI/rekomendasi TradingView. RSI diverifikasi nanti dari candle yang sudah close sebelum validasi risiko."
            : "Binance ticker data has no TradingView RSI/recommendation. RSI is evaluated later from closed exchange candles before risk validation."}
        </p>
        <form className="mt-4 space-y-3" onSubmit={save}>
          <Field label={t(lang, "minVolume")}>
            <input className={inputClass} value={form.min_quote_volume} onChange={(event) => setForm({ ...form, min_quote_volume: event.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label={t(lang, "change")}>
              <input className={inputClass} value={form.min_change_pct} onChange={(event) => setForm({ ...form, min_change_pct: event.target.value })} />
            </Field>
            <Field label=" ">
              <input className={inputClass} value={form.max_change_pct} onChange={(event) => setForm({ ...form, max_change_pct: event.target.value })} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label={t(lang, "rsi")} hint="0–100">
              <input disabled aria-disabled="true" className={inputClass} value={form.rsi_min} onChange={(event) => setForm({ ...form, rsi_min: event.target.value })} />
            </Field>
            <Field label=" ">
              <input disabled aria-disabled="true" className={inputClass} value={form.rsi_max} onChange={(event) => setForm({ ...form, rsi_max: event.target.value })} />
            </Field>
          </div>
          <Field label={t(lang, "limit")}>
            <input className={inputClass} value={form.limit} onChange={(event) => setForm({ ...form, limit: Number(event.target.value) })} />
          </Field>
          <Button type="submit">{t(lang, "apply")}</Button>
          {note ? <p className="text-sm text-zinc-400">{note}</p> : null}
        </form>
      </Card>
      {loaded.data.hits.length === 0 ? <Empty lang={lang} text={t(lang, "noHits")} /> : null}
      {loaded.data.hits.map((hit) => (
        <Card key={hit.symbol}>
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">{hit.symbol}</h2>
            <Badge>{hit.source}</Badge>
          </div>
          <p className="mt-2 text-sm text-zinc-300">
            {pretty(hit.last)} · {pretty(hit.change_pct)}% · vol {compact(hit.quote_volume)}
          </p>
          <p className="text-xs text-zinc-500">RSI {hit.rsi ?? "—"} · rec {hit.recommendation ?? "—"}</p>
        </Card>
      ))}
    </div>
  );
}

function Settings({ lang }: { lang: Lang }) {
  const loaded = useLoad<Record<string, string | number | boolean | string[]>>("/api/config");
  if (loaded.error) return <Empty lang={lang} text={t(lang, "error")} />;
  if (!loaded.data) return <p className="text-sm text-zinc-400">{t(lang, "loading")}</p>;
  const rows = Object.entries(loaded.data);
  return (
    <div className="space-y-4">
    <PaperCheck lang={lang} />
    <Card>
      <h2 className="font-semibold">{t(lang, "settings")}</h2>
      <p className="mt-1 text-sm text-zinc-400">{t(lang, "modeHelp")}</p>
      <dl className="mt-4 space-y-2">
        {rows.map(([key, value]) => (
          <div key={key} className="flex items-start justify-between gap-3 border-b border-white/5 py-2 text-sm">
            <dt className="text-zinc-500">{key}</dt>
            <dd className="max-w-[60%] break-all text-right">{Array.isArray(value) ? value.join(", ") : String(value)}</dd>
          </div>
        ))}
      </dl>
    </Card>
    </div>
  );
}
