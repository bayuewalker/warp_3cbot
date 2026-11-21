import { useEffect, useState } from "react";
import { Lang, t } from "./i18n";

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
        {t(lang, "unavailable")} · {reason || t(lang, "noTimestamp")}
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
  return (
    <span className={`age ${tone}`}>
      {t(lang, "updated")} {age}s{reason ? ` · ${reason}` : ""}
    </span>
  );
}
