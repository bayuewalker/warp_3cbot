import { FormEvent, useEffect, useState } from "react";
import {
  api,
  type AccountPosition,
  type AccountPositionsPayload,
  type BrokerAccount,
  type BudgetView,
  type ForwardStats,
  type WarpBot,
  type Status,
} from "./api";
import { Key, Lang, t } from "./i18n";
import { Age, ReceivedAge } from "./age";
import { useLoad } from "./snapshot";
import { fmtMoney, fmtNumber, fmtPct, fmtPct01, fmtPrice, fmtQty, fmtSignedMoney, fmtTime } from "./format";
import { Badge, Button, Card, CardHead, Choice, Empty, Field, KV, Loading, Note, Problem, inputClass } from "./ui";
import { Bot, FlaskConical, Layers, ListChecks, PiggyBank, Scale, Target, Wallet } from "lucide-react";
import { settingHint, settingLabel } from "./labels";

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
  not_loaded: "awaitingWorker",
  strategies_stale: "rStrategiesStale",
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
  cadence_seconds?: number;
};

export function AccountsPanel({ lang, onSelect }: { lang: Lang; onSelect: (code: string) => void }) {
  const loaded = useLoad<AccountsPayload>("/api/accounts", true);
  const book = useLoad<AccountPositionsPayload>("/api/account-positions", true);
  const rows = loaded.data?.accounts;
  const positions = book.data?.positions;
  const [selected, setSelected] = useState("");
  const [viewing, setViewing] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    const code = loaded.data?.selected?.code || "";
    setSelected(code);
    onSelect(code);
  }, [loaded.data?.selected?.code]);

  const counts = book.data?.by_account || {};
  const unassigned = (positions || []).filter((row) => !row.account_code || !rows?.some((a) => a.code === row.account_code));
  const fallback =
    (selected && rows?.some((row) => row.code === selected) && selected) ||
    rows?.find((row) => counts[row.code])?.code ||
    rows?.[0]?.code ||
    "";
  const current = viewing && (viewing === UNASSIGNED || rows?.some((row) => row.code === viewing)) ? viewing : fallback;
  const account = rows?.find((row) => row.code === current) || null;
  const shown =
    current === UNASSIGNED ? unassigned : (positions || []).filter((row) => row.account_code === current);

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

  const cadence = loaded.data?.cadence_seconds || 30;
  const bookCadence = book.data?.cadence_seconds || 60;
  return (
    <div className="stack">
      <Card>
        <CardHead
          icon={Wallet}
          title={t(lang, "accounts")}
          help={t(lang, "accountPicker")}
          aside={
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={() => {
                loaded.reload();
                book.reload();
              }}
            >
              {t(lang, "refresh")}
            </Button>
          }
        />
        <div className="fresh-lines">
          <p>
            <span className="fresh-label">{t(lang, "balancesFresh")}</span>
            <Age
              at={loaded.data?.as_of}
              limit={cadence * 3}
              lang={lang}
              reason={loaded.data?.error || (rows ? null : t(lang, "noAccounts"))}
            />
          </p>
          <p>
            <span className="fresh-label">{t(lang, "positionsFresh")}</span>
            <Age
              at={book.data?.as_of}
              limit={bookCadence * 2.5}
              lang={lang}
              reason={book.data?.error ? humanReason(book.data.error, lang) : null}
            />
          </p>
        </div>
        <p className="card-line">
          {t(lang, "sleeve")}{" "}
          <b>
            {loaded.data?.sleeve?.eligible
              ? `${fmtMoney(loaded.data.sleeve.eligible, lang)} USDT`
              : `${t(lang, "unavailable")} · ${humanReason(loaded.data?.sleeve?.reason, lang) || t(lang, "noSnapshot")}`}
          </b>
        </p>
        {rows && rows.length ? (
          <div className="account-picker" role="group" aria-label={t(lang, "accounts")}>
            {rows.map((row) => (
              <Choice
                key={row.code}
                pressed={row.code === current}
                onClick={() => setViewing(row.code)}
                title={row.name}
                state={<Badge tone={row.demo ? "warn" : "bad"}>{row.badge || (row.demo ? t(lang, "demo") : t(lang, "real"))}</Badge>}
                detail={
                  <>
                    {row.exchange_name || row.exchange_code}
                    {" · "}
                    {counts[row.code] ?? 0} {t(lang, "openCount")}
                    {selected === row.code ? ` · ${t(lang, "selectedAccount")}` : ""}
                  </>
                }
              />
            ))}
            {unassigned.length ? (
              <Choice
                pressed={current === UNASSIGNED}
                onClick={() => setViewing(UNASSIGNED)}
                title={t(lang, "unassigned")}
                detail={`${unassigned.length} ${t(lang, "openCount")}`}
              />
            ) : null}
          </div>
        ) : null}
      </Card>
      {loaded.error ? <Problem lang={lang} retry={loaded.reload} /> : null}
      {!rows && !loaded.error && <Loading lang={lang} />}
      {rows && rows.length === 0 ? <Empty icon={Wallet} text={t(lang, "noAccounts")} /> : null}
      {account ? (
        <AccountBalance lang={lang} row={account} isSelected={selected === account.code} choose={choose} />
      ) : null}
      {account || current === UNASSIGNED ? (
        <OpenPositions lang={lang} rows={shown} book={book.data} failed={book.error} retry={book.reload} />
      ) : null}
      {note ? <Note>{note}</Note> : null}
    </div>
  );
}

const UNASSIGNED = "__unassigned__";

function AccountBalance({
  lang,
  row,
  isSelected,
  choose,
}: {
  lang: Lang;
  row: BrokerAccount;
  isSelected: boolean;
  choose: (code: string) => void;
}) {
  const unavailable = row.total == null && row.free == null && row.used == null;
  const quote = row.quote || "";
  const money = (v: string | null) => {
    const f = fmtMoney(v, lang);
    return f == null ? t(lang, "unknown") : `${f}${quote ? " " + quote : ""}`;
  };
  return (
    <Card
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
                  {asset.usd == null ? t(lang, "noUsd") : `≈ ${fmtMoney(asset.usd, lang)} USD`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {row.selectable ? (
        <Button
          className="w-full"
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
}

const KIND: Record<AccountPosition["kind"], Key> = { dca: "kindDca", grid: "kindGrid", strategy: "kindStrategy" };

function sourceHint(source: string | null, lang: Lang): string | undefined {
  if (source === "3commas") return t(lang, "from3c");
  if (source === "binance") return t(lang, "fromBinance");
  if (source === "computed") return t(lang, "fromComputed");
  return undefined;
}

function OpenPositions({
  lang,
  rows,
  book,
  failed,
  retry,
}: {
  lang: Lang;
  rows: AccountPosition[];
  book: AccountPositionsPayload | null;
  failed: boolean;
  retry: () => void;
}) {
  const none = t(lang, "unknown");
  return (
    <Card>
      <CardHead
        icon={Layers}
        title={t(lang, "openPositions")}
        help={t(lang, "positionsHelp")}
        aside={book ? <Badge tone="info">{rows.length}</Badge> : undefined}
      />
      {failed ? <Problem lang={lang} retry={retry} /> : null}
      {!book && !failed ? <Loading lang={lang} /> : null}
      {book?.stale ? <Note tone="bad">{t(lang, "strategiesStale")}</Note> : null}
      {book?.errors?.length ? (
        <Note tone="warn">
          {t(lang, "partialRead")} {book.errors.join(" · ")}
        </Note>
      ) : null}
      {book && !rows.length ? (
        <p className="empty-line">{book.error && !book.as_of ? humanReason(book.error, lang) : t(lang, "noOpenPositions")}</p>
      ) : null}
      {rows.length ? (
        <ul className="position-list">
          {rows.map((row, index) => {
            const pnl = row.pnl_pct != null ? Number(row.pnl_pct) : row.pnl_usd != null ? Number(row.pnl_usd) : null;
            const tone = pnl == null || pnl === 0 ? "" : pnl > 0 ? " up" : " down";
            const usd = fmtSignedMoney(row.pnl_usd, lang);
            const pct = fmtPct(row.pnl_pct, lang);
            const quote = row.quote_asset || "";
            const size = fmtQty(row.size, lang);
            const cost = fmtMoney(row.cost, lang);
            const opened = fmtTime(row.opened_at, lang, true);
            const tps = row.take_profits.map((p) => fmtPrice(p, lang)).filter(Boolean);
            return (
              <li key={row.id || index} className="position-card">
                <div className="position-head">
                  <div>
                    <h3>{row.pair || row.symbol || none}</h3>
                    <p>{row.bot_name || none}</p>
                  </div>
                  <div className="position-badges">
                    <Badge tone={row.owner === "warp" ? "info" : "neutral"}>
                      {row.owner === "warp" ? t(lang, "ownerWarp") : t(lang, "ownerExternal")}
                    </Badge>
                    {row.stale ? <Badge tone="warn">{t(lang, "staleRow")}</Badge> : null}
                    {row.side ? (
                      <Badge tone={row.side === "long" ? "good" : "bad"}>
                        {row.side === "long" ? t(lang, "sideLong") : t(lang, "sideShort")}
                      </Badge>
                    ) : null}
                  </div>
                </div>
                <p className={`position-pnl${tone}`}>
                  <span className="position-pnl-label">{t(lang, "unrealizedPnl")}</span>
                  <b>{usd != null ? `${usd} USD` : none}</b>
                  <span>{pct ?? none}</span>
                  {sourceHint(row.pnl_usd_source || row.pnl_pct_source, lang) ? (
                    <small>{sourceHint(row.pnl_usd_source || row.pnl_pct_source, lang)}</small>
                  ) : null}
                </p>
                <KV
                  rows={[
                    [t(lang, "averageEntry"), fmtPrice(row.average_price, lang) ?? none],
                    [
                      t(lang, "currentPrice"),
                      fmtPrice(row.current_price, lang) ?? none,
                      sourceHint(row.current_price_source, lang),
                    ],
                    [
                      t(lang, "positionSize"),
                      size != null
                        ? `${size} ${row.base_asset || ""}${cost != null ? ` · ${cost} ${quote}` : ""}`.trim()
                        : none,
                    ],
                    [
                      t(lang, "safetyOrders"),
                      row.safety_used != null && row.safety_max != null
                        ? `${row.safety_used} / ${row.safety_max}`
                        : row.safety_used != null
                          ? String(row.safety_used)
                          : none,
                    ],
                    [t(lang, "takeProfitShort"), tps.length ? tps.join(" · ") : none],
                    [t(lang, "stopPrice"), fmtPrice(row.stop_price, lang) ?? none],
                    [t(lang, "openedAt"), opened ?? none],
                    [t(lang, "botLabel"), t(lang, KIND[row.kind]) + (row.status ? ` · ${row.status}` : "")],
                  ]}
                />
              </li>
            );
          })}
        </ul>
      ) : null}
      <p className="card-help">{t(lang, "readOnlyNote")}</p>
    </Card>
  );
}

export function BudgetForm({ lang, code }: { lang: Lang; code: string }) {
  const [absolute, setAbsolute] = useState("");
  const [fraction, setFraction] = useState("");
  const [maxBots, setMaxBots] = useState("");
  const budget = useLoad<{ budget: BudgetView | null }>("/api/warp-bots", true);
  const view = budget.data?.budget;
  const [note, setNote] = useState("");
  // Live frames arrive every few seconds; reset the draft only when the saved values change.
  useEffect(() => {
    setAbsolute(view?.absolute ?? "");
    setFraction(view?.fraction ?? "");
    setMaxBots(view?.max_bots?.toString() ?? "");
  }, [view?.absolute, view?.fraction, view?.max_bots]);
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
      <CardHead icon={PiggyBank} title={t(lang, "budget")} help={t(lang, "budgetHelp")} />
      <p className="panel-note">
        <ReceivedAge at={budget.received} limit={90} lang={lang} />
      </p>
      {!budget.error && view ? (
        <p className="card-line">
          {t(lang, "budgetUsed")} <b>{fmtMoney(view.used, lang) ?? t(lang, "unknown")}</b> · {t(lang, "budgetLeft")}{" "}
          <b>{fmtMoney(view.remaining, lang) ?? t(lang, "unknown")}</b>
        </p>
      ) : null}
      {budget.error && <Problem lang={lang} retry={budget.reload} />}
      <p className="card-line">
        {t(lang, "currentTarget")} · <b>{code || t(lang, "noAccountSelected")}</b>
      </p>
      <form className="form-stack" onSubmit={save}>
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
      {note ? <Note>{note}</Note> : null}
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
  const accounts = useLoad<{ accounts: BrokerAccount[] }>("/api/accounts", true);
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
        <CardHead
          icon={Bot}
          title={t(lang, "warpBots")}
          help={t(lang, "ownedOnly")}
          aside={<ReceivedAge at={list.received} limit={90} lang={lang} />}
        />
      </Card>
      {list.error && <Problem lang={lang} retry={list.reload} />}
      {accounts.error && <Problem lang={lang} retry={accounts.reload} />}
      {!bots && !list.error && <Loading lang={lang} />}
      {!list.error && list.data?.budget && (
        <div className="hud-strip">
          <div>
            <span>{t(lang, "accountShare")}</span>
            <b className={list.data.budget.sleeve_pct == null ? "kpi-missing" : undefined}>
              {fmtPct01(list.data.budget.sleeve_pct, lang) ?? t(lang, "unknown")}
            </b>
          </div>
          <div>
            <span>{t(lang, "budgetUsed")}</span>
            <b className={list.data.budget.used == null ? "kpi-missing" : undefined}>
              {fmtMoney(list.data.budget.used, lang) ?? t(lang, "unknown")}
            </b>
          </div>
          <div>
            <span>{t(lang, "budgetLeft")}</span>
            <b className={list.data.budget.remaining == null ? "kpi-missing" : undefined}>
              {fmtMoney(list.data.budget.remaining, lang) ?? t(lang, "unknown")}
            </b>
          </div>
        </div>
      )}
      {bots && bots.length === 0 ? <Empty icon={Bot} text={t(lang, "noWarpBots")} /> : null}
      {bots?.map((bot) => (
        <Card key={bot.warp_id}>
          <CardHead
            as="h3"
            title={bot.name}
            help={`${bot.symbol} · ${bot.market} · ${bot.kind}`}
            aside={<Badge>{bot.status.replaceAll("_", " ")}</Badge>}
          />
          <KV
            rows={[
              [
                t(lang, "account"),
                accounts.data?.accounts.find((row) => row.code === bot.account_code)?.badge ?? t(lang, "unknown"),
              ],
              [
                t(lang, "commitment"),
                fmtMoney(bot.commitment, lang) == null ? t(lang, "unknown") : `${fmtMoney(bot.commitment, lang)} USDT`,
              ],
            ]}
          />
          <div className="action-grid">
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
              <p className="confirm-text">
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
                className={inputClass}
                value={word}
                onChange={(event) => setWord(event.target.value)}
              />
              <div className="action-row">
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
      {note ? <Note>{note}</Note> : null}
    </div>
  );
}

export function ForwardPanel({ lang }: { lang: Lang }) {
  const loaded = useLoad<ForwardStats>("/api/forward", true);
  if (loaded.error) return <Problem lang={lang} retry={loaded.reload} />;
  const stats = loaded.data;
  if (!stats) return <Loading lang={lang} />;
  const empty = stats.scored === 0;
  const na = t(lang, "unknown");
  return (
    <Card>
      <CardHead
        icon={Target}
        title={t(lang, "forward")}
        help={t(lang, "forwardHelp")}
        aside={
          <Badge tone="neutral">
            {t(lang, "pendingVerdicts")}: {fmtNumber(stats.pending, lang, 0) ?? na}
          </Badge>
        }
      />
      <p className="panel-note">
        <ReceivedAge at={loaded.received} limit={90} lang={lang} />
      </p>
      <p className="card-line">{stats.hit_rule}</p>
      {empty ? <p className="empty-line">{t(lang, "noForward")}</p> : null}
      <div className="table-scroll">
        <table className="data-table">
          <caption className="sr-only">{t(lang, "forward")}</caption>
          <thead>
            <tr>
              <th scope="col">{t(lang, "verdict")}</th>
              <th scope="col">{t(lang, "horizon")}</th>
              <th scope="col">{t(lang, "hitRate")}</th>
              <th scope="col">{t(lang, "hitsOfScored")}</th>
              <th scope="col">{t(lang, "avgMove")}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(stats.by_verdict).flatMap(([verdict, horizons]) =>
              stats.horizons.map((label) => {
                const row = horizons[label];
                if (!row) return null;
                return (
                  <tr key={verdict + label}>
                    <th scope="row">{verdict}</th>
                    <td data-label={t(lang, "horizon")}>{label}</td>
                    <td data-label={t(lang, "hitRate")} className={row.hit_rate == null ? "na" : undefined}>
                      {fmtPct01(row.hit_rate, lang, 0) ?? na}
                    </td>
                    <td data-label={t(lang, "hitsOfScored")}>
                      {row.hits} / {row.n}
                    </td>
                    <td data-label={t(lang, "avgMove")} className={row.avg_move == null ? "na" : undefined}>
                      {row.avg_move == null ? na : fmtPct(Number(row.avg_move) * 100, lang)}
                    </td>
                  </tr>
                );
              }),
            )}
          </tbody>
        </table>
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
        <CardHead icon={ListChecks} title={t(lang, "decisions")} help={t(lang, "decisionsHelp")} />
        <p className="panel-note">
          <ReceivedAge at={cycle.received} limit={90} lang={lang} />
        </p>
        {cycle.error ? (
          <Problem lang={lang} retry={cycle.reload} />
        ) : !decisions ? (
          <Loading lang={lang} />
        ) : decisions.length === 0 ? (
          <p className="empty-line">{t(lang, "noDecisions")}</p>
        ) : null}
        {!cycle.error &&
          decisions?.map((row) => (
            <div key={row.symbol} className="sub-card">
              <div className="sub-card-head">
                <p>{row.symbol}</p>
                <Badge
                  tone={row.status.startsWith("APPROVED") ? "good" : row.status === "VETO" ? "bad" : "warn"}
                >
                  {row.status.replaceAll("_", " ")}
                </Badge>
              </div>
              {row.reasons?.length ? (
                <p className="card-help">{row.reasons.map((r) => r.replaceAll("_", " ")).join(" · ")}</p>
              ) : null}
            </div>
          ))}
      </Card>
      <Card>
        <CardHead icon={Scale} title={t(lang, "rules")} help={t(lang, "rulesHelp")} />
        {rules.error || rules.data?.error ? (
          <Problem lang={lang} retry={rules.reload} />
        ) : values ? (
          <form className="form-stack" onSubmit={save}>
            {Object.entries(values).map(([key, value]) => (
              <Field
                key={key}
                label={settingLabel(key, lang)}
                hint={[key, settingHint(key, value, lang)].filter(Boolean).join(" · ")}
              >
                <input
                  className={inputClass}
                  inputMode="decimal"
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
          <Loading lang={lang} />
        )}
        {note ? <Note>{note}</Note> : null}
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
      <CardHead
        icon={FlaskConical}
        title={t(lang, "paperCheck")}
        help={t(lang, "paperCheckHelp")}
        aside={!enabled ? <Badge tone="neutral">{t(lang, "paperOnly")}</Badge> : null}
      />
      <form className="form-stack" onSubmit={run}>
        <Field label={t(lang, "confirmWord")} hint="PAPERCHECK">
          <input
            className={inputClass}
            autoComplete="off"
            disabled={!enabled}
            value={word}
            onChange={(event) => setWord(event.target.value)}
          />
        </Field>
        <Button
          className="w-full"
          disabled={!enabled || busy || word.trim().toUpperCase() !== "PAPERCHECK"}
          type="submit"
          variant="outline"
        >
          {t(lang, "paperCheck")}
        </Button>
      </form>
      {note ? <Note>{note}</Note> : null}
    </Card>
  );
}
