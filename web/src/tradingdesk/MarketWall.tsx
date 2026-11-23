import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import { fmtPct, fmtPrice, fmtTime } from "../format";
import { t, type Lang } from "../i18n";
import { useLoad } from "../snapshot";
import { readLabel } from "./Evidence";
import { deskPrices, retainQuotes, type QuoteHistory, type QuotePoint } from "./quotes";

function QuoteTrace({ points, lang }: { points: QuotePoint[]; lang: Lang }) {
  const first = points[0], last = points.at(-1);
  if (!first || !last || points.length < 2) return <p className="market-wait">{t(lang, "awaitDeskQuotes")}</p>;
  const low = Math.min(...points.map(point => point.last));
  const high = Math.max(...points.map(point => point.last));
  const line = points.map(point => {
    const x = (point.time - first.time) / (last.time - first.time) * 480 + 10;
    const y = low === high ? 40 : 72 - (point.last - low) / (high - low) * 64;
    return x + "," + y;
  }).join(" ");
  return <figure className="market-trace">
    <svg viewBox="0 0 500 80" preserveAspectRatio="none" role="img"
      aria-label={t(lang, "deskQuoteHistory") + " · " + fmtPrice(low, lang) + " – " + fmtPrice(high, lang)}>
      <polyline points={line} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
    <figcaption><time>{fmtTime(first.time, lang)}</time><time>{fmtTime(last.time, lang)}</time></figcaption>
  </figure>;
}

export function MarketWall({ lang, now }: { lang: Lang; now: number }) {
  const loaded = useLoad("/api/prices", true);
  const prices = deskPrices(loaded, now);
  const [selected, select] = useState("");
  const [history, setHistory] = useState<QuoteHistory>({});
  useEffect(() => {
    const observation = deskPrices({ data: loaded.data, received: loaded.received, issue: loaded.issue }, Date.now());
    setHistory(previous => retainQuotes(previous, observation));
  }, [loaded.data, loaded.issue, loaded.received]);
  const list = prices.value ?? [];
  const quote = list.find(item => item.symbol === selected) ?? list[0];
  const current = prices.state === "confirmed" && prices.freshness === "fresh";
  const state = current ? t(lang, "readFresh") : readLabel(lang, prices.state);
  return <section className="market-wall" aria-labelledby="market-wall-title">
    <div className="market-wall-head">
      <h3 id="market-wall-title"><Activity size={15} />{t(lang, "priceTape")} <small>Binance</small></h3>
      <span data-fresh={current}>{state}</span>
    </div>
    {quote ? <>
      <div className="market-quote">
        <label><span className="sr-only">{t(lang, "symbols")}</span>
          <select value={quote.symbol} onChange={event => select(event.target.value)}>
            {list.map(item => <option key={item.symbol}>{item.symbol}</option>)}
          </select>
        </label>
        <div><strong>{fmtPrice(quote.last, lang)}</strong>
          <small className={current && quote.change ? Number(quote.change) > 0 ? "up" : "down" : undefined}>
            {fmtPct(quote.change, lang) ?? t(lang, "unknown")}
          </small></div>
      </div>
      {current && <QuoteTrace points={history[quote.symbol] ?? []} lang={lang} />}
    </> : <p className="market-wait">{t(lang, prices.state === "loading" ? "loading" : "noPrices")}</p>}
    <footer>{t(lang, "sourceTime")} · {fmtTime(prices.sourceAt, lang, true) ?? t(lang, "unknown")}</footer>
  </section>;
}
