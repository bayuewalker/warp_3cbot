import { Html } from "@react-three/drei";
import { fmtPrice, fmtPct } from "../../format.ts";
import { t, type Lang } from "../../i18n.ts";
import { eventLines, fmtWib } from "./office-copy.ts";

export function WallScreen({
  lang, events, notice, quotes,
}: {
  lang: Lang;
  events: unknown;
  notice: string | null;
  quotes: { symbol: string; last: string; change: string | null }[];
}) {
  const lines = eventLines(events);
  return <group position={[0, 1.85, -2.85]}>
    <mesh receiveShadow>
      <boxGeometry args={[6.4, 1.7, 0.06]} />
      <meshLambertMaterial color="#101820" />
    </mesh>
    <Html position={[0, 0, 0.05]} center zIndexRange={[15, 0]}>
      <div className="office-screen" role="region" aria-label={t(lang, "events")}>
        {quotes.length > 0 && <p className="office-quotes">
          {quotes.slice(0, 4).map((quote) => <span key={quote.symbol}>
            {quote.symbol} {fmtPrice(quote.last, lang) ?? quote.last}
            {quote.change ? " " + (fmtPct(quote.change, lang) ?? "") : ""}
          </span>)}
        </p>}
        {notice && <p className="office-empty">{notice}</p>}
        {!notice && lines.length === 0 && <p className="office-empty">{t(lang, "officeNoEvents")}</p>}
        {!notice && lines.length > 0 && <div className="office-ticker-clip">
          <div className="office-ticker">
            {[0, 1].map((copy) => <p key={copy}>
              {lines.map((line, index) => <span key={copy + "-" + index}>
                {(fmtWib(line.ts, lang) ?? t(lang, "unknown")) + " " + line.label}
              </span>)}
            </p>)}
          </div>
        </div>}
      </div>
    </Html>
  </group>;
}
