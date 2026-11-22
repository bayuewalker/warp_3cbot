import { fmtTime } from "../format";
import { t, type Key, type Lang } from "../i18n";
import type { overviewTelemetry } from "./adapters";
import type { ObservedValue, ReadState } from "./types";

const states: Record<ReadState, Key> = {
  confirmed: "readConfirmed", confirmed_empty: "readEmpty", stale: "readStale", partial: "readPartial",
  loading: "loading", awaiting_first_snapshot: "awaitingWorker", unavailable: "unavailable",
  unauthorized: "sessionExpired", error: "refreshError",
};
const sources: Record<ObservedValue<unknown>["source"], Key> = {
  worker: "worker", derived: "fromComputed", threecommas: "from3c", binance: "fromBinance",
};
const outcomes: Record<string, Key> = {
  ready: "ready", pending: "pending", blocked: "blocked", pass: "pass", halt: "halt", observe: "observe",
  permitted: "gatePermitted", reported: "readConfirmed", empty: "readEmpty",
};
const reasons: Record<string, Key> = {
  source_time_unavailable: "noTimestamp", incomplete_book: "incompleteBook",
  malformed_payload: "invalidRead", request_failed: "refreshError", awaiting_first_snapshot: "awaitingWorker",
  not_loaded: "awaitingWorker", unavailable: "unavailable", loading: "loading",
};

export function readLabel(lang: Lang, state: ReadState) {
  return t(lang, states[state]);
}

export function Evidence({ lang, model }: { lang: Lang; model: ReturnType<typeof overviewTelemetry> }) {
  const metrics = Object.entries(model.metrics).map(([key, item]) => ({ label: t(lang, key as Key), item, outcome: null }));
  const roles = Object.entries(model.agents).map(([label, item]) => ({
    label, item, outcome: item.value ? t(lang, outcomes[item.value]) : null,
  }));
  return (
    <details className="surface telemetry">
      <summary>{t(lang, "telemetryEvidence")}</summary>
      <dl>
        {[...metrics, ...roles].map(({ label, item, outcome }) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              <strong>{outcome && item.state !== "confirmed_empty" ? outcome + " · " : ""}{readLabel(lang, item.state)}</strong>
              <span>{t(lang, "source")} · {t(lang, sources[item.source])}</span>
              <span>{t(lang, "sourceTime")} · {fmtTime(item.sourceAt, lang, true) || t(lang, "unknown")}</span>
              <span>{t(lang, "clientReceipt")} · {fmtTime(item.receivedAt, lang, true) || t(lang, "unknown")}</span>
              <span>{t(lang, "freshness")} · {t(lang, item.freshness === "fresh" ? "readFresh" : item.freshness === "stale" ? "readStale" : "unknown")}</span>
              {item.reason && <span>{t(lang, "reason")} · {reasons[item.reason] ? t(lang, reasons[item.reason]) : item.reason.replaceAll("_", " ")}</span>}
              {item.traceId && <span>Trace ID · {item.traceId}</span>}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
