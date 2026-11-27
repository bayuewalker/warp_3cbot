import { Activity, CandlestickChart } from "lucide-react";
import type { Status } from "../api";
import { fmtPct, fmtPrice, fmtTime } from "../format";
import { t, type Lang } from "../i18n";
import { Card, CardHead } from "../ui";
import { xauCard, xauLabels, xauReason, xauTape } from "./xau-lane";

export function XauDesk({ lang, row }: { lang: Lang; row: Status }) {
  const card = xauCard(row);
  const copy = xauLabels(lang);
  const warming = card.reason === "warming" || card.state === "warming" || card.funnel.some(item => item.reason === "warming_up");
  const basis = warming && card.basisSamples !== null
    ? `${xauReason(lang, "warming")} (${card.basisSamples}/10)`
    : card.basis === "unknown" ? t(lang, "unknown") : card.basis;
  const signal = card.open ? card.signal : copy.none;
  const today = card.sent === "unknown"
    ? t(lang, "unknown")
    : `${copy.sent} ${card.sent} · ${copy.journal} ${card.journal} · R ${card.r}`;
  const funnel = card.funnel.length
    ? card.funnel.map(item => xauReason(lang, item.reason) + (item.count > 1 ? ` × ${item.count}` : "")).join(", ")
    : t(lang, "unknown");
  const closed = card.spotState === "market_closed";
  const spot = card.spot === "unknown" ? t(lang, "unknown") : card.spot;
  return <Card>
    <CardHead icon={CandlestickChart} title={copy.desk} help={copy.source}
      aside={<time>{fmtTime(card.updated, lang, true) ?? t(lang, "unknown")}</time>} />
    <ul className="symbols">
      <li>{t(lang, "ready")}: {card.ready ? t(lang, "ready") : xauReason(lang, card.reason)}</li>
      <li>{t(lang, "officeXauLive")}: {card.window} · {card.engine}</li>
      <li>{copy.next}: {card.next}</li>
      <li>{closed ? copy.closed : copy.source}: {spot}</li>
      <li>{copy.basis}: {basis}</li>
      <li>{signal}</li>
      <li>{copy.today}: {today}</li>
      <li>{copy.funnel}: {funnel}</li>
    </ul>
  </Card>;
}

export function XauQuote({ lang, now, status, symbols, onSelect }: {
  lang: Lang;
  now: number;
  status?: Status | null;
  symbols: string[];
  onSelect: (symbol: string) => void;
}) {
  const tape = xauTape(status, now);
  const copy = xauLabels(lang);
  const fresh = tape.fresh === true;
  const state = tape.state === "market_closed" ? copy.closed : tape.state === "stale" ? t(lang, "readStale") : fresh ? t(lang, "readFresh") : t(lang, "unknown");
  const closed = tape.state === "market_closed";
  return <section className="market-wall" aria-labelledby="market-wall-title">
    <div className="market-wall-head">
      <h3 id="market-wall-title"><Activity size={15} />{t(lang, "priceTape")} <small>{copy.source}</small></h3>
      <span data-fresh={fresh}>{state}</span>
    </div>
    {tape.last ? <div className="market-quote">
      <label><span className="sr-only">{t(lang, "symbols")}</span>
        <select value="XAUUSD" onChange={event => onSelect(event.target.value)}>
          {symbols.map(symbol => <option key={symbol}>{symbol}</option>)}
        </select>
      </label>
      <div><strong>{fmtPrice(tape.last, lang)}</strong>
        <small>{fmtPct(null, lang) ?? t(lang, "unknown")}</small></div>
    </div> : <>
      <label><span className="sr-only">{t(lang, "symbols")}</span>
        <select value="XAUUSD" onChange={event => onSelect(event.target.value)}>
          {symbols.map(symbol => <option key={symbol}>{symbol}</option>)}
        </select>
      </label>
      <p className="market-wait">{copy.unavailable} · {tape.reason ?? t(lang, "unknown")}</p>
    </>}
    <footer>{(closed ? copy.closed + " · " + copy.last : t(lang, "sourceTime"))} · {fmtTime(tape.at, lang, true) ?? t(lang, "unknown")}</footer>
  </section>;
}
