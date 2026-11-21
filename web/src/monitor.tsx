import {
  Activity,
  Bot,
  Boxes,
  CircleDollarSign,
  Cpu,
  Crosshair,
  Layers3,
  Radio,
  ScanLine,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import { useState } from "react";
import { api, type Status, type Position, type WarpBot } from "./api";
import { t, type Lang, type Key } from "./i18n";
import { Badge, Button, Card, Field, Loading, Problem, inputClass } from "./ui";
import { useLoad } from "./snapshot";

type StatusRead = ReturnType<typeof useLoad<Status>>;
export function stateLabel(lang: Lang, state?: string) {
  const labels: Record<string, Key> = {
    ready: "ready",
    pass: "pass",
    blocked: "blocked",
    pending: "pending",
    ok: "healthy",
    cooldown: "cooldown",
    unconfigured: "unconfiguredProvider",
  };
  return state ? (labels[state] ? t(lang, labels[state]) : state.replaceAll("_", " ")) : "—";
}
export function Providers({ lang, data }: { lang: Lang; data?: Status | null }) {
  return (
    <div className="providers">
      <h3>{t(lang, "providers")}</h3>
      {data?.providers && Object.keys(data.providers).length ? (
        Object.entries(data.providers).map(([key, provider]) => (
          <div className="provider" key={key}>
            <strong>{key}</strong>
            <Badge tone={provider.state === "ok" ? "good" : "warn"}>{stateLabel(lang, provider.state)}</Badge>
            <small>{[provider.model, provider.detail, provider.until].filter(Boolean).join(" · ")}</small>
          </div>
        ))
      ) : (
        <p>{t(lang, "noProvider")}</p>
      )}
    </div>
  );
}

function RiskControls({ lang, row, reload }: { lang: Lang; row: Status; reload: () => void }) {
  const [action, setAction] = useState<"halt" | "resume" | null>(null);
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Key | null>(null);
  async function send() {
    if (!action || busy || word.trim().toUpperCase() !== action.toUpperCase()) return;
    setBusy(true);
    try {
      await api(action === "halt" ? "/api/pause" : "/api/resume", { method: "POST" });
      setAction(null);
      setNote(action === "halt" ? "haltAck" : "resumeAck");
      reload();
    } catch {
      setNote("error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <h2 className="panel-title">
        <ShieldCheck />
        {t(lang, "control")}
      </h2>
      <div className="ledger">
        <span>{t(lang, "riskGate")}</span>
        <b>{row.halt ? "HALT" : t(lang, row.risk_permitted ? "riskOn" : "riskOff")}</b>
      </div>
      <div className="ledger">
        <span>{t(lang, "execution")}</span>
        <b>{t(lang, row.halt || !row.execution_permitted ? "off" : "on")}</b>
      </div>
      <p className="panel-note">{t(lang, "haltHelp")}</p>
      <div className="action-row">
        {(["halt", "resume"] as const).map((a) => (
          <Button
            key={a}
            variant={a === "halt" ? "danger" : "outline"}
            disabled={busy}
            onClick={() => {
              setAction(a);
              setWord("");
              setNote(null);
            }}
          >
            {t(lang, a)}
          </Button>
        ))}
      </div>
      {action && (
        <form
          className="confirm-box"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <Field label={t(lang, action === "halt" ? "confirmHalt" : "confirmResume")}>
            <input
              autoFocus
              className={inputClass}
              autoComplete="off"
              value={word}
              onChange={(e) => setWord(e.target.value)}
            />
          </Field>
          <div className="action-row">
            <Button disabled={busy || word.trim().toUpperCase() !== action.toUpperCase()}>
              {action.toUpperCase()}
            </Button>
            <Button type="button" variant="ghost" disabled={busy} onClick={() => setAction(null)}>
              {t(lang, "cancel")}
            </Button>
          </div>
        </form>
      )}
      {note && (
        <p role="status" className="panel-note">
          {t(lang, note)}
        </p>
      )}
    </Card>
  );
}

function Pipeline({ lang, row }: { lang: Lang; row: Status }) {
  const p = row.pipeline;
  const stages = [
    { key: "stepScreen", icon: ScanLine, stage: p?.screening, source: p?.screening?.source },
    { key: "stepAnalysis", icon: Cpu, stage: p?.analysis, source: p?.analysis?.mode },
    {
      key: "stepRisk",
      icon: ShieldCheck,
      stage: p?.risk,
      source: p?.risk && "AEGIS",
    },
    {
      key: "stepDesk",
      icon: Crosshair,
      stage: p?.opportunities,
      source: p?.opportunities && t(lang, "research"),
    },
  ] as const;
  return (
    <Card className="pipeline">
      <div className="panel-heading">
        <h2 className="panel-title">
          <Layers3 />
          {t(lang, "pipeline")}
        </h2>
        <small>
          {stages.length} {t(lang, "stages")}
        </small>
      </div>
      <ol>
        {stages.map(({ key, icon: Icon, stage, source }) => (
          <li key={key}>
            <details className="stage-details">
              <summary>
                <span className="stage-icon">
                  <Icon size={19} />
                </span>
                <div className="stage-main">
                  <div className="stage-top">
                    <h3>{t(lang, key)}</h3>
                    <Badge
                      tone={
                        stage?.state === "blocked"
                          ? "bad"
                          : stage?.state === "pending"
                            ? "warn"
                            : stage?.state === "pass" || stage?.state === "ready"
                              ? "good"
                              : "neutral"
                      }
                    >
                      {stateLabel(lang, stage?.state)}
                    </Badge>
                  </div>
                  <div className="stage-meta">
                    <span>{source?.replaceAll("_", " ") || "—"}</span>
                    {stage && "count" in stage && (
                      <span>
                        {t(lang, "outputs")} <b>{stage.count}</b>
                      </span>
                    )}
                    {stage && "reason" in stage && <span>{stage.reason.replaceAll("_", " ")}</span>}
                    {key === "stepScreen" && (
                      <span>{t(lang, row.market_data_fresh ? "fresh" : "stale")}</span>
                    )}
                  </div>
                </div>
                <span className="disclose" aria-hidden="true">
                  ⌄
                </span>
              </summary>
              <dl className="stage-facts">
                <dt>{t(lang, "stageTime")}</dt>
                <dd>{t(lang, "unknown")}</dd>
                <dt>{t(lang, "freshness")}</dt>
                <dd>
                  {key === "stepScreen"
                    ? t(lang, row.market_data_fresh ? "fresh" : "stale")
                    : t(lang, "unknown")}
                </dd>
                <dt>{t(lang, "inputCount")}</dt>
                <dd>—</dd>
                <dt>{t(lang, "outputs")}</dt>
                <dd>{stage && "count" in stage ? stage.count : "—"}</dd>
              </dl>
            </details>
          </li>
        ))}
      </ol>
    </Card>
  );
}

export function Overview({ lang, status }: { lang: Lang; status: StatusRead }) {
  const positions = useLoad<{ positions: Position[] }>("/api/positions", true);
  const bots = useLoad<{ bots: WarpBot[] }>("/api/warp-bots", true);
  if (status.error) return <Problem lang={lang} retry={status.reload} />;
  if (!status.data) return <Loading lang={lang} />;
  const row = status.data;
  const active = bots.data?.bots?.filter((bot) => ["confirmed", "active"].includes(bot.status)).length;
  const metrics = [
    { title: "equity", value: row.equity, icon: Wallet, detail: "accountMark", unit: "USDT" },
    { title: "pnl", value: row.pnl_today, icon: CircleDollarSign, detail: "todayUTC", unit: "USDT" },
    {
      title: "activeBots",
      value: bots.error ? null : active,
      icon: Bot,
      detail: bots.error ? "refreshError" : "owned",
    },
    {
      title: "open",
      value: positions.error ? null : positions.data?.positions?.length,
      icon: Boxes,
      detail: positions.error ? "refreshError" : "listPositions",
    },
  ] as const;
  return (
    <div className="overview">
      <div className="metrics">
        {metrics.map((m) => (
          <Card key={m.title} className="kpi">
            <div>
              <span>{t(lang, m.title)}</span>
              <m.icon size={16} />
            </div>
            <strong>
              {m.value ?? "—"} {"unit" in m && <small>{m.unit}</small>}
            </strong>
            <small>{t(lang, m.detail)}</small>
          </Card>
        ))}
      </div>
      <div className="monitor-grid">
        <div className="monitor-col">
          <Pipeline lang={lang} row={row} />
          <Card>
            <div className="panel-heading">
              <h2 className="panel-title">
                <Radio />
                {t(lang, "coverage")}
              </h2>
              <small>
                {t(lang, "symbols")} · {row.universe?.length ?? "—"}
              </small>
            </div>
            <div className="symbols">
              {row.universe?.length
                ? row.universe.map((symbol) => <code key={symbol}>{symbol}</code>)
                : t(lang, "noSymbols")}
            </div>
          </Card>
        </div>
        <div className="monitor-col">
          <Card>
            <h2 className="panel-title">
              <Activity />
              {t(lang, "system")}
            </h2>
            {(
              [
                ["worker", row.alive],
                ["dependencies", row.dependencies_healthy],
              ] as const
            ).map(([key, value]) => (
              <div className="ledger" key={key}>
                <span>{t(lang, key)}</span>
                <Badge tone={value == null ? "neutral" : value ? "good" : "warn"}>
                  {t(
                    lang,
                    value == null
                      ? "unknown"
                      : value
                        ? key === "worker"
                          ? "online"
                          : "healthy"
                        : "degraded",
                  )}
                </Badge>
              </div>
            ))}
            <Providers lang={lang} data={row} />
          </Card>
          <RiskControls lang={lang} row={row} reload={status.reload} />
        </div>
      </div>
    </div>
  );
}
