"""Noticing movie nights from voice: was enough of the group in voice, long enough, around showtime?

Pingwin only looks at who is in a voice channel and for how long (never audio). From 15 minutes before a
series' showtime it samples the voice channel the group is in (the movie night's Discord Event channel, or
the busiest voice channel). When the night is over, if enough people stayed long enough, the movie mods
get a card asking which movie it was, with likely picks from the polls, the franchise queue and the
watchlist.

The helpers here are pure, so they're easy to test; the movie night cog does the Discord parts.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .timeparse import next_weekly

EARLY = timedelta(minutes=15)  # start watching this long before showtime
LONGEST = timedelta(hours=5)  # stop watching this long after showtime, whatever happens
EMPTY_ENDS_AFTER = 20 * 60  # once people came and everyone left for this long, the night is over
MAX_GAP = 120  # seconds: a longer gap between samples (Pingwin restarting) isn't counted as time in voice


def current_show(showtime: str, now: datetime) -> datetime | None:
    """The showing being watched now: from 15 minutes before showtime until 5 hours after. None otherwise."""
    if not showtime:
        return None
    show = next_weekly(showtime, now - LONGEST)
    if show is None or not (show - EARLY <= now <= show + LONGEST):
        return None
    return show


def new_session(day: str, now: float) -> dict[str, Any]:
    return {"day": day, "started": now, "last": now, "seconds": {}, "first_seen": None, "last_seen": None, "peak": 0}


def sample(session: dict[str, Any], here: set[str], now: float, channel_id: int | None = None) -> None:
    """Add the time since the last sample to everyone in voice now."""
    gap = max(0.0, min(MAX_GAP, now - session["last"]))
    session["last"] = now
    if channel_id:
        session["channel"] = channel_id
    if not here:
        return
    for uid in here:
        session["seconds"][uid] = session["seconds"].get(uid, 0) + gap
    session["first_seen"] = session["first_seen"] or now
    session["last_seen"] = now
    session["peak"] = max(session["peak"], len(here))


def is_over(session: dict[str, Any], show: datetime | None, now: float) -> bool:
    """Over when the watch window closes, or when people came and the channel has been empty for a while
    (but not before showtime + 30 minutes, so a late start doesn't end it)."""
    if show is None:
        return True
    if session.get("last_seen") and now - session["last_seen"] > EMPTY_ENDS_AFTER:
        return now > show.timestamp() + 30 * 60
    return False


def stayed(session: dict[str, Any], min_minutes: int) -> list[str]:
    """Everyone who was in voice for at least `min_minutes`."""
    need = min_minutes * 60
    return sorted(uid for uid, s in session["seconds"].items() if s >= need)


def minutes_together(session: dict[str, Any]) -> int:
    if not session.get("first_seen") or not session.get("last_seen"):
        return 0
    return int((session["last_seen"] - session["first_seen"]) // 60)


# --------------------------------------------------------------------- likely movies, from the polls
_GENRE = re.compile(r"^\W*(not\s*horror|non-?horror|horror)\W*$", re.I)
_SKIP = re.compile(r"^\W*(yes|no|maybe|skip|other)\b", re.I)


def _side(text: str) -> str | None:
    t = text.casefold()
    if re.search(r"\bnot\s*horror|\bnon-?horror", t):
        return "not horror"
    if "horror" in t:
        return "horror"
    return None


def poll_picks(polls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Likely movies from recent polls (oldest first): [{"q", "answers": [(text, votes)], "at"}].

    Clubs often vote in two steps: a "Horror or Not Horror?" poll, and a poll of movies for each side.
    The winner of the latest movie poll for the winning side comes first, then other recent winners,
    then the runners-up."""
    genre_side: list[str] = []
    movie_polls = []
    for p in polls:
        answers = [(a.strip(), int(n or 0)) for a, n in p.get("answers", []) if a and a.strip()]
        if not answers:
            continue
        if all(_GENRE.match(a) for a, _ in answers):
            best = max(n for _, n in answers)
            if best:
                genre_side = [_side(a) or "" for a, n in answers if n == best]
            continue
        if all(_SKIP.match(a) for a, _ in answers):
            continue
        movie_polls.append({**p, "answers": answers, "side": _side(p.get("q", ""))})
    first: list[dict[str, Any]] = []
    later: list[dict[str, Any]] = []
    runners: list[dict[str, Any]] = []
    seen_side: set[str] = set()
    for p in reversed(movie_polls):  # newest first
        best = max(n for _, n in p["answers"])
        winners = [a for a, n in p["answers"] if n == best and n > 0]
        why = f'won "{p.get("q", "a poll")[:60]}"' + (f" ({best} votes)" if best else "")
        matches_side = p["side"] in genre_side and p["side"] not in seen_side
        if p["side"]:
            seen_side.add(p["side"])
        target = first if matches_side else later
        target.extend({"title": a, "why": why} for a in winners)
        runners.extend(
            {"title": a, "why": f'runner-up in "{p.get("q", "a poll")[:60]}"'}
            for a, n in sorted(p["answers"], key=lambda x: -x[1])
            if a not in winners and n > 0
        )
    out, seen = [], set()
    for c in first + later + runners:
        key = re.sub(r"[^a-z0-9]+", "", c["title"].casefold())
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out


_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")


def split_year(text: str) -> tuple[str, int | None]:
    """'Alien (1979)' -> ('Alien', 1979)."""
    m = _YEAR.search(text)
    return (text[: m.start()].strip(), int(m[1])) if m else (text.strip(), None)


# --------------------------------------------------------------------- where the vote stands (for reminders)
def standings(polls: list[dict[str, Any]], since: float) -> dict[str, Any] | None:
    """Where this week's vote stands, from recent polls (oldest first, with "at" timestamps).

    `since` is when this week's voting began (the last showing): only polls posted after it count for the
    Horror / Not Horror race and the number of votes, while the movie polls for each side can be older.
    Returns {"sides": [(answer, votes)], "lead": [sides tied for first], "picks": {side: (title, votes)},
    "pick": title or None, "votes": how many voted} or None when there's no poll to go on."""
    this_week = [p for p in polls if p.get("at", 0) >= since]
    if not this_week:
        return None
    genre = None
    for p in reversed(this_week):
        answers = [(a.strip(), int(n or 0)) for a, n in p.get("answers", []) if a and a.strip()]
        if answers and all(_GENRE.match(a) for a, _ in answers):
            genre = answers
            break
    picks: dict[str, tuple[str, int]] = {}
    for p in reversed(polls):  # the newest movie poll for each side
        answers = [(a.strip(), int(n or 0)) for a, n in p.get("answers", []) if a and a.strip()]
        if not answers or all(_GENRE.match(a) for a, _ in answers) or all(_SKIP.match(a) for a, _ in answers):
            continue
        side = _side(p.get("q", "")) or ""
        if side in picks:
            continue
        best = max(answers, key=lambda x: x[1])
        if best[1] > 0:
            picks[side] = best
    lead: list[str] = []
    if genre:
        top = max(n for _, n in genre)
        lead = [_side(a) or a.casefold() for a, n in genre if n == top] if top else []
        votes = sum(n for _, n in genre)
    else:
        votes = max((sum(int(n or 0) for _, n in p.get("answers", [])) for p in this_week), default=0)
    pick = None
    if len(lead) == 1 and lead[0] in picks:
        pick = picks[lead[0]][0]
    elif not genre and "" in picks:
        pick = picks[""][0]
    return {"sides": genre or [], "lead": lead, "picks": picks, "pick": pick, "votes": votes}


def standings_line(st: dict[str, Any] | None) -> str:
    """'🗳️ Horror is ahead (3–2), so it's looking like **Monster Squad**.'"""
    if not st:
        return ""
    sides = st["sides"]
    score = "–".join(str(n) for _, n in sorted(sides, key=lambda x: -x[1])) if sides else ""
    if sides and not st["votes"]:
        return "🗳️ Nobody has voted yet!"
    if len(st["lead"]) == 1:
        side = st["lead"][0]
        label = next((a for a, _ in sides if (_side(a) or a.casefold()) == side), side.title())
        text = f"🗳️ **{label}** is ahead ({score})"
        return text + (f", so it's looking like **{st['pick']}**." if st["pick"] else ".")
    if len(st["lead"]) > 1:
        options = [f"{side.title()}: **{st['picks'][side][0]}**" for side in st["lead"] if side in st["picks"]]
        return f"🗳️ It's a tie ({score})!" + (" " + " or ".join(options) + "." if options else "")
    if st["pick"]:
        return f"🗳️ **{st['pick']}** is winning so far."
    return ""


def nudge(nudges: list[tuple[int, str]], votes: int | None) -> str:
    """The message for the lowest 'fewer than N votes' threshold that applies, with {votes} filled in."""
    if votes is None:
        return ""
    for below, message in sorted(nudges):
        if votes < below and message.strip():
            return message.replace("{votes}", str(votes))
    return ""


_UNITS = {
    "m": 1,
    "min": 1,
    "mins": 1,
    "minute": 1,
    "minutes": 1,
    "h": 60,
    "hr": 60,
    "hrs": 60,
    "hour": 60,
    "hours": 60,
    "d": 1440,
    "day": 1440,
    "days": 1440,
    "w": 10080,
    "week": 10080,
    "weeks": 10080,
}


def parse_before(text: str) -> int | None:
    """Minutes from '1 day', '2h', '1h30m', '90', '15 min'. None if it can't be read."""
    t = str(text or "").strip().casefold().replace(",", " ")
    if not t:
        return None
    if t.isdigit():
        return int(t)
    total, found = 0, False
    for n, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]+)", t):
        if unit not in _UNITS:
            return None
        total += float(n) * _UNITS[unit]
        found = True
    return int(total) if found and total > 0 else None
