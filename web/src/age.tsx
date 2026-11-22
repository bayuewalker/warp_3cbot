import { useEffect, useState } from "react";
import { Lang, t } from "./i18n";
import { fmtTime } from "./format";

export function Age({
  at,
  limit,
  lang,
  reason,
}: {
  at: string | null | undefined;
  limit: number;
  lang: Lang;
  reason?: string | null;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  if (!at) {
    return (
      <span className="age bad">
        <span className="age-dot" aria-hidden="true" />
        {t(lang, "unavailable")}
        <span className="age-reason"> · {reason || t(lang, "noTimestamp")}</span>
      </span>
    );
  }
  const parsed = Date.parse(at);
  if (Number.isNaN(parsed)) {
    return (
      <span className="age bad">
        {t(lang, "unavailable")} · {t(lang, "noTimestamp")}
      </span>
    );
  }
  const age = Math.max(0, Math.round((now - parsed) / 1000));
  const tone = age > limit ? "bad" : age > limit / 2 ? "warn" : "good";
  const span = age < 90 ? `${age}s` : age < 5400 ? `${Math.round(age / 60)}m` : `${Math.round(age / 3600)}h`;
  return (
    <span className={`age ${tone}`}>
      <span className="age-dot" aria-hidden="true" />
      <time dateTime={at} title={fmtTime(at, lang, true) ?? undefined}>
        {t(lang, "updated")} {span} {t(lang, "updatedAgo")}
      </time>
      {reason ? <span className="age-reason"> · {reason}</span> : null}
    </span>
  );
}
