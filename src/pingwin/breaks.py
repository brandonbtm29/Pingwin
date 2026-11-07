"""Breaks: weeks a movie night series skips (between franchises, holidays, a week off).

A series' breaks are stored with its movie night state as a list of {"from": date, "until": date, "why": text,
"by": name}, both dates included. A showing on a date inside a break doesn't happen: no poll closes on it, no
franchise part is announced or asked about, the voice check doesn't watch it, and Pingwin moves the series'
repeating Discord Event to the first showing after the break, so everyone sees the real next date.

The helpers here are pure, so they're easy to test; the movie night and event cogs do the Discord parts.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .timeparse import next_weekly

MAX_WEEKS = 26  # half a year is plenty


def _d(text: Any) -> date | None:
    try:
        return date.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def covering(breaks: list[dict[str, Any]] | None, day: date) -> dict[str, Any] | None:
    """The break that `day` falls in, if any."""
    for b in breaks or []:
        start, end = _d(b.get("from")), _d(b.get("until"))
        if start and end and start <= day <= end:
            return b
    return None


def next_showing(showtime: str, after: datetime, breaks: list[dict[str, Any]] | None) -> datetime | None:
    """The first showing after `after` that isn't in a break (None without a showtime)."""
    when = next_weekly(showtime, after) if showtime else None
    for _ in range(MAX_WEEKS * 4):
        if when is None or covering(breaks, when.date()) is None:
            return when
        when = next_weekly(showtime, when + timedelta(minutes=1))
    return None


def last_showing(showtime: str, now: datetime) -> datetime | None:
    """The most recent showing by the schedule (break or not) at or before `now`."""
    when = next_weekly(showtime, now - timedelta(days=7)) if showtime else None
    if when is not None and when > now:
        when = next_weekly(showtime, now - timedelta(days=14))
    return when


def plan(showtime: str, start: datetime, weeks: int, breaks: list[dict[str, Any]] | None) -> tuple[date, date] | None:
    """The dates of a break of `weeks` showings, starting with the first showing at or after `start` that
    isn't already skipped. (first skipped date, last skipped date), or None without a showtime."""
    weeks = max(1, min(MAX_WEEKS, int(weeks)))
    first = next_showing(showtime, start - timedelta(minutes=1), breaks)
    if first is None:
        return None
    last = first
    for _ in range(weeks - 1):
        nxt = next_weekly(showtime, last + timedelta(minutes=1))
        if nxt is None:
            break
        last = nxt
    return first.date(), last.date()


def add(breaks: list[dict[str, Any]], start: date, end: date, why: str = "", by: str = "") -> list[dict[str, Any]]:
    """Breaks with a new one added. Breaks that overlap or have no showing between them (a week or less apart)
    are merged into one."""
    new = {"from": start.isoformat(), "until": end.isoformat(), "why": why[:100], "by": by[:60]}
    out = []
    for b in breaks:
        s, e = _d(b.get("from")), _d(b.get("until"))
        if s is None or e is None:
            continue
        if s <= end + timedelta(days=7) and start <= e + timedelta(days=7):  # overlap, or back to back
            start, end = min(start, s), max(end, e)
            new.update({"from": start.isoformat(), "until": end.isoformat()})
            new["why"] = new["why"] or b.get("why", "")
            new["by"] = new["by"] or b.get("by", "")
        else:
            out.append(b)
    out.append(new)
    return sorted(out, key=lambda b: b["from"])


def end(breaks: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """End the current break and drop the coming ones: what's left are breaks already over."""
    return [b for b in breaks if (_d(b.get("until")) or today) < today]


def prune(breaks: list[dict[str, Any]], today: date, keep_days: int = 60) -> list[dict[str, Any]]:
    """Drop breaks that ended a while ago."""
    return [b for b in breaks if (_d(b.get("until")) or today) >= today - timedelta(days=keep_days)]


def upcoming(breaks: list[dict[str, Any]] | None, today: date) -> list[dict[str, Any]]:
    """Breaks that are on now or still to come, soonest first."""
    return sorted((b for b in breaks or [] if (_d(b.get("until")) or today) >= today), key=lambda b: b["from"])


def describe(b: dict[str, Any], back: datetime | None) -> str:
    """'Off from Fri, Nov 13 to Fri, Dec 4 (between franchises); back Fri, Dec 11.'"""
    s, e = _d(b["from"]), _d(b["until"])
    span = f"{_day(s)}" if s == e else f"{_day(s)} to {_day(e)}"
    why = f" ({b['why']})" if b.get("why") else ""
    return f"Off {span}{why}" + (f"; back {_day(back.date())}." if back else ".")


def _day(d: date | None) -> str:
    return f"{d:%a}, {d:%b} {d.day}" if d else "?"
