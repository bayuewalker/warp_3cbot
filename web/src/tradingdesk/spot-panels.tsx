import { useEffect, useState } from "react";
import { Bot, Boxes, ChevronRight, CircleDollarSign, Cpu, Wallet } from "lucide-react";
import type { CardRow, Status } from "../api";
import { fmtMoney, fmtNumber, fmtTime } from "../format";
import { t, type Lang } from "../i18n";
import { useLoad } from "../snapshot";
import { Badge, Card, CardHead } from "../ui";
import { overviewTelemetry } from "./adapters";
import { readLabel } from "./Evidence";
import { instant, sourceInstant } from "./frames";
import { currentPublication } from "./room";

type StatusRead = ReturnType<typeof useLoad<Status>>;

export function SpotResearch({ lang, status, now }: { lang: Lang; status: StatusRead; now: number }) {
  const loaded = useLoad<{ cards: CardRow[] }>("/api/opportunities", true);
  const card = loaded.data?.cards?.[0];
  const [checkedAt, setCheckedAt] = useState(0);
  const clock = Math.max(now, checkedAt);
  const current = !status.issue && currentPublication(status.data?.published_at, clock);
  const evidenceAt = sourceInstant(card?.evidence_as_of, clock);
  const signalAt = sourceInstant(card?.as_of, clock);
  const expiresAt = instant(card?.expires_at);
  const expired = expiresAt !== null && clock > expiresAt;
  const fresh = current && !loaded.issue && card?.fresh === true && signalAt !== null &&
    evidenceAt !== null && expiresAt !== null && !expired;
  useEffect(() => {
    if (!fresh || expiresAt === null) return;
    const delay = Math.max(0, expiresAt + 1 - Date.now());
    if (delay > 2_147_483_647) return;
    const timer = window.setTimeout(() => setCheckedAt(Date.now()), delay);
    return () => window.clearTimeout(timer);
  }, [fresh, expiresAt]);
  const verdict = card && ["BUY", "WAIT", "SKIP"].includes(card.action) ? card.action : t(lang, "unknown");
  const state = loaded.issue ? t(lang, "refreshError") : !loaded.data ? t(lang, "loading") :
    !current || card?.fresh === false || expired ? t(lang, "readStale") : !card ? t(lang, "readEmpty") :
    fresh ? t(lang, "readFresh") : t(lang, "freshnessUnknown");
  return <Card className="desk-research">
    <CardHead icon={Cpu} title={t(lang, "deskResearch")} aside={<Badge tone={fresh ? "info" : "neutral"}>{state}</Badge>} />
    {card ? <div className="desk-research-body">
      <strong>{card.symbol} · {verdict}</strong>
      <p>{(lang === "id" ? card.why_id : card.thesis) || t(lang, "unknown")}</p>
      <small>{t(lang, "marketTime")} · {fmtTime(evidenceAt, lang, true) ?? t(lang, "unknown")}</small>
    </div> : loaded.data && !loaded.issue ? <p>{t(lang, "deskNoResearch")}</p> : null}
    <a className="desk-link" href="#signals">{t(lang, "signals")} <ChevronRight size={16} /></a>
  </Card>;
}

export function SpotMetrics({ lang, model }: { lang: Lang; model: ReturnType<typeof overviewTelemetry> }) {
  const metrics = [
    { title: "equity", icon: Wallet, detail: "accountMark", unit: "USDT" },
    { title: "pnl", icon: CircleDollarSign, detail: "todayUTC", unit: "USDT" },
    { title: "activeBots", icon: Bot, detail: "owned" },
    { title: "open", icon: Boxes, detail: "listPositions" },
  ] as const;
  return (
    <div className="metrics">
      {metrics.map((m) => {
        const observation = model.metrics[m.title];
        const value = observation.value;
        return (
          <Card key={m.title} className="kpi">
            <div>
              <span>{t(lang, m.title)}</span>
              <m.icon size={16} />
            </div>
            <strong className={value == null ? "kpi-missing" : undefined}>
              {value == null
                ? t(lang, "unknown")
                : "unit" in m
                  ? fmtMoney(value, lang) ?? t(lang, "unknown")
                  : fmtNumber(value, lang, 0)}{" "}
              {value != null && "unit" in m && <small>{m.unit}</small>}
            </strong>
            <small>{t(lang, m.detail)} · {readLabel(lang, observation.state)}</small>
          </Card>
        );
      })}
    </div>
  );
}
