"""News gate. Rulebook §7 N1–N6. FF is polled at most once an hour; the TOML is the backstop."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .clock import WIB, wib, wib_date

TIER1 = re.compile(r"CPI|Non-Farm|NFP|FOMC|Federal Funds|PCE|GDP|Unemployment Claims", re.IGNORECASE)

_STATIC_TOML = """# Static tier-1 backstop, 2026-12-01 through 2027-06-30.
# FOMC decision 14:00 America/New_York: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# CPI 08:30 America/New_York: https://www.bls.gov/schedule/news_release/cpi.htm
# Non-Farm Payrolls 08:30 America/New_York: https://www.bls.gov/schedule/news_release/empsit.htm
# GDP and PCE 08:30 America/New_York: https://www.bea.gov/news/schedule
coverage_start = "2026-12-01"
coverage_end = "2027-06-30"

[[event]]
title = "Non-Farm Payrolls"
at = "2026-12-04T08:30:00-05:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/empsit.htm"

[[event]]
title = "FOMC"
at = "2026-12-09T14:00:00-05:00"
impact = "High"
source = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

[[event]]
title = "CPI"
at = "2026-12-10T08:30:00-05:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/cpi.htm"

[[event]]
title = "GDP"
at = "2026-12-23T08:30:00-05:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "PCE"
at = "2026-12-23T08:30:00-05:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "Non-Farm Payrolls"
at = "2027-01-08T08:30:00-05:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/empsit.htm"

[[event]]
title = "CPI"
at = "2027-01-13T08:30:00-05:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/cpi.htm"

[[event]]
title = "FOMC"
at = "2027-01-27T14:00:00-05:00"
impact = "High"
source = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

[[event]]
title = "GDP"
at = "2027-01-28T08:30:00-05:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "PCE"
at = "2027-01-28T08:30:00-05:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "Non-Farm Payrolls"
at = "2027-02-05T08:30:00-05:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/empsit.htm"

[[event]]
title = "CPI"
at = "2027-02-11T08:30:00-05:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/cpi.htm"

[[event]]
title = "GDP"
at = "2027-02-25T08:30:00-05:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "PCE"
at = "2027-02-25T08:30:00-05:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "Non-Farm Payrolls"
at = "2027-03-05T08:30:00-05:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/empsit.htm"

[[event]]
title = "CPI"
at = "2027-03-10T08:30:00-05:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/cpi.htm"

[[event]]
title = "FOMC"
at = "2027-03-17T14:00:00-04:00"
impact = "High"
source = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

[[event]]
title = "GDP"
at = "2027-03-31T08:30:00-04:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "PCE"
at = "2027-03-31T08:30:00-04:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "Non-Farm Payrolls"
at = "2027-04-02T08:30:00-04:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/empsit.htm"

[[event]]
title = "CPI"
at = "2027-04-13T08:30:00-04:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/cpi.htm"

[[event]]
title = "FOMC"
at = "2027-04-28T14:00:00-04:00"
impact = "High"
source = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

[[event]]
title = "GDP"
at = "2027-04-29T08:30:00-04:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "PCE"
at = "2027-04-29T08:30:00-04:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "Non-Farm Payrolls"
at = "2027-05-07T08:30:00-04:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/empsit.htm"

[[event]]
title = "CPI"
at = "2027-05-12T08:30:00-04:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/cpi.htm"

[[event]]
title = "GDP"
at = "2027-05-27T08:30:00-04:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "PCE"
at = "2027-05-27T08:30:00-04:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "Non-Farm Payrolls"
at = "2027-06-04T08:30:00-04:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/empsit.htm"

[[event]]
title = "FOMC"
at = "2027-06-09T14:00:00-04:00"
impact = "High"
source = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

[[event]]
title = "CPI"
at = "2027-06-10T08:30:00-04:00"
impact = "High"
source = "https://www.bls.gov/schedule/news_release/cpi.htm"

[[event]]
title = "GDP"
at = "2027-06-25T08:30:00-04:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"

[[event]]
title = "PCE"
at = "2027-06-25T08:30:00-04:00"
impact = "High"
source = "https://www.bea.gov/news/schedule"
"""


@dataclass(frozen=True)
class Release:
    title: str
    at: datetime
    impact: str
    country: str


@dataclass(frozen=True)
class NewsView:
    releases: tuple[Release, ...]
    fresh: bool
    covers_today: bool
    blocked: bool
    source: str

    def tier1(self) -> tuple[Release, ...]:
        return tuple(
            item
            for item in self.releases
            if item.country.upper() == "USD"
            and (item.impact.lower() == "high" or TIER1.search(item.title))
        )


def static_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "news_static.toml"


def load_static_text() -> str:
    path = static_path()
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return _STATIC_TOML


def parse_static(text: str) -> tuple[datetime, datetime, tuple[Release, ...]]:
    raw = tomllib.loads(text)
    start = date.fromisoformat(str(raw["coverage_start"]))
    end = date.fromisoformat(str(raw["coverage_end"]))
    events = []
    for row in raw.get("event", []):
        moment = datetime.fromisoformat(str(row["at"]))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        events.append(Release(str(row["title"]), moment, str(row.get("impact", "High")), "USD"))
    return (
        datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
        datetime(end.year, end.month, end.day, tzinfo=timezone.utc),
        tuple(events),
    )


def parse_ff(payload: object) -> tuple[Release, ...]:
    """Forex Factory weekly JSON. ``date`` is ISO with a numeric offset."""
    if not isinstance(payload, list):
        return ()
    found: list[Release] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            moment = datetime.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        found.append(
            Release(
                str(row.get("title") or ""),
                moment,
                str(row.get("impact") or ""),
                str(row.get("country") or ""),
            )
        )
    return tuple(found)


class NewsBook:
    def __init__(self, max_age_h: int = 24):
        self.max_age = timedelta(hours=max_age_h)
        self.fetched_at: datetime | None = None
        self.ff: tuple[Release, ...] = ()
        self._static_start, self._static_end, self.static = _safe_static()

    def note_fetch(self, moment: datetime, payload: object) -> None:
        self.fetched_at = moment
        self.ff = parse_ff(payload)

    def due(self, moment: datetime) -> bool:
        if self.fetched_at is None:
            return True
        return moment - self.fetched_at >= timedelta(hours=1)

    def view(self, moment: datetime) -> NewsView:
        today = wib_date(moment)
        covers = self._static_start.date() <= today <= self._static_end.date()
        fresh = self.fetched_at is not None and moment - self.fetched_at <= self.max_age
        if fresh:
            releases = self.ff
            source = "ff"
        elif covers:
            releases = self.static
            source = "static"
        else:
            releases = ()
            source = "none"
        blocked = not fresh and not covers
        return NewsView(releases, fresh, covers, blocked, source)

    def in_window(self, moment: datetime, engine: str) -> Release | None:
        """N2. Returns the release whose no-new-signal window contains ``moment``."""
        view = self.view(moment)
        for release in view.tier1():
            if engine == "SMC" and release.at - timedelta(minutes=30) <= moment <= release.at + timedelta(minutes=20):
                return release
            if engine == "EMA" and release.at - timedelta(minutes=30) <= moment <= release.at + timedelta(minutes=15):
                return release
        return None

    def cancel_pending(
        self,
        moment: datetime,
        valid_until: datetime | None,
        created_at: datetime | None = None,
    ) -> bool:
        """N3. Cancel at T−30 when a tier-1 release falls inside this signal's life, before expiry."""
        if valid_until is None:
            return False
        for release in self.view(moment).tier1():
            trigger = release.at - timedelta(minutes=30)
            if trigger > moment or release.at > valid_until:
                continue
            # A release that already printed before the signal existed is not this signal's news.
            if created_at is not None and release.at <= created_at:
                continue
            return True
        return False

    def next_tier1(self, moment: datetime) -> Release | None:
        upcoming = [item for item in self.view(moment).tier1() if item.at >= moment]
        if not upcoming:
            return None
        return min(upcoming, key=lambda item: item.at)

    def scalp_window_blocked(self, moment: datetime) -> bool:
        """N2. A tier-1 release inside the NY scalp window blocks scalp for that whole window."""
        local = wib(moment)
        start = local.replace(hour=20, minute=15, second=0, microsecond=0)
        end_minute = 21 * 60 + 45 if local.weekday() == 4 else 22 * 60 + 15
        end = local.replace(hour=end_minute // 60, minute=end_minute % 60, second=0, microsecond=0)
        for release in self.view(moment).tier1():
            at = release.at.astimezone(WIB)
            if start <= at < end:
                return True
        return False

    def smc_rearm_needed(self, moment: datetime, last_structure_at: datetime | None) -> bool:
        """N5. After T+20 a fresh M15 BOS or CHoCH must print before a new SMC signal."""
        for release in self.view(moment).tier1():
            reopen = release.at + timedelta(minutes=20)
            if moment >= reopen and (last_structure_at is None or last_structure_at < reopen):
                # Only the most recent release that has reopened matters.
                if moment - release.at < timedelta(hours=12):
                    return True
        return False


def _safe_static() -> tuple[datetime, datetime, tuple[Release, ...]]:
    try:
        return parse_static(load_static_text())
    except (OSError, tomllib.TOMLDecodeError, KeyError, ValueError):
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return epoch, epoch, ()


def dumps_ff(releases: tuple[Release, ...]) -> str:
    return json.dumps(
        [
            {
                "title": item.title,
                "country": item.country,
                "date": item.at.isoformat(),
                "impact": item.impact,
            }
            for item in releases
        ]
    )
