import { Activity, Bot, ClipboardCheck, FlaskConical, ShieldCheck, Wallet, WalletCards } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import {
  api,
  type BrokerAccount,
  type BudgetView,
  type ForwardStats,
  type WarpBot,
  type Status,
} from "./api";
import { Key, Lang, settingLabel, t } from "./i18n";
import { Age } from "./age";
import { useLoad } from "./snapshot";
import { fmtMoney, fmtQty } from "./format";
import { Badge, Button, Card, DeskSummary, Field, Loading, PanelTitle, Problem, inputClass } from "./ui";

const REASONS: Record<string, Key> = {
  paper_requires_demo: "rPaperDemo",
  live_ack_required: "rLiveAck",
  observe_refuses_real: "rObserveReal",
  spot_only: "rSpotOnly",
  quote_not_usdt: "rQuoteUsdt",
  "balance unavailable": "rNoBalance",
  balance_unavailable: "rNoBalance",
  error: "rNoBalance",
  usd_mark_unavailable: "rNoUsd",
  account_unavailable: "rNoAccount",
  quote_unavailable: "rQuoteUsdt",
  reserves_unknown: "rReserves",
};

function humanReason(reason: string | null | undefined, lang: Lang): string {
  if (!reason) return "";
  const key = REASONS[reason];
  return key ? t(lang, key) : reason.replaceAll("_", " ");
}

function reasonLabel(reason: string, lang: Lang): string {
  return humanReason(reason, lang) || t(lang, "accountHelp");
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
      <DeskSummary
        icon={WalletCards}
        title={t(lang, "accountMark")}
        value={rows?.find((account) => account.code === selected)?.name || selected || t(lang, "unknown")}
        action={<Button variant="outline" size="sm" type="button" onClick={loaded.reload}>{t(lang, "refresh")}</Button>}
      >
        <Age
          at={loaded.data?.as_of}
          limit={90}
          lang={lang}
          reason={loaded.data?.error || (rows ? null : t(lang, "noAccounts"))}
        />
      </DeskSummary>
      <Card>
        <PanelTitle icon={WalletCards}>{t(lang, "accounts")}</PanelTitle>
        <p className="panel-note">{t(lang, "accountHelp")}</p>
        <p className="panel-note">
          {t(lang, "sleeve")}{" "}
          <b>
            {loaded.data?.sleeve?.eligible
              ? `${fmtMoney(loaded.data.sleeve.eligible, lang)} USDT`
              : `${t(lang, "unavailable")} · ${humanReason(loaded.data?.sleeve?.reason, lang) || t(lang, "noSnapshot")}`}
          </b>
        </p>
      </Card>
      {loaded.error ? <Problem lang={lang} retry={loaded.reload} /> : null}
      {!rows && !loaded.error && <Loading lang={lang} />}
      {rows && rows.length === 0 ? (
        <p className="desk-empty">{t(lang, "noAccounts")}</p>
      ) : null}
      {rows?.map((row) => {
        const unavailable = row.total == null && row.free == null && row.used == null;
        const quote = row.quote || "";
        const money = (v: string | null) => {
          const f = fmtMoney(v, lang);
          return f == null ? t(lang, "unknown") : `${f}${quote ? " " + quote : ""}`;
        };
        const isSelected = selected === row.code;
        return (
          <Card
            key={row.code}
            className={`account-card${unavailable ? " is-unavailable" : ""}${!row.selectable ? " not-selectable" : ""}${isSelected ? " is-selected" : ""}`}
          >
            <div className="account-head">
              <div>
                <h3>{row.name}</h3>
                <p>
                  {row.exchange_name || row.exchange_code} ·{" "}
                  {row.market === "spot" ? t(lang, "spot") : row.market === "futures" ? t(lang, "futures") : row.market || t(lang, "unknown")}
                </p>
              </div>
              <Badge tone={row.demo ? "warn" : "bad"}>{row.badge || (row.demo ? t(lang, "demo") : t(lang, "real"))}</Badge>
            </div>
            {unavailable ? (
              <p className="account-unavailable">
                <b>{t(lang, "balanceUnavailable")}</b>
                <span>
                  {t(lang, "reason")}: {humanReason(row.error || row.reason || row.status, lang) || t(lang, "unknown")}
                </span>
              </p>
            ) : (
              <dl className="account-figures">
                <div>
                  <dt>{t(lang, "total")}</dt>
                  <dd>{money(row.total)}</dd>
                </div>
                <div>
                  <dt>{t(lang, "free")}</dt>
                  <dd>{money(row.free)}</dd>
                </div>
                <div>
                  <dt>{t(lang, "inUse")}</dt>
                  <dd>{money(row.used)}</dd>
                </div>
              </dl>
            )}
            {row.assets?.length ? (
              <div className="account-assets">
                <h4>{t(lang, "assetsHeld")}</h4>
                <ul>
                  {row.assets.map((asset) => (
                    <li key={asset.asset}>
                      <span>
                        <b>{asset.asset}</b> {fmtQty(asset.total, lang) ?? t(lang, "unknown")}
                      </span>
                      <span className={asset.usd == null ? "na" : undefined}>
                        {asset.usd == null
                          ? t(lang, "noUsd")
                          : `≈ ${fmtMoney(asset.usd, lang)} USD`}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {row.selectable ? (
              <Button
                className="mt-3 w-full"
                variant={isSelected ? "outline" : "default"}
                type="button"
                disabled={isSelected}
                aria-pressed={isSelected}
                onClick={() => void choose(row.code)}
              >
                {isSelected ? t(lang, "selectedAccount") : t(lang, "selectAccount")}
              </Button>
            ) : (
              <p className="account-locked">
                <b>{t(lang, "notSelectable")}</b>
                {unavailable && humanReason(row.reason, lang) === humanReason(row.error || row.reason || row.status, lang)
                  ? null
                  : ` · ${reasonLabel(row.reason, lang)}`}
              </p>
            )}
          </Card>
        );
      })}
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
      <PanelTitle icon={Wallet}>{t(lang, "budget")}</PanelTitle>
      {!budget.error && view ? (
        <p className="mt-2 text-sm text-zinc-300">
          {t(lang, "budgetUsed")} <b>{fmtMoney(view.used, lang) ?? t(lang, "unknown")}</b> · {t(lang, "budgetLeft")}{" "}
          <b>{fmtMoney(view.remaining, lang) ?? t(lang, "unknown")}</b>
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
      <DeskSummary icon={Bot} title={t(lang, "warpBots")} value={list.error ? t(lang, "unavailable") : bots?.length ?? "—"}>
        {t(lang, "ownedOnly")}
      </DeskSummary>
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
            {t(lang, "budgetUsed")} <b>{fmtMoney(list.data.budget.used, lang) ?? t(lang, "unknown")}</b>
          </span>
          <span>
            {t(lang, "budgetLeft")} <b>{fmtMoney(list.data.budget.remaining, lang) ?? t(lang, "unknown")}</b>
          </span>
        </div>
      )}
      {bots && bots.length === 0 ? (
        <p className="desk-empty">{t(lang, "noWarpBots")}</p>
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
      <PanelTitle icon={Activity}>{t(lang, "forward")}</PanelTitle>
      <p className="mt-2 text-xs text-zinc-500">{t(lang, "forwardHelp")}</p>
      <p className="mt-2 text-xs text-zinc-500">{stats.hit_rule}</p>
      {empty ? <p className="mt-3 text-sm text-zinc-400">{t(lang, "noForward")}</p> : null}
      <p className="mt-2 text-xs text-zinc-500">
        {t(lang, "pendingVerdicts")}: {stats.pending}
      </p>
      <div className="mt-3 space-y-3">
        {Object.entries(stats.by_verdict).map(([verdict, horizons]) => (
          <div key={verdict} className="desk-record">
            <p className="font-medium">{verdict}</p>
            {stats.horizons.map((label) => {
              const row = horizons[label];
              if (!row) return null;
              const rate = row.hit_rate == null ? t(lang, "unknown") : `${Math.round(Number(row.hit_rate) * 100)}%`;
              const move = row.avg_move == null ? t(lang, "unknown") : `${(Number(row.avg_move) * 100).toFixed(2)}%`;
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
        <PanelTitle icon={ClipboardCheck}>{t(lang, "decisions")}</PanelTitle>
        {cycle.error ? (
          <Problem lang={lang} retry={cycle.reload} />
        ) : !decisions ? (
          <Loading lang={lang} />
        ) : decisions.length === 0 ? (
          <p className="mt-2 text-sm text-zinc-400">{t(lang, "noDecisions")}</p>
        ) : null}
        {!cycle.error &&
          decisions?.map((row) => (
            <div key={row.symbol} className="desk-record mt-3">
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
        <PanelTitle icon={ShieldCheck}>{t(lang, "rules")}</PanelTitle>
        <p className="mt-2 text-xs text-zinc-500">{t(lang, "rulesHelp")}</p>
        {rules.error || rules.data?.error ? (
          <Problem lang={lang} retry={rules.reload} />
        ) : values ? (
          <form className="rules-fields" onSubmit={save}>
            {Object.entries(values).map(([key, value]) => (
              <Field key={key} label={settingLabel(lang, key)}>
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
      <PanelTitle icon={FlaskConical}>{t(lang, "paperCheck")}</PanelTitle>
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
