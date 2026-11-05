"""Franchise queues: a series (like Franchise Fridays) works through one franchise's movies in order.

Each showtime, Pingwin announces the next part ("Alien 3 (1992), part 3 of 4"), logs it in the history
when the movie starts (so its Rate it button still comes), and moves on. When the franchise is done,
it's time for a poll to pick the next one.

A queue lives in the series' movie night state:
    {"name": "Alien", "items": [{"id", "title", "year", "done": "2026-10-30" | None, "skipped": bool}],
     "paused": bool, "announced_for": date, "logged_for": date, "postponed_for": date}

A franchise that wins a poll while the current one still has parts left waits in the series' state as
"next_franchise", and starts once the current one is done.
"""

from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any

from .movies import DONE_MARKS, TODO_MARKS, norm, parse_list_message

_NUMBERED = re.compile(r"^\s*(?:part\s*)?\d{1,3}\s*[.):-]\s*", re.I)


def parse_items(text: str) -> list[dict[str, Any]]:
    """Movies in order from pasted lines ('1. Alien (1979)', '✅ Aliens 1986', 'Alien 3'). Every line is a movie;
    ✅ marks one already watched."""
    rows = []
    for line in text.splitlines():
        line = _NUMBERED.sub("", line.strip()).strip()
        done = line.startswith(DONE_MARKS)
        for mark in DONE_MARKS + TODO_MARKS:
            if line.startswith(mark):
                line = _NUMBERED.sub("", line[len(mark) :].strip()).strip()
                break
        if not line:
            continue
        parsed = parse_list_message("🇽 " + line)  # one line, always counted
        if parsed:
            rows.append({**parsed[0], "done": done})
    return [
        {
            "id": secrets.token_hex(4),
            "title": r["title"],
            "year": r["year"],
            "done": "earlier" if r["done"] else None,
            "skipped": False,
        }
        for r in rows
    ]


def new_queue(name: str, items: list[dict[str, Any]] | None = None, paused: bool = False) -> dict[str, Any]:
    return {"name": name.strip()[:100], "items": items or [], "paused": paused, "started": date.today().isoformat()}


def counted(queue: dict[str, Any]) -> list[dict[str, Any]]:
    """The parts that count (skipped ones don't)."""
    return [i for i in queue.get("items", []) if not i.get("skipped")]


def next_part(queue: dict[str, Any] | None) -> dict[str, Any] | None:
    if not queue:
        return None
    return next((i for i in counted(queue) if not i.get("done")), None)


def part_number(queue: dict[str, Any], item: dict[str, Any]) -> tuple[int, int]:
    """(part, total) for an item, e.g. (7, 8)."""
    parts = counted(queue)
    return parts.index(item) + 1, len(parts)


def label(item: dict[str, Any]) -> str:
    return f"{item['title']} ({item['year']})" if item.get("year") else item["title"]


def finished(queue: dict[str, Any] | None) -> bool:
    return bool(queue and counted(queue) and next_part(queue) is None)


def due(queue: dict[str, Any], showtime: datetime, now: datetime, announce_hours: float) -> str | None:
    """What to do now for this week's showtime: "announce", "note", "log", "cancel" or None.

    `showtime` is the next showing after `now`; last week's showing is a week earlier.
    "note" is for when no announcement is wanted (`announce_hours` is 0): it just notes the coming showing,
    so the part is still logged when it starts. "cancel" means an announced showing started while the queue
    was paused: it didn't happen, so it mustn't be logged when the queue resumes."""
    if not queue or next_part(queue) is None:
        return None
    last = showtime - timedelta(days=7)
    started = (
        queue.get("announced_for") == last.date().isoformat()
        and queue.get("logged_for") != last.date().isoformat()
        and queue.get("postponed_for") != last.date().isoformat()
    )
    if queue.get("paused"):
        return "cancel" if started else None
    # The showing that just started: log it, if we announced it (otherwise we can't tell it happened).
    if started:
        return "log"
    day = showtime.date().isoformat()
    if queue.get("postponed_for") == day or queue.get("announced_for") == day:
        return None
    if announce_hours <= 0:
        return "note"
    if now >= showtime - timedelta(hours=announce_hours):
        return "announce"
    return None


def same_movie(item: dict[str, Any], title: str, year: Any) -> bool:
    """Is a movie this queue part? Same title, and the same year (give or take one) when both have one."""
    if not title or norm(item["title"]) != norm(title):
        return False
    try:
        return not (item.get("year") and year and abs(int(item["year"]) - int(year)) > 1)
    except (TypeError, ValueError):
        return True


def mark_logged(queue: dict[str, Any] | None, titles: list[str], year: Any, day: str) -> dict[str, Any] | None:
    """A movie night logged some other way (a voice card, /movie tonight, the movie pages). If it was the
    queue's next part, that part is done, and that showing isn't announced or logged again. Returns the part."""
    item = next_part(queue)
    if item is None or not any(same_movie(item, t, year) for t in titles):
        return None
    item["done"] = day
    for key in ("announced_for", "logged_for"):  # never back to an earlier showing (the next one may be lined up)
        queue[key] = max(queue.get(key) or "", day)
    return item


def move(queue: dict[str, Any], item_id: str, delta: int) -> None:
    items = queue["items"]
    i = next((n for n, it in enumerate(items) if it["id"] == item_id), None)
    if i is None:
        return
    j = max(0, min(len(items) - 1, i + delta))
    items[i], items[j] = items[j], items[i]
