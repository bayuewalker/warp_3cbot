import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { Activity } from "lucide-react";
import { fmtPct, fmtPrice, fmtTime } from "../format";
import { t, type Lang } from "../i18n";
import { useLoad } from "../snapshot";
import { readLabel } from "./Evidence";
import type { Status } from "../api";
import { deskPrices, quoteTrace, retainQuotes, type QuoteHistory } from "./quotes";

const XauQuote = lazy(() => import("./xau-desk").then(mod => ({ default: mod.XauQuote })));

function QuoteTrace({ trace, lang }: { trace: ReturnType<typeof quoteTrace>; lang: Lang }) {
  if (!trace) return <p className="market-wait">{t(lang, "awaitDeskQuotes")}</p>;
  const { line, low, high, start, end } = trace;
  return <figure className="market-trace">
    <svg viewBox="0 0 500 80" preserveAspectRatio="none" role="img"
      aria-label={t(lang, "deskQuoteHistory") + " · " + fmtPrice(low, lang) + " – " + fmtPrice(high, lang)}>
      <polyline points={line} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
    <figcaption><time>{fmtTime(start, lang)}</time><time>{fmtTime(end, lang)}</time></figcaption>
  </figure>;
}

export function MarketWall({ lang, now, status, children }: {
  lang: Lang;
  now: number;
  status?: Status | null;
  children?: (panel: ReactNode, monitorLine: string | null) => ReactNode;
}) {
  const loaded = useLoad("/api/prices", true);
  const prices = deskPrices(loaded, now);
  const [selected, select] = useState("");
  const [history, setHistory] = useState<QuoteHistory>({});
  useEffect(() => {
    const observation = deskPrices({ data: loaded.data, received: loaded.received, issue: loaded.issue }, Date.now());
    setHistory(previous => retainQuotes(previous, observation));
  }, [loaded.data, loaded.issue, loaded.received]);
  const xauOn = status?.lanes?.primary === "xau";
  const list = prices.value ?? [];
  const symbols = xauOn
    ? ["XAUUSD", ...list.map(item => item.symbol).filter(symbol => symbol !== "XAUUSD")]
    : list.map(item => item.symbol);
  const choice = selected || (xauOn ? "XAUUSD" : list[0]?.symbol ?? "");
  const quote = list.find(item => item.symbol === choice) ?? null;
  const current = prices.state === "confirmed" && prices.freshness === "fresh";
  const state = current ? t(lang, "readFresh") : readLabel(lang, prices.state);
  const points = quote && current ? history[quote.symbol] : undefined;
  const trace = points && prices.sourceAt && points.at(-1)?.time === Date.parse(prices.sourceAt)
    ? quoteTrace(points) : null;
  const sourceAt = prices.sourceAt;
  if (choice === "XAUUSD" && xauOn) {
    const panel = <Suspense fallback={null}>
      <XauQuote lang={lang} now={now} status={status} symbols={symbols} onSelect={select} />
    </Suspense>;
    return children ? <>{children(panel, null)}</> : panel;
  }
  const panel = <section className="market-wall" aria-labelledby="market-wall-title">
    <div className="market-wall-head">
      <h3 id="market-wall-title"><Activity size={15} />{t(lang, "priceTape")} <small>Binance</small></h3>
      <span data-fresh={current}>{state}</span>
    </div>
    {quote ? <>
      <div className="market-quote">
        <label><span className="sr-only">{t(lang, "symbols")}</span>
          <select value={quote.symbol} onChange={event => select(event.target.value)}>
            {symbols.map(symbol => <option key={symbol}>{symbol}</option>)}
          </select>
        </label>
        <div><strong>{fmtPrice(quote.last, lang)}</strong>
          <small className={current && quote.change ? Number(quote.change) > 0 ? "up" : "down" : undefined}>
            {fmtPct(quote.change, lang) ?? t(lang, "unknown")}
          </small></div>
      </div>
      {current && <QuoteTrace trace={trace} lang={lang} />}
    </> : <p className="market-wait">{t(lang, prices.state === "loading" ? "loading" : "noPrices")}</p>}
    <footer>{t(lang, "sourceTime")} · {fmtTime(sourceAt, lang, true) ?? t(lang, "unknown")}</footer>
  </section>;
  return children ? <>{children(panel, trace?.line ?? null)}</> : panel;
}
