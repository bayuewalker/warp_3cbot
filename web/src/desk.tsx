import { FormEvent, useEffect, useState } from "react";
import {
  api,
  type AccountPositionsPayload,
  type AccountsPayload,
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
import {
  UNASSIGNED, accountsView, botGroups, botsView, bookView, capital, scope, shown, sourceKey,
  type AccountView, type BookError, type BookView, type Capital, type Fact, type PositionView,
} from "./tradingdesk/accounts-model";
import type { ReadInput, ReadIssue } from "./tradingdesk/types";

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

const SAVE_ERRORS: Record<string, Key> = {
  invalid_rules: "errInvalidRules",
  invalid_budget: "errInvalidBudget",
  journal_unavailable: "errJournal",
};

/** Readable reason for a refused save. Unknown failures keep the generic message. */
function saveFailure(err: unknown, lang: Lang): string {
  const key = err instanceof Error ? SAVE_ERRORS[err.message] : undefined;
  return t(lang, key ?? "error");
}

function useTick(ms = 1000) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((value) => value + 1), ms);
    return () => window.clearInterval(id);
  }, [ms]);
}

function readOf<T>(load: { data: T | null; received: Date | null; issue: ReadIssue | null }): ReadInput {
  return { data: load.data, received: load.received, issue: load.issue };
}

/** Source and its own clock for a single value. A source without a clock says so. */
function tag(item: Fact<unknown>, lang: Lang): string {
  const source = sourceKey(item.source);
  if (!source) return "";
  const at = fmtTime(item.sourceAt, lang, true);
  return `${t(lang, source)} · ${at ?? t(lang, "sourceTimeMissing")}`;
}

export function AccountsPanel({ lang, onSelect }: { lang: Lang; onSelect: (code: string) => void }) {
  const loaded = useLoad<AccountsPayload>("/api/accounts", true);
  const book = useLoad<AccountPositionsPayload>("/api/account-positions", true);
  const botsLoad = useLoad<{ bots: WarpBot[]; budget: BudgetView | null }>("/api/warp-bots", true);
  const [selected, setSelected] = useState("");
  const [viewing, setViewing] = useState("");
  const [note, setNote] = useState("");
  useTick();
  const now = Date.now();

  const accounts = accountsView(readOf(loaded), now);
  const positions = bookView(readOf(book), now);
  const bots = botsView(readOf(botsLoad), now);

  useEffect(() => {
    const code = loaded.data?.selected?.code || "";
    setSelected(code);
    onSelect(code);
  }, [loaded.data?.selected?.code]);

  const rows = accounts.known ? accounts.accounts : undefined;
  const accountReason = loaded.refreshFailed ? t(lang, "error") : humanReason(loaded.data?.error, lang) ||
    (loaded.data && !rows ? t(lang, "awaitingWorker") : "");
  const bookReason = book.refreshFailed ? t(lang, "error") : humanReason(book.data?.error, lang) ||
    (book.data && (!positions.known || positions.errors.length > 0 || !Array.isArray(book.data.errors))
      ? t(lang, "incompleteBook") : "");
  const bookIssue = bookReason || (book.data?.stale ? t(lang, "staleRow") : "");
  const view = scope(accounts, positions, viewing, selected);
  const { current, account, shown, unassigned } = view;
  const markStale = shown.some((row) => row.markState.stale) || Boolean(account?.assets.some((asset) => asset.markStale));

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

  if (loaded.data && "state" in loaded.data && loaded.data.state === "postponed") {
    return <Card><CardHead icon={Wallet} title={t(lang, "spotPostponed")} help={t(lang, "spotPostponedDetail")} /></Card>;
  }

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
              at={accountReason ? null : loaded.data?.as_of}
              limit={accounts.cadenceSeconds * 3}
              lang={lang}
              reason={accountReason || (!loaded.data?.as_of ? t(lang, "awaitingWorker") : null)}
            />
          </p>
          <p>
            <span className="fresh-label">{t(lang, "positionsFresh")}</span>
            <Age
              at={bookIssue ? null : book.data?.as_of}
              limit={positions.cadenceSeconds * 2.5}
              lang={lang}
              reason={bookIssue || null}
            />
          </p>
        </div>
        {positions.brokerStale || positions.brokerPartial || accounts.stale || markStale ? (
          <ul className="p4b-flags" aria-label={t(lang, "readState")}>
            {positions.brokerStale ? <li><Badge tone="warn">{t(lang, "brokerStaleBadge")}</Badge></li> : null}
            {positions.brokerPartial ? <li><Badge tone="warn">{t(lang, "brokerPartialBadge")}</Badge></li> : null}
            {accounts.stale ? <li><Badge tone="warn">{t(lang, "accountStaleBadge")}</Badge></li> : null}
            {markStale ? <li><Badge tone="warn">{t(lang, "markStaleBadge")}</Badge></li> : null}
          </ul>
        ) : null}
        <p className="card-line">
          {t(lang, "sleeve")}{" "}
          <b>
            {accounts.current && accounts.sleeve.eligible.value !== null
              ? `${fmtMoney(accounts.sleeve.eligible.value, lang)} USDT`
              : `${t(lang, "unavailable")} · ${humanReason(loaded.data?.sleeve?.reason, lang) || t(lang, "noSnapshot")}`}
          </b>
        </p>
        {(rows && rows.length) || unassigned.length ? (
          <div className="account-picker" role="group" aria-label={t(lang, "accounts")}>
            {(rows || []).map((row) => {
              const total = view.count(row.code);
              return (
                <Choice
                  key={row.code}
                  pressed={row.code === current}
                  onClick={() => setViewing(row.code)}
                  title={row.name}
                  state={
                    <Badge tone={row.badge === "DEMO" ? "warn" : "bad"}>
                      {row.badge ? (row.badge === "DEMO" ? t(lang, "demo") : t(lang, "real")) : t(lang, "unknown")}
                    </Badge>
                  }
                  detail={
                    <>
                      {row.exchange || t(lang, "unknown")}
                      {" · "}
                      {marketLabel(row.market, lang)}
                      {" · "}
                      {row.status || t(lang, "unknown")}
                      {row.quote ? ` · ${row.quote}` : ""}
                      {" · "}
                      {t(lang, "openPositions")} · {total !== null ? total : t(lang, "unavailable")}
                      {selected === row.code ? ` · ${t(lang, "selectedAccount")}` : ""}
                    </>
                  }
                />
              );
            })}
            {unassigned.length ? (
              <Choice
                pressed={current === UNASSIGNED}
                onClick={() => setViewing(UNASSIGNED)}
                title={t(lang, "unassigned")}
                detail={`${unassigned.length} ${t(lang, positions.complete ? "openCount" : "reportedRows")}`}
              />
            ) : null}
          </div>
        ) : null}
      </Card>
      {loaded.refreshFailed ? <Problem lang={lang} retry={loaded.reload} /> : null}
      {!loaded.data && !loaded.error && <Loading lang={lang} />}
      {accounts.current && rows?.length === 0 ? <Empty icon={Wallet} text={t(lang, "noAccounts")} /> : null}
      {account ? (
        <AccountBalance
          lang={lang}
          row={account}
          capitalView={capital(accounts, account, bots, now)}
          sleeveReason={humanReason(loaded.data?.sleeve?.reason, lang)}
          isSelected={selected === account.code}
          choose={choose}
        />
      ) : null}
      {account || current === UNASSIGNED ? (
        <OpenPositions
          lang={lang}
          rows={shown}
          book={positions}
          loading={!book.data}
          complete={positions.complete && (accounts.current || current === UNASSIGNED)}
          failed={book.refreshFailed}
          retry={book.reload}
          unassigned={current === UNASSIGNED}
        />
      ) : null}
      {shown.length ? <BotsReporting lang={lang} rows={shown} /> : null}
      {note ? <Note>{note}</Note> : null}
    </div>
  );
}

function marketLabel(market: string | null, lang: Lang) {
  return market === "spot" ? t(lang, "spot") : market === "futures" ? t(lang, "futures") : market || t(lang, "unknown");
}

function AccountBalance({
  lang,
  row,
  capitalView,
  sleeveReason,
  isSelected,
  choose,
}: {
  lang: Lang;
  row: AccountView;
  capitalView: Capital;
  sleeveReason: string;
  isSelected: boolean;
  choose: (code: string) => void;
}) {
  const unavailable = row.total.value === null && row.free.value === null && row.used.value === null;
  const quote = row.quote || "";
  const money = (item: Fact<string>) => {
    const text = shown(item, lang, (value) => fmtMoney(value, lang));
    return item.value === null ? text : `${text}${quote ? " " + quote : ""}`;
  };
  const cell = (label: string, item: Fact<unknown>, value: string, extra?: string) => (
    <div className="p4b-cell">
      <dt>{label}</dt>
      <dd className={item.value === null ? "na" : undefined}>{value}</dd>
      <small>
        {item.value === null ? extra || t(lang, "unknown") : tag(item, lang)}
        {item.state === "stale" ? ` · ${t(lang, "staleRow")}` : ""}
      </small>
    </div>
  );
  const usdt = (item: Fact<string>) => {
    const text = shown(item, lang, (value) => fmtMoney(value, lang));
    return item.value === null ? text : `${text} USDT`;
  };
  const budget = capitalView;
  const absent = budget.openBots.value !== null ? t(lang, "notPublished") : undefined;
  return (
    <Card
      className={`account-card${unavailable ? " is-unavailable" : ""}${!row.selectable ? " not-selectable" : ""}${isSelected ? " is-selected" : ""}`}
    >
      <div className="account-head">
        <div>
          <h3>{row.name}</h3>
          <p>
            {row.exchange || t(lang, "unknown")} · {marketLabel(row.market, lang)}
          </p>
        </div>
        <Badge tone={row.badge === "DEMO" ? "warn" : "bad"}>
          {row.badge ? (row.badge === "DEMO" ? t(lang, "demo") : t(lang, "real")) : t(lang, "unknown")}
        </Badge>
      </div>
      {row.stale ? <Badge tone="warn">{t(lang, "accountStaleBadge")}</Badge> : null}
      {unavailable ? (
        <p className="account-unavailable">
          <b>{t(lang, "balanceUnavailable")}</b>
          <span>
            {t(lang, "reason")}: {humanReason(row.error || row.reason || row.status, lang) || t(lang, "unknown")}
          </span>
        </p>
      ) : null}
      {row.rejected.length ? (
        <p className="card-help">
          {t(lang, "rejectedValue")} · {row.rejected.join(", ")}
        </p>
      ) : null}
      <h4 className="p4b-heading">{t(lang, "capitalTitle")}</h4>
      <dl className="p4b-capital">
        {cell(t(lang, "total"), row.total, money(row.total))}
        {cell(t(lang, "free"), row.free, money(row.free))}
        {cell(t(lang, "inUse"), row.used, money(row.used))}
        {cell(
          t(lang, "sleeve"),
          budget.sleeveEligible,
          usdt(budget.sleeveEligible),
          budget.sleeveEligible.reason === "not_selected_account" ? t(lang, "notSelectedAccount") : sleeveReason || undefined,
        )}
        {cell(t(lang, "sleeveConfigured"), budget.sleeveConfigured, usdt(budget.sleeveConfigured))}
        {cell(t(lang, "accountShare"), budget.sleevePct, shown(budget.sleevePct, lang, (value) => fmtPct01(value, lang)))}
        {cell(t(lang, "budgetUsed"), budget.budgetUsed, usdt(budget.budgetUsed), absent)}
        {cell(t(lang, "budgetLeft"), budget.budgetRemaining, usdt(budget.budgetRemaining), absent)}
        {cell(t(lang, "openWarpBots"), budget.openBots, shown(budget.openBots, lang, String), t(lang, "derivedBots"))}
        {cell(t(lang, "maxBots"), budget.maxBots, shown(budget.maxBots, lang, String), absent)}
      </dl>
      {row.assets.length ? (
        <div className="account-assets">
          <h4>{t(lang, "assetsHeld")}</h4>
          <ul>
            {row.assets.map((asset) => (
              <li key={asset.asset} className="p4b-asset">
                <span>
                  <b>{asset.asset}</b> {shown(asset.total, lang, (value) => fmtQty(value, lang))}
                </span>
                <span className={asset.usd.value === null ? "na" : undefined}>
                  {asset.usd.value === null ? t(lang, "noUsd") : `≈ ${fmtMoney(asset.usd.value, lang)} USD`}
                </span>
                <small className="p4b-asset-note">
                  {asset.usd.value !== null
                    ? asset.usd.source === "computed"
                      ? t(lang, "derivedUsd")
                      : t(lang, sourceKey(asset.usd.source)!)
                    : humanReason(asset.usdReason, lang) || t(lang, "unknown")}
                  {asset.markAgeSeconds !== null
                    ? ` · ${t(lang, "markAge")} ${asset.markAgeSeconds}s`
                    : asset.usd.source === "computed" || asset.usd.value === null
                      ? ` · ${t(lang, "markAgeMissing")}`
                      : ""}
                </small>
                {asset.markStale ? <Badge tone="warn">{t(lang, "markStaleBadge")}</Badge> : null}
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
            : ` · ${reasonLabel(row.reason || "", lang)}`}
        </p>
      )}
    </Card>
  );
}

const KIND: Record<NonNullable<PositionView["kind"]>, Key> = { dca: "kindDca", grid: "kindGrid", strategy: "kindStrategy" };
const ERROR_LABEL: Record<BookError["kind"], Key> = {
  incomplete_rows: "errIncomplete", read_cap: "errReadCap", strategies: "errStrategies",
  list_failed: "errListFailed", read_failed: "errReadFailed", other: "errOther",
};

function PositionCard({ lang, row }: { lang: Lang; row: PositionView }) {
  const none = t(lang, "unknown");
  const pnl = row.pnlPct.value !== null ? Number(row.pnlPct.value) : row.pnlUsd.value !== null ? Number(row.pnlUsd.value) : null;
  const tone = pnl == null || pnl === 0 ? "" : pnl > 0 ? " up" : " down";
  const quote = row.quoteAsset || "";
  const tps = row.takeProfits.value?.map((price) => fmtPrice(price, lang)).filter(Boolean) ?? [];
  const safety = row.safety.value;
  const markHint = row.mark.value !== null
    ? tag(row.mark, lang)
    : t(lang, row.markState.reason === "mark_source_missing" ? "markSourceMissing" : "markNoSource");
  return (
    <li className="position-card">
      <div className="position-head">
        <div>
          <h3>{row.pair || row.symbol || none}</h3>
          <p>{row.botName || none}</p>
          {row.accountName ? <p>{row.accountName}</p> : null}
        </div>
        <div className="position-badges">
          {row.owner === "warp" ? <Badge tone="info">{t(lang, "ownerWarp")}</Badge> : null}
          {row.owner === "external" ? <Badge tone="neutral">{t(lang, "readOnlyExternal")}</Badge> : null}
          {row.owner === null ? <Badge tone="warn">{t(lang, "ownerMissing")}</Badge> : null}
          {row.retained ? <Badge tone="warn">{t(lang, "staleRow")}</Badge> : null}
          {row.incomplete ? <Badge tone="warn">{t(lang, "incompleteRows")}</Badge> : null}
          {row.markState.stale ? <Badge tone="warn">{t(lang, "markStaleBadge")}</Badge> : null}
          {row.side ? (
            <Badge tone={row.side === "long" ? "good" : "bad"}>
              {row.side === "long" ? t(lang, "sideLong") : t(lang, "sideShort")}
            </Badge>
          ) : null}
        </div>
      </div>
      <p className={`position-pnl${tone}`}>
        <span className="position-pnl-label">{t(lang, "unrealizedPnl")}</span>
        <b>{row.pnlUsd.value !== null ? `${fmtSignedMoney(row.pnlUsd.value, lang)} USD` : none}</b>
        <span>{shown(row.pnlPct, lang, (value) => fmtPct(value, lang))}</span>
        {row.pnlUsd.value !== null || row.pnlPct.value !== null ? (
          <small>
            {[row.pnlUsd, row.pnlPct]
              .filter((item) => item.value !== null)
              .map((item) => t(lang, sourceKey(item.source)!))
              .filter((label, index, all) => all.indexOf(label) === index)
              .join(" + ")}
          </small>
        ) : null}
      </p>
      <KV
        rows={[
          [t(lang, "averageEntry"), shown(row.averagePrice, lang, (value) => fmtPrice(value, lang))],
          [t(lang, "currentPrice"), shown(row.mark, lang, (value) => fmtPrice(value, lang)), markHint],
          [
            t(lang, "openSize"),
            row.size.value !== null ? `${shown(row.size, lang, (value) => fmtQty(value, lang))} ${row.baseAsset || ""}`.trim() : none,
          ],
          [t(lang, "soldSize"), none, t(lang, "soldSizeGap")],
          [
            t(lang, "positionCost"),
            row.cost.value !== null ? `${shown(row.cost, lang, (value) => fmtMoney(value, lang))} ${quote}`.trim() : none,
          ],
          [
            t(lang, "safetyOrders"),
            safety === null ? none : `${safety.used ?? none} / ${safety.max ?? none}`,
          ],
          [t(lang, "takeProfitShort"), tps.length ? tps.join(" · ") : none],
          [t(lang, "stopPrice"), shown(row.stop, lang, (value) => fmtPrice(value, lang))],
          [t(lang, "openedAt"), shown(row.openedAt, lang, (value) => fmtTime(value, lang, true))],
          [t(lang, "botLabel"), (row.kind ? t(lang, KIND[row.kind]) : none) + (row.status ? ` · ${row.status}` : "")],
        ]}
      />
      {row.incomplete ? <p className="card-help">{t(lang, "incompleteRowsHelp")}</p> : null}
      {row.rejected.length ? (
        <p className="card-help">
          {t(lang, "rejectedValue")} · {row.rejected.map((name) => (name === "id" ? t(lang, "idMissing") : name)).join(", ")}
        </p>
      ) : null}
      <p className="p4b-tag">{tag(row.averagePrice.value !== null ? row.averagePrice : row.size, lang) || t(lang, "unknown")}</p>
    </li>
  );
}

function OpenPositions({
  lang,
  rows,
  book,
  loading,
  failed,
  complete,
  retry,
  unassigned,
}: {
  lang: Lang;
  rows: PositionView[];
  book: BookView;
  loading: boolean;
  failed: boolean;
  complete: boolean;
  retry: () => void;
  unassigned: boolean;
}) {
  return (
    <Card>
      <CardHead
        icon={Layers}
        title={t(lang, "openPositions")}
        help={t(lang, "positionsHelp")}
        aside={complete ? <Badge tone="info">{rows.length}</Badge> : undefined}
      />
      {failed ? <Problem lang={lang} retry={retry} /> : null}
      {loading && !failed ? <Loading lang={lang} /> : null}
      {unassigned ? <p className="card-help">{t(lang, "capitalUnassigned")}</p> : null}
      {book.brokerReasons.includes("feed_stale") ? <Note tone="bad">{t(lang, "strategiesStale")}</Note> : null}
      {book.errors.length || book.malformedRows || book.duplicateRows ? (
        <div className="p4b-errors" role="group" aria-label={t(lang, "bookErrors")}>
          <p>
            <b>{t(lang, "partialRead")}</b>
          </p>
          <ul>
            {book.errors.map((error) => (
              <li key={error.raw}>
                <Badge tone="warn">{t(lang, ERROR_LABEL[error.kind])}</Badge> <code>{error.raw}</code>
              </li>
            ))}
            {book.malformedRows ? <li>{book.malformedRows} {t(lang, "malformedRows")}</li> : null}
            {book.duplicateRows ? <li>{book.duplicateRows} {t(lang, "duplicateRows")}</li> : null}
          </ul>
        </div>
      ) : null}
      {!loading && !rows.length ? (
        <p className="empty-line">
          {complete ? t(lang, "noOpenPositions") : humanReason(book.error, lang) || t(lang, "unavailable")}
        </p>
      ) : null}
      {rows.length ? (
        <ul className="position-list">
          {rows.map((row) => (
            <PositionCard key={row.key} lang={lang} row={row} />
          ))}
        </ul>
      ) : null}
      <p className="card-help">{t(lang, "readOnlyNote")}</p>
    </Card>
  );
}

function BotsReporting({ lang, rows }: { lang: Lang; rows: PositionView[] }) {
  const groups = botGroups(rows);
  return (
    <Card>
      <CardHead icon={Bot} title={t(lang, "botsReporting")} help={t(lang, "ownedOnly")} />
      <ul className="p4b-bots">
        {groups.map((group) => (
          <li key={group.key}>
            <span className="p4b-bot-name">{group.name || t(lang, "unknown")}</span>
            <span>
              {group.kind ? t(lang, KIND[group.kind]) : t(lang, "unknown")} · {group.rows} {t(lang, "rowsReported")}
            </span>
            <span className="p4b-bot-badges">
              {group.owner === "warp" ? <Badge tone="info">{t(lang, "ownerWarp")}</Badge> : null}
              {group.owner === "external" ? <Badge tone="neutral">{t(lang, "readOnlyExternal")}</Badge> : null}
              {group.owner === null ? <Badge tone="warn">{t(lang, "ownerMissing")}</Badge> : null}
              {group.incomplete ? <Badge tone="warn">{t(lang, "incompleteRows")}</Badge> : null}
            </span>
          </li>
        ))}
      </ul>
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
  const [failed, setFailed] = useState(false);
  // Live frames arrive every few seconds; reset the draft only when the saved values change.
  useEffect(() => {
    setAbsolute(view?.absolute ?? "");
    setFraction(view?.fraction ?? "");
    setMaxBots(view?.max_bots?.toString() ?? "");
  }, [view?.absolute, view?.fraction, view?.max_bots]);
  async function save(event: FormEvent) {
    event.preventDefault();
    setNote("");
    setFailed(false);
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
    } catch (err) {
      setFailed(true);
      setNote(saveFailure(err, lang));
    }
  }

  return (
    <Card>
      <CardHead icon={PiggyBank} title={t(lang, "budget")} help={t(lang, "budgetHelp")} />
      <p className="panel-note setting-note">{t(lang, "settingNotOrder")}</p>
      <p className="panel-note">
        <ReceivedAge at={budget.received} lang={lang} />
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
      {note ? <Note tone={failed ? "bad" : "neutral"}>{note}</Note> : null}
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

  if (accounts.data && "state" in accounts.data && (accounts.data as { state?: string }).state === "postponed") {
    return <Card><CardHead icon={Bot} title={t(lang, "spotPostponed")} help={t(lang, "spotPostponedDetail")} /></Card>;
  }

  return (
    <div className="space-y-3">
      <Card>
        <CardHead
          icon={Bot}
          title={t(lang, "warpBots")}
          help={t(lang, "ownedOnly")}
          aside={<ReceivedAge at={list.received} lang={lang} />}
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
          {status?.mode === "paper" ? (
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
          ) : (
            <p className="card-help">{t(lang, "actionsPaperOnly")}</p>
          )}
          {status?.mode === "paper" && pending?.id === bot.warp_id ? (
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
  if (stats && "state" in stats && (stats as { state?: string }).state === "postponed") {
    return <Card><CardHead icon={Target} title={t(lang, "spotPostponed")} help={t(lang, "spotPostponedDetail")} /></Card>;
  }
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
        <ReceivedAge at={loaded.received} lang={lang} />
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
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setValues(rules.data?.values ?? null);
  }, [rules.data]);
  const decisions = cycle.data?.decisions;
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!values) return;
    setNote("");
    setFailed(false);
    try {
      const saved = await api<{ values: Record<string, string | number> }>("/api/rules", {
        method: "POST",
        body: JSON.stringify(values),
      });
      setValues(saved.values);
      setNote(t(lang, "saved"));
    } catch (err) {
      setFailed(true);
      setNote(saveFailure(err, lang));
    }
  }

  return (
    <div className="form-grid">
      <Card>
        <CardHead icon={ListChecks} title={t(lang, "decisions")} help={t(lang, "decisionsHelp")} />
        <p className="panel-note">
          <ReceivedAge at={cycle.received} lang={lang} />
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
        <p className="panel-note setting-note">{t(lang, "settingNotOrder")}</p>
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
        {note ? <Note tone={failed ? "bad" : "neutral"}>{note}</Note> : null}
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
