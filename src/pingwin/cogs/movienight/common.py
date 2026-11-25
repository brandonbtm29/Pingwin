"""Movie night constants and helpers: picking a poll's movies, poll timing, titles, dates and embeds."""

from __future__ import annotations

import logging
import math
import random
import re
import secrets
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import discord

from ... import breaks, movies

if TYPE_CHECKING:
    pass


log = logging.getLogger("pingwin.movienight")
STATE = "movienight"
DEFAULT_RUNTIME = 120  # minutes, when we don't know the movie's length
GOLD = discord.Color.gold()


def pick_options(noms: list[dict], how: str, count: int) -> list[dict]:
    unwatched = [n for n in noms if not n.get("watched")]
    if how == "random":
        return random.sample(unwatched, min(count, len(unwatched)))
    return sorted(unwatched, key=lambda n: n["added"])[:count]


# /pingwin movie poll's poll-type: (ranked, more than one vote each)
POLL_TYPES = {"plurality": (False, False), "ranked": (True, False), "approval": (False, True)}
# Polls that are Pingwin's own message (all new polls; older ones were Discord polls, with no "kind")
POLL_NAMES = {"plurality": "One vote each", "approval": "Approval", "ranked": "Ranked choice"}
PINGWIN_POLLS = frozenset(POLL_NAMES)


def _list_key(n: dict) -> str:
    return str(n.get("list") or "").strip().casefold()


def _genres_of(genres: dict[str, str] | None, n: dict) -> set[str]:
    return {g.strip().casefold() for g in (genres or {}).get(n.get("movie_id") or "", "").split(",") if g.strip()}


def pick_movies(
    noms: list[dict],
    how: str,
    count: int,
    *,
    last: list[str] | None = None,
    mix: list[tuple[str, int]] | None = None,
    genres: dict[str, str] | None = None,
    rng: random.Random | None = None,
) -> list[dict]:
    """The movies Pingwin picks for a poll.

    oldest / random: `count` of the watchlist. carryover: the last poll's movies (`last`, by id) in the same order,
    where each one watched since (or taken off the watchlist) gives way to a random movie from the same watchlist
    list (Horror for Horror), or from the whole watchlist when that list has none left. by_list / by_genre: a
    random mix, `mix` being (list or IMDb genre, how many) pairs; `genres` maps movie ids to OMDb's genres.
    Without a last poll or a mix, it's random."""
    rng = rng or random.Random()
    unwatched = [n for n in noms if not n.get("watched")]
    if how == "carryover" and last:
        by_id = {n["id"]: n for n in noms}
        keep = {i for i in last if i in by_id and not by_id[i].get("watched")}
        taken = set(keep)
        picks = []
        for i in last[:POLL_MAX]:
            if i in keep:
                picks.append(by_id[i])
                continue
            gone = by_id.get(i)  # watched since, or None: taken off the watchlist
            pool = [n for n in unwatched if n["id"] not in taken]
            same = [n for n in pool if gone is not None and _list_key(n) == _list_key(gone)]
            if pool:
                pick = rng.choice(same or pool)
                picks.append(pick)
                taken.add(pick["id"])
        return picks
    if how in ("by_list", "by_genre") and mix:
        picks, taken = [], set()
        for name, k in mix:
            key = name.strip().casefold()
            pool = [
                n
                for n in unwatched
                if n["id"] not in taken and (_list_key(n) == key if how == "by_list" else key in _genres_of(genres, n))
            ]
            for n in rng.sample(pool, min(max(0, k), len(pool))):
                picks.append(n)
                taken.add(n["id"])
        picks = picks[:POLL_MAX]
        rng.shuffle(picks)
        return picks
    return pick_options(noms, "oldest" if how == "oldest" else "random", count)


SOONEST = timedelta(minutes=5)  # a showtime closer than this means next week's


def poll_close(
    showtime: str, now: datetime, hours: int | None, default_hours: int, skips: list[dict[str, Any]] | None = None
) -> tuple[datetime, int]:
    """When a poll should close, and the whole-hour duration to give Discord (it only takes hours).

    Explicit hours win. Otherwise, with a showtime, the poll closes at the next showtime and Pingwin ends
    it then (Discord's own timer runs up to an hour longer). Otherwise the default length.

    Lengths are real elapsed time (timestamps), not wall-clock time, so they're right across a DST change.
    """
    if hours is None and showtime:
        start = breaks.next_showing(showtime, now, skips)  # a week off doesn't count
        if start is not None:
            if start.timestamp() - now.timestamp() < SOONEST.total_seconds():
                start = breaks.next_showing(showtime, start + timedelta(minutes=1), skips) or start
            seconds = start.timestamp() - now.timestamp()
            return start, max(1, min(768, math.ceil(seconds / 3600)))
    h = hours or default_hours
    return (now.astimezone(UTC) + timedelta(hours=h)).astimezone(now.tzinfo), h


def winners(counts: list[tuple[str, int]]) -> list[str]:
    """IDs with the most votes (several on a tie); empty if nobody voted."""
    if not counts or max(c for _, c in counts) == 0:
        return []
    top = max(c for _, c in counts)
    return [i for i, c in counts if c == top]


def tallies(p: dict[str, Any]) -> dict[str, int]:
    """Votes for each movie in a one-vote or approval poll: each ballot counts once for every movie on it."""
    counts = {c: 0 for c in p["options"]}
    for ballot in p.get("ballots", {}).values():
        for c in set(ballot):
            if c in counts:
                counts[c] += 1
    return counts


def voter_count(p: dict[str, Any]) -> int:
    return len([b for b in p.get("ballots", {}).values() if b])


def candidate_label(c: dict[str, Any]) -> str:
    """A movie's entry in the night card's menu."""
    return (f"{c['title']} ({c['year']})" if c.get("year") else str(c.get("title", "")))[:100]


def _yr(d: dict[str, Any]) -> str:
    return f" ({d['year']})" if d.get("year") else ""


def describe_request(req: dict[str, Any]) -> str:
    """One line for an approval card."""
    d = req["data"]
    kind = req["kind"]
    if kind == "nominate":
        return f"Add **{d['title']}**{_yr(d)} to the {req['series']} watchlist"
    if kind == "remove":
        return f"Take **{d['title']}** off the {req['series']} watchlist"
    if kind == "watched":
        if not d.get("watched", True):
            return f"Mark **{d['title']}** as not watched ({req['series']})"
        when = f", watched {d['date']}" if d.get("date") else ""
        rate = " with a Rate it button" if d.get("rate") else ""
        return f"Mark **{d['title']}** as watched ({req['series']}{when}){rate}"
    if kind == "tonight":
        day = f" ({d['when'][:10]})" if d.get("when") else ""
        return f"Set tonight's {req['series']} movie{day} to **{d['title']}**"
    if kind == "edit":
        changes = ", ".join(f"{k}: {v}" for k, v in d.get("changes", {}).items())
        return f"Fix **{d.get('label', 'a movie night')}** in the history: {changes}"
    if kind == "night":
        imdb = f" · <https://www.imdb.com/title/{d['imdb_id']}/>" if d.get("imdb_id") else ""
        return f"Add **{d['title']}**{_yr(d)} to the {req['series']} history, watched {d['date']}{imdb}"
    if kind == "detected":
        v = d.get("voice")
        parts = [f"{req['series']} on {d['date']}"]
        if d.get("expected"):
            parts.append(f"{d.get('why') or 'up tonight'}: {d['expected']['title']}{_yr(d['expected'])}")
        if v:
            parts.append(f"{v.get('people', '?')} people were in voice for about {_duration(v.get('minutes', 0))}")
        return ": ".join(parts[:1]) + (" · " + " · ".join(parts[1:]) if parts[1:] else "") + ". Which movie was it?"
    return kind


def _same_year(m: dict[str, Any] | None, year: Any) -> dict[str, Any] | None:
    """The library movie, unless its year says it's a namesake (the 2002 Resident Evil for the 2026 one)."""
    try:
        if m and year and m.get("year") and abs(int(m["year"]) - int(year)) > 1:
            return None
    except (TypeError, ValueError):
        pass
    return m


def _same_title(item: dict[str, Any], title: str, year: Any) -> bool:
    """Is a watchlist or library movie this one? Same title, and not a remake or namesake from another year
    (when both have a year)."""
    return movies.norm(item.get("title") or "") == movies.norm(title or "") and _same_year(item, year) is not None


POLL_MAX = 10  # a Discord poll holds 10 answers; hand-picked polls can use them all
TYPED_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")


def parse_typed_movie(text: str) -> dict[str, Any] | None:
    """A movie someone typed for a poll: 'Alien', 'Alien (1979)' or an IMDb link (with or without a title).
    Only a year in brackets counts, so titles like 'Blade Runner 2049' stay whole."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return None
    imdb_id = movies.imdb_id_in(text)
    title = re.sub(r"\S*imdb\.com\S*|\btt\d{6,10}\b", "", text).strip(" -–,")
    year = None
    m = TYPED_YEAR.search(title)
    if m and title[: m.start()].strip():
        year, title = int(m[1]), title[: m.start()].strip()
    if not title and not imdb_id:
        return None
    return {"title": title[:100], "year": year, "imdb_id": imdb_id}


def _new_nomination(data: dict[str, Any], by: int) -> dict[str, Any]:
    return {
        "id": secrets.token_hex(4),
        "title": data["title"],
        "year": data.get("year"),
        "movie_id": data.get("movie_id"),
        "by": by,
        "added": discord.utils.utcnow().isoformat(),
        "watched": None,
    }


def _known(index: dict[str, list[Any]], title: str, year: Any) -> bool:
    """Is this title (and year) in an index of {normalized title: [years]}?"""
    return any(_same_year({"year": y}, year) for y in index.get(movies.norm(title), []))


def movie_card(m: dict[str, Any], why: str = "") -> discord.Embed:
    """A movie for an event post: title (linked to IMDb), poster, rating, runtime, genre and a short plot.
    `why` goes underneath, like 'Part 3 of 6 of Alien' or 'Leading the vote so far'."""
    title = m.get("title") or "?"
    if m.get("year"):
        title += f" ({m['year']})"
    url = f"https://www.imdb.com/title/{m['imdb_id']}/" if m.get("imdb_id") else None
    facts = []
    if m.get("imdb_rating"):
        facts.append(f"⭐ IMDb {m['imdb_rating']}")
    if m.get("runtime"):
        facts.append(f"{m['runtime']} min")
    if m.get("genre"):
        facts.append(str(m["genre"]))
    plot = str(m.get("plot") or "").strip()
    if len(plot) > 220:
        plot = plot[:217].rsplit(" ", 1)[0] + "…"
    lines = [" · ".join(facts), plot]
    if m.get("director"):
        lines.append(f"-# Directed by {m['director']}")
    e = discord.Embed(
        title=title[:250], url=url, description="\n".join(x for x in lines if x)[:4000] or None, color=0xE8B04B
    )
    poster = str(m.get("poster") or "")
    if poster.startswith("https://"):
        e.set_image(url=poster)
    if why:
        e.set_footer(text=why[:200])
    return e


def imdb_url(m: dict[str, Any] | None, title: str, year: Any = None) -> str:
    """The movie's IMDb page, or an IMDb search for it when we don't know which one it is."""
    if m and m.get("imdb_id"):
        return f"https://www.imdb.com/title/{m['imdb_id']}/"
    return "https://www.imdb.com/find/?" + urlencode({"q": f"{title} {year or ''}".strip(), "s": "tt"})


def poll_details_embed(
    items: list[tuple[dict[str, Any], dict[str, Any] | None]], title: str = "🎬 The movies in the poll"
) -> discord.Embed | None:
    """The movies in a poll, one paragraph each: title (linked to IMDb), rating, runtime, genre and a short
    plot. `items` pairs each poll option with what the movie library knows about it (None: nothing)."""
    if not items:
        return None
    plot_room = max(80, min(220, 3600 // len(items) - 120))  # 10 movies still fit in one message
    parts = []
    for n, m in items:
        m = m or {}
        label = n["title"] + (f" ({n['year']})" if n.get("year") else "")
        facts = []
        if m.get("imdb_rating"):
            facts.append(f"⭐ {m['imdb_rating']}")
        if m.get("runtime"):
            facts.append(f"{m['runtime']} min")
        if m.get("genre"):
            facts.append(str(m["genre"]))
        head = f"**[{label}]({imdb_url(m, n['title'], n.get('year'))})**" + (" · " + " · ".join(facts) if facts else "")
        plot = re.sub(r"\s+", " ", str(m.get("plot") or "")).strip()
        if len(plot) > plot_room:
            plot = plot[: plot_room - 1].rsplit(" ", 1)[0] + "…"
        parts.append(head + ("\n" + plot if plot else ""))
    text = "\n\n".join(parts)
    return discord.Embed(title=title[:256], description=text[:4096], color=0xE8B04B)


_MONTHS = {
    m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)
}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_watch_date(text: str, today: date) -> date | None:
    """When a movie was watched, from '2026-11-02', '11/2', '11/2/2026', 'Nov 2', 'today', 'yesterday' or a
    weekday ('monday' = the latest Monday, today included). Dates without a year are the latest one that
    isn't in the future. None if it can't be read or is in the future."""
    t = re.sub(r"\s+", " ", (text or "").strip().casefold().replace(",", " "))
    if not t or t == "today":
        return today
    if t == "yesterday":
        return today - timedelta(days=1)
    t = re.sub(r"^last ", "", t)
    if t[:3] in [w[:3] for w in _WEEKDAYS] and re.fullmatch(r"[a-z]+", t):
        wd = next(i for i, w in enumerate(_WEEKDAYS) if w.startswith(t[:3]))
        return today - timedelta(days=(today.weekday() - wd) % 7)
    y = None
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    elif m := re.fullmatch(r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2}|\d{4}))?", t):
        mo, d = int(m[1]), int(m[2])
        y = (int(m[3]) + (2000 if len(m[3]) == 2 else 0)) if m[3] else None
    elif m := re.fullmatch(r"([a-z]{3})[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?", t):
        if m[1] not in _MONTHS:
            return None
        mo, d, y = _MONTHS[m[1]], int(m[2]), int(m[3]) if m[3] else None
    else:
        return None
    try:
        when = date(y or today.year, mo, d)
        if y is None and when > today:
            when = date(today.year - 1, mo, d)
    except ValueError:
        return None
    return None if when > today else when


def parse_from_date(text: str, today: date) -> date | None:
    """A date from today on, from '11/13', 'Nov 13', '2026-11-13', 'today', 'tomorrow' or a weekday ('friday'
    = the next Friday, today included). Dates without a year are the next one to come."""
    t = re.sub(r"\s+", " ", (text or "").strip().casefold().replace(",", " "))
    if not t or t == "today":
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    t = re.sub(r"^(next|this) ", "", t)
    if t[:3] in [w[:3] for w in _WEEKDAYS] and re.fullmatch(r"[a-z]+", t):
        wd = next(i for i, w in enumerate(_WEEKDAYS) if w.startswith(t[:3]))
        return today + timedelta(days=(wd - today.weekday()) % 7)
    y = None
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    elif m := re.fullmatch(r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2}|\d{4}))?", t):
        mo, d = int(m[1]), int(m[2])
        y = (int(m[3]) + (2000 if len(m[3]) == 2 else 0)) if m[3] else None
    elif m := re.fullmatch(r"([a-z]{3})[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?", t):
        if m[1] not in _MONTHS:
            return None
        mo, d, y = _MONTHS[m[1]], int(m[2]), int(m[3]) if m[3] else None
    else:
        return None
    try:
        when = date(y or today.year, mo, d)
        if y is None and when < today:
            when = date(today.year + 1, mo, d)
    except ValueError:
        return None
    return None if when < today else when


def _with_first(candidates: list[dict[str, Any]], first: dict[str, Any]) -> list[dict[str, Any]]:
    """`first` at the top of the candidates, without listing the same movie twice."""
    rest = [c for c in candidates if not _same_title(c, first["title"], first.get("year"))]
    return [{k: first.get(k) for k in ("title", "year", "imdb_id", "why") if first.get(k) is not None}, *rest]


def _day_label(day: str) -> str:
    """'2026-11-09' -> 'Mon, Nov 9'."""
    try:
        d = date.fromisoformat(day)
    except (TypeError, ValueError):
        return str(day)
    return f"{d:%a}, {d:%b} {d.day}"


def _duration(minutes: int) -> str:
    h, m = divmod(int(minutes or 0), 60)
    return f"{h} h {m} min" if h else f"{m} min"


def is_franchise(s) -> bool:
    return bool(getattr(s, "franchises", False))


def series_label(s) -> str:
    """A series' full name for announcements ('Movie Mondays'), else its short name."""
    return getattr(s, "full_name", "") or s.name


# A watchlist for anything that isn't for a particular series. No role, channel or showtime of its own:
# its polls go to the channel they're started in.
GENERAL = SimpleNamespace(name="General", full_name="General", role="", channel=None, showtime="")


def _movie_label(n: dict[str, Any]) -> str:
    return n["title"] + (f" ({n['year']})" if n.get("year") else "")
