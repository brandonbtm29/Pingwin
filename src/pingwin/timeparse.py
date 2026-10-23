"""Turn things people type ("fri 8pm", "tomorrow 7:30pm", "in 2 hours") into a date and time."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, tzinfo

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_WORDS = {
    **{d: i for i, d in enumerate(_DAYS)},
    "monday": 0,
    "tues": 1,
    "tuesday": 1,
    "wednesday": 2,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_TIME = re.compile(r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm|a|p)?$")
_REL = re.compile(
    r"in\s+(?:(?P<h>\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours))?\s*(?:(?P<m>\d+)\s*(?:m|min|mins|minute|minutes))?$"
)


def _parse_time(text: str, default_pm: bool = True) -> time | None:
    text = text.strip().lower().replace(" ", "")
    if text in ("noon",):
        return time(12, 0)
    if text in ("midnight",):
        return time(0, 0)
    m = _TIME.match(text)
    if not m:
        return None
    h, mi, ap = int(m["h"]), int(m["m"] or 0), m["ap"]
    if mi > 59:
        return None
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap.startswith("p") else 0)
    elif h <= 12 and default_pm and 1 <= h <= 11 and not m["m"]:
        h += 12  # "8" on its own means 8pm for evening plans
    if h > 23:
        return None
    return time(h, mi)


def next_weekly(text: str, now: datetime) -> datetime | None:
    """The next time a weekly slot like 'Mon 8:30pm' comes around, after `now` (in now's time zone)."""
    words = " ".join(text.strip().lower().replace(",", " ").split()).split(" ", 1)
    if len(words) != 2 or words[0].rstrip("s") not in _DAY_WORDS and words[0] not in _DAY_WORDS:
        return None
    day = _DAY_WORDS.get(words[0], _DAY_WORDS.get(words[0].rstrip("s")))
    t = _parse_time(words[1].replace("at ", ""))
    if t is None:
        return None
    ahead = (day - now.weekday()) % 7
    when = datetime.combine(now.date() + timedelta(days=ahead), t, now.tzinfo)
    if when <= now:
        when += timedelta(days=7)
    return when


def parse_when(text: str, now: datetime) -> datetime | None:
    """Parse a time in now's time zone. Returns None if it can't be understood."""
    tz: tzinfo = now.tzinfo
    s = " ".join(text.strip().lower().replace(",", " ").split())
    if not s:
        return None
    rel = _REL.match(s)
    if rel and (rel["h"] or rel["m"]):
        return now + timedelta(hours=float(rel["h"] or 0), minutes=int(rel["m"] or 0))

    day: date | None = None
    rest = s
    words = s.split(" ", 1)
    first = words[0]
    if first in ("today", "tonight"):
        day, rest = now.date(), words[1] if len(words) > 1 else ("8pm" if first == "tonight" else "")
    elif first in ("tomorrow", "tmrw", "tmr"):
        day, rest = now.date() + timedelta(days=1), words[1] if len(words) > 1 else ""
    elif first in _DAY_WORDS:
        target = _DAY_WORDS[first]
        ahead = (target - now.weekday()) % 7
        day, rest = now.date() + timedelta(days=ahead), words[1] if len(words) > 1 else ""
    else:
        m = re.match(r"(?P<y>\d{4})-(?P<mo>\d{1,2})-(?P<d>\d{1,2})(?:\s+|t)?(?P<rest>.*)$", s) or re.match(
            r"(?P<mo>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?(?:\s+(?P<rest>.*))?$", s
        )
        if m:
            year = int(m["y"]) if m["y"] else now.year
            if year < 100:
                year += 2000
            try:
                day = date(year, int(m["mo"]), int(m["d"]))
            except ValueError:
                return None
            if not m["y"] and day < now.date():
                day = day.replace(year=day.year + 1)
            rest = (m["rest"] or "").strip()

    t = _parse_time(rest.replace("at ", "")) if rest else None
    if rest and t is None:
        return None
    if day is None:
        if t is None:
            return None
        when = datetime.combine(now.date(), t, tz)
        return when if when > now else when + timedelta(days=1)
    if t is None:
        return None  # a day without a time is too vague for a reminder
    when = datetime.combine(day, t, tz)
    if first in _DAY_WORDS and when <= now:
        when += timedelta(days=7)
    return when
