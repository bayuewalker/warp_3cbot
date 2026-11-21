import { FormEvent, useEffect, useState } from "react";
import {
  api,
  type BrokerAccount,
  type BudgetView,
  type ForwardStats,
  type WarpBot,
  type Status,
} from "./api";
import { Key, Lang, t } from "./i18n";
import { Age } from "./age";
import { useLoad } from "./snapshot";
import { Badge, Button, Card, Field, Loading, Problem, inputClass } from "./ui";

function reasonLabel(reason: string, lang: Lang): string {
  const known: Record<string, Key> = {
    paper_requires_demo: "accountHelp",
    live_ack_required: "accountHelp",
    observe_refuses_real: "accountHelp",
    spot_only: "notSpot",
    quote_not_usdt: "accountHelp",
  };
  const key = known[reason];
  return key ? t(lang, key) : reason.replaceAll("_", " ");
}

type AccountsPayload = {
  accounts: BrokerAccount[];
  selected: { code?: string } | null;
  as_of?: string | null;
  error?: string | null;
  sleeve?: { eligible: string | null; sleeve: string | null; reason: string | null; basis?: string };
  readonly?: boolean;
};

export function AccountsPanel({ lang, onSelect }: { lang: Lang; onSelect: (code: string) => void }) {
  const loaded = useLoad<AccountsPayload>("/api/accounts", true);
  const rows = loaded.data?.accounts;
  const [selected, setSelected] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    const code = loaded.data?.selected?.code || "";
    setSelected(code);
    onSelect(code);
  }, [loaded.data?.selected?.code]);

  async function choose(code: string) {
    setNote("");
    try {
      await api("/api/accounts/select", { method: "POST", body: JSON.stringify({ code }) });
      setSelected(code);
      setNote(t(lang, "saved"));
      loaded.reload();
    } catch (err) {
      setNote(reasonLabel((err as Error).message, lang));
    }
  }

  return (
    <div className="space-y-3">
      <Card>
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-semibold">{t(lang, "accounts")}</h2>
          <Button variant="outline" size="sm" type="button" onClick={loaded.reload}>
            {t(lang, "refresh")}
          </Button>
        </div>
        <p className="mt-2 text-xs text-zinc-500">{t(lang, "accountHelp")}</p>
        <p className="panel-note">
          <Age
            at={loaded.data?.as_of}
            limit={90}
            lang={lang}
            reason={loaded.data?.error || (rows ? null : t(lang, "noAccounts"))}
          />
        </p>
        <p className="panel-note">
          {t(lang, "sleeve")}{" "}
          {loaded.data?.sleeve?.eligible
            ? `${loaded.data.sleeve.eligible} USDT`
            : `${t(lang, "unavailable")} · ${loaded.data?.sleeve?.reason || t(lang, "noSnapshot")}`}
        </p>
      </Card>
      {loaded.error ? <Problem lang={lang} retry={loaded.reload} /> : null}
      {!rows && !loaded.error && <Loading lang={lang} />}
      {rows && rows.length === 0 ? (
        <Card>
          <p className="text-sm text-zinc-400">{t(lang, "noAccounts")}</p>
        </Card>
      ) : null}
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
            {t(lang, "balance")}{" "}
            {row.total == null
              ? `${t(lang, "unavailable")} · ${row.error || row.reason || t(lang, "noSnapshot")}`
              : `${row.total} ${row.quote}`}
          </p>
          {row.assets?.length ? (
            <ul className="event-log">
              {row.assets.map((asset) => (
                <li key={asset.asset}>
                  <span>
                    {asset.asset} {asset.total}
                  </span>
                  <span>
                    {asset.usd == null
                      ? `${t(lang, "unavailable")} · ${asset.usd_reason || t(lang, "usdUnavailable")}${
                          asset.quote_age_seconds != null ? ` · ${asset.quote_age_seconds}s` : ""
                        }`
                      : `${asset.usd} USD`}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="mt-1 text-xs text-zinc-500">
            {row.market === "spot" ? t(lang, "spot") : t(lang, "notSpot")} · {row.exchange_code}
          </p>
          {!row.selectable ? (
            <p className="mt-2 text-xs text-zinc-400">{reasonLabel(row.reason, lang)}</p>
          ) : null}
          <Button
            className="mt-3 w-full"
            variant={selected === row.code ? "outline" : "default"}
            type="button"
            disabled={!row.selectable || selected === row.code}
            onClick={() => void choose(row.code)}
          >
            {selected === row.code ? t(lang, "selectedAccount") : t(lang, "selectAccount")}
          </Button>
        </Card>
      ))}
      {note ? (
        <p role="status" className="text-sm text-zinc-400">
          {note}
        </p>
      ) : null}
    </div>
  );
}

export function BudgetForm({ lang, code }: { lang: Lang; code: string }) {
  const [absolute, setAbsolute] = useState("");
  const [fraction, setFraction] = useState("");
  const [maxBots, setMaxBots] = useState("");
  const budget = useLoad<{ budget: BudgetView | null }>("/api/warp-bots");
  const view = budget.data?.budget;
  const [note, setNote] = useState("");
  useEffect(() => {
    setAbsolute(view?.absolute ?? "");
    setFraction(view?.fraction ?? "");
    setMaxBots(view?.max_bots?.toString() ?? "");
  }, [view]);
  async function save(event: FormEvent) {
    event.preventDefault();
    setNote("");
    try {
      await api("/api/accounts/budget", {
        method: "POST",
        body: JSON.stringify({
          code,
          budget_usdt: absolute,
          budget_fraction: fraction,
          max_bots: Number(maxBots),
        }),
      });
      setNote(t(lang, "saved"));
    } catch {
      setNote(t(lang, "error"));
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">{t(lang, "budget")}</h2>
      {!budget.error && view ? (
        <p className="mt-2 text-sm text-zinc-300">
          {t(lang, "budgetUsed")} {view.used ?? t(lang, "unavailable")} · {t(lang, "budgetLeft")}{" "}
          {view.remaining ?? t(lang, "unavailable")}
        </p>
      ) : null}
      {budget.error && <Problem lang={lang} retry={budget.reload} />}
      <p className="panel-note">
        {t(lang, "currentTarget")} · {code || t(lang, "unknown")}
      </p>
      <form className="mt-3 space-y-3" onSubmit={save}>
        <Field label={lang === "en" ? "Budget limit (USDT)" : "Batas anggaran (USDT)"}>
          <input
            className={inputClass}
            value={absolute}
            onChange={(event) => setAbsolute(event.target.value)}
          />
        </Field>
        <Field label={lang === "en" ? "Budget fraction" : "Fraksi anggaran"} hint="0.10 = 10%">
          <input
            className={inputClass}
            value={fraction}
            onChange={(event) => setFraction(event.target.value)}
          />
        </Field>
        <Field label={t(lang, "maxBots")}>
          <input
            className={inputClass}
            value={maxBots}
            onChange={(event) => setMaxBots(event.target.value)}
          />
        </Field>
        <Button
          className="w-full"
          type="submit"
          disabled={!code || !absolute || !fraction || !maxBots || budget.error}
        >
          {t(lang, "saveBudget")}
        </Button>
      </form>
      {note ? (
        <p role="status" className="mt-3 text-sm text-zinc-400">
          {note}
        </p>
      ) : null}
    </Card>
  );
}

const confirms = {
  pause: "PAUSE",
  resume: "RESUME",
  close: "CLOSE",
  delete: "DELETE",
} as const;

export function WarpBotsPanel({ lang, status }: { lang: Lang; status: Status | null }) {
  const list = useLoad<{ bots: WarpBot[]; budget: BudgetView | null }>("/api/warp-bots", true);
  const accounts = useLoad<{ accounts: BrokerAccount[] }>("/api/accounts");
  const bots = list.data?.bots;
  const [pending, setPending] = useState<{ id: string; action: keyof typeof confirms } | null>(null);
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  function allowed(bot: WarpBot, action: keyof typeof confirms) {
    const account = accounts.data?.accounts.find((row) => row.code === bot.account_code);
    return (
      !list.error &&
      !accounts.error &&
      status?.mode === "paper" &&
      account?.demo === true &&
      account.market === "spot" &&
      account.status === "active" &&
      !!bot.external_code &&
      ["dca_bot", "grid_bot"].includes(bot.product) &&
      bot.status !== "deleted" &&
      (action !== "resume" || status.halt === false)
    );
  }
  async function act() {
    const bot = bots?.find((row) => row.warp_id === pending?.id);
    if (
      !bot ||
      !pending ||
      busy ||
      !allowed(bot, pending.action) ||
      word.trim().toUpperCase() !== confirms[pending.action]
    )
      return;
    setNote("");
    setBusy(true);
    try {
      await api("/api/warp-bots/action", {
        method: "POST",
        body: JSON.stringify({
          warp_id: pending.id,
          action: pending.action,
          confirm: confirms[pending.action],
        }),
      });
      setPending(null);
      setWord("");
      list.reload();
    } catch (err) {
      setNote((err as Error).message || t(lang, "error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <Card>
        <h2 className="font-semibold">{t(lang, "warpBots")}</h2>
        <p className="mt-2 text-xs text-zinc-500">{t(lang, "ownedOnly")}</p>
      </Card>
      {list.error && <Problem lang={lang} retry={list.reload} />}
      {accounts.error && <Problem lang={lang} retry={accounts.reload} />}
      {!bots && !list.error && <Loading lang={lang} />}
      {!list.error && list.data?.budget && (
        <div className="capital-strip">
          {list.data.budget.sleeve_pct != null && (
            <span>
              {lang === "id" ? "Alokasi akun" : "Account sleeve"}{" "}
              <b>{Number(list.data.budget.sleeve_pct) * 100}%</b>
            </span>
          )}
          <span>
            {t(lang, "budgetUsed")} <b>{list.data.budget.used ?? "—"}</b>
          </span>
          <span>
            {t(lang, "budgetLeft")} <b>{list.data.budget.remaining ?? "—"}</b>
          </span>
        </div>
      )}
      {bots && bots.length === 0 ? (
        <Card>
          <p className="text-sm text-zinc-400">{t(lang, "noWarpBots")}</p>
        </Card>
      ) : null}
      {bots?.map((bot) => (
        <Card key={bot.warp_id}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold">{bot.name}</h3>
              <p className="text-xs text-zinc-500">
                {bot.symbol} · {bot.market} · {bot.kind}
              </p>
            </div>
            <Badge>{bot.status}</Badge>
          </div>
          <p className="panel-note">
            {accounts.data?.accounts.find((row) => row.code === bot.account_code)?.badge ??
              t(lang, "unknown")}
          </p>
          <p className="mt-2 text-sm text-zinc-300">{bot.commitment} USDT</p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {(["pause", "resume", "close", "delete"] as const).map((action) => (
              <Button
                key={action}
                variant={action === "delete" || action === "close" ? "danger" : "outline"}
                disabled={busy || !allowed(bot, action)}
                size="sm"
                type="button"
                onClick={() => {
                  setPending({ id: bot.warp_id, action });
                  setWord("");
                }}
              >
                {t(
                  lang,
                  action === "pause"
                    ? "pauseBot"
                    : action === "resume"
                      ? "resumeBot"
                      : action === "close"
                        ? "closeBot"
                        : "deleteBot",
                )}
              </Button>
            ))}
          </div>
          {pending?.id === bot.warp_id ? (
            <div className="confirm-box">
              <p className="text-sm">
                {t(
                  lang,
                  pending.action === "pause"
                    ? "confirmPause"
                    : pending.action === "resume"
                      ? "confirmBotResume"
                      : pending.action === "close"
                        ? "confirmClose"
                        : "confirmDelete",
                )}
              </p>
              <p className="panel-note">
                {bot.name} · {bot.symbol}
              </p>
              <input
                autoFocus
                aria-label={t(lang, "confirmWord")}
                className={`${inputClass} mt-3`}
                value={word}
                onChange={(event) => setWord(event.target.value)}
              />
              <div className="mt-3 flex gap-2">
                <Button
                  type="button"
                  disabled={
                    busy ||
                    !allowed(bot, pending.action) ||
                    word.trim().toUpperCase() !== confirms[pending.action]
                  }
                  onClick={() => void act()}
                >
                  {confirms[pending.action]}
                </Button>
                <Button variant="ghost" type="button" disabled={busy} onClick={() => setPending(null)}>
                  {t(lang, "cancel")}
                </Button>
              </div>
            </div>
          ) : null}
        </Card>
      ))}
      {note ? (
        <p role="status" className="text-sm text-zinc-400">
          {note}
        </p>
      ) : null}
    </div>
  );
}

export function ForwardPanel({ lang }: { lang: Lang }) {
  const loaded = useLoad<ForwardStats>("/api/forward", true);
  if (loaded.error) return <Problem lang={lang} retry={loaded.reload} />;
  const stats = loaded.data;
  if (!stats) return <Loading lang={lang} />;
  const empty = stats.scored === 0;
  return (
    <Card>
      <h2 className="font-semibold">{t(lang, "forward")}</h2>
      <p className="mt-2 text-xs text-zinc-500">{t(lang, "forwardHelp")}</p>
      <p className="mt-2 text-xs text-zinc-500">{stats.hit_rule}</p>
      {empty ? <p className="mt-3 text-sm text-zinc-400">{t(lang, "noForward")}</p> : null}
      <p className="mt-2 text-xs text-zinc-500">
        {t(lang, "pendingVerdicts")}: {stats.pending}
      </p>
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
  const rules = useLoad<{ values: Record<string, string | number>; error?: string }>("/api/rules");
  const cycle = useLoad<{ decisions: { symbol: string; status: string; reasons: string[] }[] }>(
    "/api/decisions",
    true,
  );
  const [note, setNote] = useState("");
  useEffect(() => {
    setValues(rules.data?.values ?? null);
  }, [rules.data]);
  const decisions = cycle.data?.decisions;
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
      setNote(t(lang, "error"));
    }
  }

  return (
    <div className="form-grid">
      <Card>
        <h2 className="font-semibold">{t(lang, "decisions")}</h2>
        {cycle.error ? (
          <Problem lang={lang} retry={cycle.reload} />
        ) : !decisions ? (
          <Loading lang={lang} />
        ) : decisions.length === 0 ? (
          <p className="mt-2 text-sm text-zinc-400">{t(lang, "noDecisions")}</p>
        ) : null}
        {!cycle.error &&
          decisions?.map((row) => (
            <div key={row.symbol} className="mt-3 rounded-xl bg-black/20 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="font-medium">{row.symbol}</p>
                <Badge
                  tone={row.status.startsWith("APPROVED") ? "good" : row.status === "VETO" ? "bad" : "warn"}
                >
                  {row.status}
                </Badge>
              </div>
              {row.reasons?.length ? (
                <p className="mt-1 text-xs text-zinc-400">{row.reasons.join(", ")}</p>
              ) : null}
            </div>
          ))}
      </Card>
      <Card>
        <h2 className="font-semibold">{t(lang, "rules")}</h2>
        <p className="mt-2 text-xs text-zinc-500">{t(lang, "rulesHelp")}</p>
        {rules.error || rules.data?.error ? (
          <Problem lang={lang} retry={rules.reload} />
        ) : values ? (
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
            <Button className="w-full" type="submit">
              {t(lang, "saveRules")}
            </Button>
          </form>
        ) : (
          <p className="mt-3 text-sm text-zinc-400">{t(lang, "loading")}</p>
        )}
        {note ? (
          <p role="status" className="mt-3 text-sm text-zinc-400">
            {note}
          </p>
        ) : null}
      </Card>
    </div>
  );
}

export function PaperCheck({ lang, enabled }: { lang: Lang; enabled: boolean }) {
  const [word, setWord] = useState("");
  const [note, setNote] = useState("");

  const [busy, setBusy] = useState(false);
  async function run(event: FormEvent) {
    event.preventDefault();
    if (!enabled || busy || word.trim().toUpperCase() !== "PAPERCHECK") return;
    setNote("");
    setBusy(true);
    try {
      const report = await api<{ ok: boolean; steps: { step: string; ok: boolean; detail: string }[] }>(
        "/api/paper/roundtrip",
        { method: "POST", body: JSON.stringify({ confirm: "PAPERCHECK" }) },
      );
      setWord("");
      setNote(report.steps.map((step) => `${step.step}: ${step.ok ? "ok" : step.detail}`).join(" · "));
    } catch (err) {
      setNote((err as Error).message || t(lang, "error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">{t(lang, "paperCheck")}</h2>
      <p className="mt-2 text-xs text-zinc-500">{t(lang, "paperCheckHelp")}</p>
      <form className="mt-3 space-y-3" onSubmit={run}>
        <input
          aria-label={t(lang, "confirmWord")}
          className={inputClass}
          value={word}
          onChange={(event) => setWord(event.target.value)}
        />
        <Button
          className="w-full"
          disabled={!enabled || busy || word.trim().toUpperCase() !== "PAPERCHECK"}
          type="submit"
          variant="outline"
        >
          {t(lang, "paperCheck")}
        </Button>
      </form>
      {note ? (
        <p role="status" className="mt-3 text-sm text-zinc-300">
          {note}
        </p>
      ) : null}
    </Card>
  );
}
