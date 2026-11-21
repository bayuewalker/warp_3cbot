import { FormEvent, useEffect, useState } from "react";
import {
  api,
  type BrokerAccount,
  type BudgetView,
  type ForwardStats,
  type WarpBot,
} from "./api";
import { Key, Lang, t } from "./i18n";
import { Badge, Button, Card, Field, inputClass } from "./ui";

function reasonLabel(reason: string, lang: Lang): string {
  const known: Record<string, Key> = {
    paper_requires_demo: "accountHelp",
    live_ack_required: "accountHelp",
    observe_refuses_real: "accountHelp",
    futures_requires_isolated: "leverageHelp",
    margin_unconfirmed: "marginNote",
    quote_not_usdt: "accountHelp",
  };
  const key = known[reason];
  return key ? t(lang, key) : reason.replaceAll("_", " ");
}

export function AccountsPanel({ lang }: { lang: Lang }) {
  const [rows, setRows] = useState<BrokerAccount[] | null>(null);
  const [selected, setSelected] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  async function load() {
    setError("");
    try {
      const payload = await api<{ accounts: BrokerAccount[]; selected: { code?: string } | null }>("/api/accounts");
      setRows(payload.accounts);
      setSelected(payload.selected?.code || "");
    } catch {
      setError(t(lang, "error"));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function choose(code: string) {
    setNote("");
    try {
      await api("/api/accounts/select", { method: "POST", body: JSON.stringify({ code }) });
      setSelected(code);
      setNote(t(lang, "saved"));
      await load();
    } catch (err) {
      setNote(reasonLabel((err as Error).message, lang));
    }
  }

  return (
    <div className="space-y-3">
      <Card>
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-semibold">{t(lang, "accounts")}</h2>
          <Button variant="outline" size="sm" type="button" onClick={() => void load()}>
            {t(lang, "refresh")}
          </Button>
        </div>
        <p className="mt-2 text-xs text-zinc-500">{t(lang, "accountHelp")}</p>
      </Card>
      {error ? <Card><p className="text-sm text-red-300">{error}</p></Card> : null}
      {rows && rows.length === 0 ? <Card><p className="text-sm text-zinc-400">{t(lang, "noAccounts")}</p></Card> : null}
      {rows?.map((row) => (
        <Card key={row.code}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold">{row.name}</h3>
              <p className="text-xs text-zinc-500">{row.exchange_name || row.exchange_code}</p>
            </div>
            <Badge tone={row.demo ? "warn" : "bad"}>{row.demo ? t(lang, "demo") : t(lang, "real")}</Badge>
          </div>
          <p className="mt-3 text-sm text-zinc-200">
            {t(lang, "balance")} {row.total} {row.quote}
          </p>
          <p className="mt-1 text-xs text-zinc-500">
            {row.market === "futures" ? t(lang, "futures") : t(lang, "spot")} · {row.exchange_code} · {row.margin_mode}
          </p>
          {row.reason === "margin_unconfirmed" ? <p className="mt-2 text-xs text-amber-200">{t(lang, "marginNote")}</p> : null}
          {!row.selectable ? <p className="mt-2 text-xs text-zinc-400">{reasonLabel(row.reason, lang)}</p> : null}
          <Button
            className="mt-3 w-full"
            variant={selected === row.code ? "outline" : "default"}
            type="button"
            disabled={!row.selectable}
            onClick={() => void choose(row.code)}
          >
            {selected === row.code ? t(lang, "selectedAccount") : t(lang, "selectAccount")}
          </Button>
        </Card>
      ))}
      {note ? <p className="text-sm text-zinc-400">{note}</p> : null}
    </div>
  );
}

export function BudgetForm({ lang, code }: { lang: Lang; code: string }) {
  const [absolute, setAbsolute] = useState("100");
  const [fraction, setFraction] = useState("0.10");
  const [maxBots, setMaxBots] = useState("2");
  const [view, setView] = useState<BudgetView | null>(null);
  const [target, setTarget] = useState(code);
  const [note, setNote] = useState("");

  async function load() {
    const [payload, accounts] = await Promise.all([
      api<{ budget: BudgetView | null }>("/api/warp-bots"),
      api<{ selected: { code?: string } | null }>("/api/accounts"),
    ]);
    const chosen = code || accounts.selected?.code || "";
    setTarget(chosen);
    if (payload.budget) {
      setView(payload.budget);
      setAbsolute(payload.budget.absolute);
      setFraction(payload.budget.fraction);
      setMaxBots(String(payload.budget.max_bots));
    }
  }

  useEffect(() => {
    void load().catch(() => setNote(t(lang, "error")));
  }, [code]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setNote("");
    try {
      await api("/api/accounts/budget", {
        method: "POST",
        body: JSON.stringify({
          code: target,
          budget_usdt: absolute,
          budget_fraction: fraction,
          max_bots: Number(maxBots),
        }),
      });
      setNote(t(lang, "saved"));
      await load();
    } catch {
      setNote(t(lang, "error"));
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">{t(lang, "budget")}</h2>
      {view ? (
        <p className="mt-2 text-sm text-zinc-300">
          {t(lang, "budgetUsed")} {view.used} · {t(lang, "budgetLeft")} {view.remaining} · {view.open_bots}/{view.max_bots}
        </p>
      ) : null}
      <form className="mt-3 space-y-3" onSubmit={save}>
        <Field label="USDT">
          <input className={inputClass} value={absolute} onChange={(event) => setAbsolute(event.target.value)} />
        </Field>
        <Field label="%" hint="0.10 = 10%">
          <input className={inputClass} value={fraction} onChange={(event) => setFraction(event.target.value)} />
        </Field>
        <Field label={t(lang, "maxBots")}>
          <input className={inputClass} value={maxBots} onChange={(event) => setMaxBots(event.target.value)} />
        </Field>
        <Button className="w-full" type="submit" disabled={!target}>
          {t(lang, "saveBudget")}
        </Button>
      </form>
      {note ? <p className="mt-3 text-sm text-zinc-400">{note}</p> : null}
    </Card>
  );
}

const confirms = {
  pause: "PAUSE",
  resume: "RESUME",
  close: "CLOSE",
  delete: "DELETE",
} as const;

export function WarpBotsPanel({ lang }: { lang: Lang }) {
  const [bots, setBots] = useState<WarpBot[] | null>(null);
  const [pending, setPending] = useState<{ id: string; action: keyof typeof confirms } | null>(null);
  const [word, setWord] = useState("");
  const [note, setNote] = useState("");

  async function load() {
    const payload = await api<{ bots: WarpBot[] }>("/api/warp-bots");
    setBots(payload.bots);
  }

  useEffect(() => {
    void load().catch(() => setNote(t(lang, "error")));
  }, []);

  async function act() {
    if (!pending || word.trim().toUpperCase() !== confirms[pending.action]) return;
    setNote("");
    try {
      await api("/api/warp-bots/action", {
        method: "POST",
        body: JSON.stringify({ warp_id: pending.id, action: pending.action, confirm: confirms[pending.action] }),
      });
      setPending(null);
      setWord("");
      setBots(null);
    } catch (err) {
      setNote((err as Error).message || t(lang, "error"));
    }
  }

  return (
    <div className="space-y-3">
      <Card>
        <h2 className="font-semibold">{t(lang, "warpBots")}</h2>
        <p className="mt-2 text-xs text-zinc-500">{t(lang, "ownedOnly")}</p>
      </Card>
      {bots && bots.length === 0 ? <Card><p className="text-sm text-zinc-400">{t(lang, "noWarpBots")}</p></Card> : null}
      {bots?.map((bot) => (
        <Card key={bot.warp_id}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold">{bot.name}</h3>
              <p className="text-xs text-zinc-500">{bot.symbol} · {bot.market} · {bot.kind}</p>
            </div>
            <Badge>{bot.status}</Badge>
          </div>
          <p className="mt-2 text-sm text-zinc-300">{bot.commitment} USDT</p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {(["pause", "resume", "close", "delete"] as const).map((action) => (
              <Button
                key={action}
                variant={action === "delete" || action === "close" ? "danger" : "outline"}
                size="sm"
                type="button"
                onClick={() => {
                  setPending({ id: bot.warp_id, action });
                  setWord("");
                }}
              >
                {t(lang, action === "pause" ? "pauseBot" : action === "resume" ? "resumeBot" : action === "close" ? "closeBot" : "deleteBot")}
              </Button>
            ))}
          </div>
        </Card>
      ))}
      {pending ? (
        <Card>
          <p className="text-sm">{t(lang, pending.action === "pause" ? "confirmPause" : pending.action === "resume" ? "confirmBotResume" : pending.action === "close" ? "confirmClose" : "confirmDelete")}</p>
          <input className={`${inputClass} mt-3`} value={word} onChange={(event) => setWord(event.target.value)} />
          <div className="mt-3 flex gap-2">
            <Button type="button" onClick={() => void act()}>{t(lang, "save")}</Button>
            <Button variant="ghost" type="button" onClick={() => setPending(null)}>{t(lang, "cancel")}</Button>
          </div>
        </Card>
      ) : null}
      {note ? <p className="text-sm text-zinc-400">{note}</p> : null}
    </div>
  );
}

export function ForwardPanel({ lang }: { lang: Lang }) {
  const [stats, setStats] = useState<ForwardStats | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    void api<ForwardStats>("/api/forward")
      .then(setStats)
      .catch(() => setError(t(lang, "error")));
  }, []);
  if (error) return <Card><p className="text-sm text-red-300">{error}</p></Card>;
  if (!stats) return <p className="text-sm text-zinc-400">{t(lang, "loading")}</p>;
  const empty = stats.scored === 0;
  return (
    <Card>
      <h2 className="font-semibold">{t(lang, "forward")}</h2>
      <p className="mt-2 text-xs text-zinc-500">{t(lang, "forwardHelp")}</p>
      <p className="mt-2 text-xs text-zinc-500">{stats.hit_rule}</p>
      {empty ? <p className="mt-3 text-sm text-zinc-400">{t(lang, "noForward")}</p> : null}
      <p className="mt-2 text-xs text-zinc-500">{t(lang, "pendingVerdicts")}: {stats.pending}</p>
      <div className="mt-3 space-y-3">
        {Object.entries(stats.by_verdict).map(([verdict, horizons]) => (
          <div key={verdict} className="rounded-xl bg-black/20 p-3">
            <p className="font-medium">{verdict}</p>
            {stats.horizons.map((label) => {
              const row = horizons[label];
              if (!row) return null;
              const rate = row.hit_rate == null ? "—" : `${Math.round(Number(row.hit_rate) * 100)}%`;
              const move = row.avg_move == null ? "—" : `${(Number(row.avg_move) * 100).toFixed(2)}%`;
              return (
                <p key={label} className="mt-1 text-sm text-zinc-300">
                  {label} · {t(lang, "hitRate")} {rate} ({row.hits}/{row.n}) · {t(lang, "avgMove")} {move}
                </p>
              );
            })}
          </div>
        ))}
      </div>
    </Card>
  );
}

export function RulesPage({ lang }: { lang: Lang }) {
  const [values, setValues] = useState<Record<string, string | number> | null>(null);
  const [decisions, setDecisions] = useState<{ symbol: string; status: string; reasons: string[] }[]>([]);
  const [note, setNote] = useState("");

  async function load() {
    const [rules, cycle] = await Promise.all([
      api<{ values: Record<string, string | number> }>("/api/rules"),
      api<{ decisions: { symbol: string; status: string; reasons: string[] }[] }>("/api/decisions"),
    ]);
    setValues(rules.values);
    setDecisions(cycle.decisions || []);
  }

  useEffect(() => {
    void load().catch(() => setNote(t(lang, "error")));
  }, [lang]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!values) return;
    setNote("");
    try {
      const saved = await api<{ values: Record<string, string | number> }>("/api/rules", {
        method: "POST",
        body: JSON.stringify(values),
      });
      setValues(saved.values);
      setNote(t(lang, "saved"));
    } catch {
      setNote(t(lang, "weightsSum"));
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <h2 className="font-semibold">{t(lang, "decisions")}</h2>
        {decisions.length === 0 ? <p className="mt-2 text-sm text-zinc-400">{t(lang, "noDecisions")}</p> : null}
        {decisions.map((row) => (
          <div key={row.symbol} className="mt-3 rounded-xl bg-black/20 p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="font-medium">{row.symbol}</p>
              <Badge tone={row.status.startsWith("APPROVED") ? "good" : row.status === "VETO" ? "bad" : "warn"}>{row.status}</Badge>
            </div>
            {row.reasons?.length ? <p className="mt-1 text-xs text-zinc-400">{row.reasons.join(", ")}</p> : null}
          </div>
        ))}
      </Card>
      <Card>
        <h2 className="font-semibold">{t(lang, "rules")}</h2>
        <p className="mt-2 text-xs text-zinc-500">{t(lang, "rulesHelp")}</p>
        {values ? (
          <form className="mt-4 space-y-3" onSubmit={save}>
            {Object.entries(values).map(([key, value]) => (
              <Field key={key} label={key}>
                <input
                  className={inputClass}
                  value={String(value)}
                  onChange={(event) => setValues({ ...values, [key]: event.target.value })}
                />
              </Field>
            ))}
            {Number(values.min_score) < 80 ? <Badge tone="warn">{t(lang, "scoreWarning")}</Badge> : null}
            <Button className="w-full" type="submit">{t(lang, "saveRules")}</Button>
          </form>
        ) : (
          <p className="mt-3 text-sm text-zinc-400">{t(lang, "loading")}</p>
        )}
        {note ? <p className="mt-3 text-sm text-zinc-400">{note}</p> : null}
      </Card>
    </div>
  );
}

export function PaperCheck({ lang }: { lang: Lang }) {
  const [word, setWord] = useState("");
  const [note, setNote] = useState("");

  async function run(event: FormEvent) {
    event.preventDefault();
    if (word.trim().toUpperCase() !== "PAPERCHECK") return;
    setNote("");
    try {
      const report = await api<{ ok: boolean; steps: { step: string; ok: boolean; detail: string }[] }>(
        "/api/paper/roundtrip",
        { method: "POST", body: JSON.stringify({ confirm: "PAPERCHECK" }) },
      );
      setNote(report.steps.map((step) => `${step.step}: ${step.ok ? "ok" : step.detail}`).join(" · "));
    } catch (err) {
      setNote((err as Error).message || t(lang, "error"));
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">{t(lang, "paperCheck")}</h2>
      <p className="mt-2 text-xs text-zinc-500">{t(lang, "paperCheckHelp")}</p>
      <form className="mt-3 space-y-3" onSubmit={run}>
        <input className={inputClass} value={word} onChange={(event) => setWord(event.target.value)} />
        <Button className="w-full" type="submit" variant="outline">{t(lang, "paperCheck")}</Button>
      </form>
      {note ? <p className="mt-3 text-sm text-zinc-300">{note}</p> : null}
    </Card>
  );
}
