"""The movie library: every movie night watched, everyone's ratings, and movie details.

Stored in the shared settings folder (state/movielib.json):

    movies   {movie_id: {title, year, runtime, imdb_id, imdb_rating, director, writer, stars, genre, poster, plot}}
    history  [{id, movie_id, date, series, ratings: {key: {...}}, prompt, closed, source}]
    pending  {request_id: {...}}   changes waiting for a mod (see cogs/movienight.py)

Movie details come from OMDb (omdbapi.com) and are looked up once, then kept. History can be imported
from a spreadsheet (CSV) and a ratings document (plain text) like the ones many movie clubs keep.
"""

from __future__ import annotations

import csv
import difflib
import io
import logging
import re
import secrets
import time
from datetime import date, datetime
from typing import Any

import aiohttp

from . import store

log = logging.getLogger("pingwin.movies")
STATE = "movielib"
OMDB = "https://www.omdbapi.com/"
SECRET = "OMDB_API_KEY"


# --------------------------------------------------------------------- basics
def load() -> dict[str, Any]:
    lib = store.load(STATE)
    lib.setdefault("movies", {})
    lib.setdefault("history", [])
    lib.setdefault("pending", {})
    return lib


def save(lib: dict[str, Any]) -> None:
    store.save(STATE, lib)


def norm(title: str) -> str:
    """Loose key for matching titles: 'The Thing', 'the thing!' and 'Thing, The' all match."""
    t = title.casefold().strip()
    t = re.sub(r",\s*the$", "", t)
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def movie_id(title: str, year: int | str | None) -> str:
    y = str(year or "").strip()
    return f"{norm(title)}-{y}" if y else norm(title)


def find_movie(lib: dict[str, Any], title: str, year: int | str | None = None) -> dict[str, Any] | None:
    if year:
        m = lib["movies"].get(movie_id(title, year))
        if m:
            return m
    key = norm(title)
    matches = [m for m in lib["movies"].values() if norm(m["title"]) == key]
    if year:
        matches = [m for m in matches if str(m.get("year") or "") == str(year)] or matches
    return matches[0] if len(matches) >= 1 else None


def upsert_movie(lib: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    mid = movie_id(info["title"], info.get("year"))
    cur = lib["movies"].setdefault(mid, {"id": mid, "title": info["title"]})
    for k, v in info.items():
        if v not in (None, "", "N/A", "Not Found") and (cur.get(k) in (None, "") or k in ("title",)):
            cur[k] = v
    cur["id"] = mid
    return cur


def label(m: dict[str, Any]) -> str:
    return f"{m['title']} ({m['year']})" if m.get("year") else m["title"]


# --------------------------------------------------------------------- OMDb
def _num(v: Any) -> float | None:
    try:
        return float(str(v).split()[0].replace(",", ""))
    except (ValueError, IndexError):
        return None


def from_omdb(d: dict[str, Any]) -> dict[str, Any]:
    year = re.match(r"\d{4}", d.get("Year", "") or "")
    runtime = _num(d.get("Runtime"))
    return {
        "title": d.get("Title", ""),
        "year": int(year[0]) if year else None,
        "runtime": int(runtime) if runtime else None,
        "imdb_id": d.get("imdbID"),
        "imdb_rating": _num(d.get("imdbRating")),
        "director": None if d.get("Director") in (None, "N/A") else d["Director"],
        "writer": None if d.get("Writer") in (None, "N/A") else d["Writer"],
        "stars": None if d.get("Actors") in (None, "N/A") else d["Actors"],
        "genre": None if d.get("Genre") in (None, "N/A") else d["Genre"],
        "poster": None if d.get("Poster") in (None, "N/A") else d["Poster"],
        "plot": None if d.get("Plot") in (None, "N/A") else d["Plot"],
        "looked_up": time.time(),
    }


async def omdb_lookup(
    http: aiohttp.ClientSession, key: str, title: str, year: int | str | None = None
) -> dict[str, Any] | None:
    """One OMDb request (exact title, then a search for the closest match). None if not found."""
    params = {"apikey": key, "t": title, "type": "movie"}
    if year:
        params["y"] = str(year)
    async with http.get(OMDB, params=params) as r:
        d = await r.json(content_type=None)
    if d.get("Response") == "True":
        return from_omdb(d)
    if "Invalid API key" in str(d.get("Error", "")):
        raise ValueError("OMDb didn't accept the API key")
    params = {"apikey": key, "s": title, "type": "movie", **({"y": str(year)} if year else {})}
    async with http.get(OMDB, params=params) as r:
        d = await r.json(content_type=None)
    hits = d.get("Search") or []
    if not hits:
        return None
    async with http.get(OMDB, params={"apikey": key, "i": hits[0]["imdbID"]}) as r:
        d = await r.json(content_type=None)
    return from_omdb(d) if d.get("Response") == "True" else None


# --------------------------------------------------------------------- history
def new_entry(movie: dict[str, Any], day: str | None, series: str, source: str = "pingwin") -> dict[str, Any]:
    return {
        "id": secrets.token_hex(4),
        "movie_id": movie["id"],
        "date": day,
        "series": series,
        "ratings": {},
        "source": source,
        "created": time.time(),
    }


def ratings_avg(entry: dict[str, Any]) -> float | None:
    scores = [r["score"] for r in entry.get("ratings", {}).values() if isinstance(r.get("score"), (int, float))]
    if scores:
        return round(sum(scores) / len(scores), 1)
    return entry.get("group_avg")


def add_rating(
    entry: dict[str, Any],
    key: str,
    *,
    name: str,
    score: float | None,
    comment: str = "",
    user_id: str | None = None,
    answer: str = "",
    ask: str = "",
    answered: str = "",
) -> None:
    entry.setdefault("ratings", {})[key] = {
        "name": name,
        "user_id": user_id,
        "score": score,
        "comment": comment.strip(),
        "answered": answered.strip(),  # the question they were asked by the previous rater
        "answer": answer.strip(),
        "ask": ask.strip(),  # the question they pass on to the next rater
        "at": time.time(),
    }


def open_question(entry: dict[str, Any]) -> str:
    """The question the most recent rater passed on (for whoever rates next)."""
    rs = sorted(entry.get("ratings", {}).values(), key=lambda r: r.get("at", 0))
    return rs[-1].get("ask", "") if rs else ""


def parse_score(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", text or "")
    if not m:
        return None
    v = float(m[0])
    return v if 0 <= v <= 10 else None


# --------------------------------------------------------------------- importing
SERIES_ALIASES = {
    "moviemonday": "MMM",
    "moviemondays": "MMM",
    "filmfranchisefriday": "FFF",
    "filmfranchisefridays": "FFF",
}


def series_name(session: str, known: list[str]) -> str:
    key = re.sub(r"[^a-z]", "", session.casefold())
    if key in SERIES_ALIASES and SERIES_ALIASES[key] in known:
        return SERIES_ALIASES[key]
    for k in known:
        if k.casefold() == session.strip().casefold():
            return k
    return session.strip() or "Other"


def _date(text: str) -> str | None:
    text = (text or "").strip()
    for fmt in ("%A, %B %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _int(text: str) -> int | None:
    v = _num(text)
    return int(v) if v else None


def parse_sheet(csv_text: str) -> list[dict[str, Any]]:
    """Rows from a movie log spreadsheet: Date, Movie, Year, Duration, Session, IMDB rating, our rating, ..."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows:
        return []
    head = [h.casefold() for h in rows[0]]

    def col(*words: str) -> int | None:
        for i, h in enumerate(head):
            if all(w in h for w in words):
                return i
        return None

    c = {
        "date": col("date"),
        "title": col("movie") if col("movie") is not None else col("title"),
        "year": col("year"),
        "runtime": col("duration") if col("duration") is not None else col("runtime"),
        "session": col("session") if col("session") is not None else col("series"),
        "imdb": col("imdb"),
        "ours": col("our"),
        "director": col("director"),
        "writer": col("writer"),
        "stars": col("star"),
    }
    out = []
    for r in rows[1:]:

        def get(k: str, r: list[str] = r) -> str:
            i = c.get(k)
            return r[i].strip() if i is not None and i < len(r) else ""

        title = get("title")
        if not title or title.startswith("="):
            continue
        year, runtime = _int(get("year")), _int(get("runtime"))
        if year and runtime and year < 1800 and runtime > 1800:  # columns swapped
            year, runtime = runtime, year
        out.append(
            {
                "date": _date(get("date")),
                "title": title,
                "year": year,
                "runtime": runtime,
                "session": get("session"),
                "imdb_rating": _num(get("imdb")),
                "group_avg": _num(get("ours")),
                "director": get("director") or None,
                "writer": get("writer") or None,
                "stars": get("stars") or None,
            }
        )
    return out


RATING = re.compile(
    r"^(?P<name>[A-Z][\w .'’&/-]{0,30}?)\s*(?:’s|'s)?\s*"
    r"\(\s*(?P<score>[\d.]+|NA|N/A|U|)\s*/\s*10(?:\.0)?\s*\)\s*[:\-–]?\s*(?P<comment>.*)$"
)
RATING_ALT = re.compile(r"^(?P<name>[A-Z][\w .'’&/-]{0,30}):\s*(?P<score>[\d.]+)\s*/\s*10\s*(?P<comment>.*)$")
QUESTION = re.compile(r"^To\s*(?P<to>[^:]{0,30}):\s*(?P<q>.*)$")
DATE_LINE = re.compile(r"^Date watched:\s*(?P<date>.+)$", re.I)


def _title_key(line: str) -> str:
    return norm(re.sub(r"\(\d{4}\)|\d+(\.\d+)?\s*$|:\s*remake", "", line, flags=re.I))


BULLET = re.compile(r"^[*•\-–]\s*")
HOW_MANY = re.compile(r"^how many\b|\bout of\b", re.I)
SEPARATOR = re.compile(r"^[_\-=*]{6,}$")
TOC_LINE = re.compile(r"^(?P<title>\S.*?)(?:\t+|\s{2,})(?P<page>\d{1,4})$")


def parse_ratings_doc(text: str, titles: set[str] | None = None) -> list[dict[str, Any]]:
    """Sections of a ratings document: a movie title line, 'Date watched: ...', then lines like
    'Name (7/10): comment' and 'To Name: how many X out of Y?' passed to the next person.

    Sections are found by a separator line (________) before them, or by a title followed by
    'Date watched' or a rating. `titles` (normalized movie titles) helps spot the second kind.
    A table of contents at the top ('Title    12') names sections that start without a title."""
    titles = titles or set()
    lines = [ln.strip().replace("\u200b", "") for ln in text.splitlines()]
    sections: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    last_rating: dict[str, Any] | None = None
    toc: list[str] = []
    toc_at = -1  # where the latest section sits in the table of contents
    after_sep = False
    seen_sep = False

    def start(title: str | None) -> dict[str, Any]:
        nonlocal toc_at
        if title is None and 0 <= toc_at + 1 < len(toc):
            title = toc[toc_at + 1]  # an untitled section: the next one in the contents
        if title:
            key = _title_key(title)
            keys = [_title_key(t) for t in toc]
            later = [n for n in range(toc_at + 1, len(toc)) if keys[n] == key]
            if later:
                toc_at = later[0]
            elif title in toc:
                toc_at = toc.index(title)
        sec = {"title": title or "", "date": None, "ratings": []}
        sections.append(sec)
        return sec

    for i, ln in enumerate(lines):
        if not ln:
            continue
        if SEPARATOR.match(ln):
            after_sep = seen_sep = True
            cur, last_rating = None, None
            continue
        is_rating = RATING.match(ln) or RATING_ALT.match(ln)
        if not seen_sep and not sections:
            m = TOC_LINE.match(ln)
            if m and not is_rating and len(ln) < 120:
                toc.append(m["title"].strip())
                continue
        if after_sep:
            after_sep = False
            if is_rating:
                cur = start(None)
                last_rating = None
            elif not DATE_LINE.match(ln) and not QUESTION.match(ln) and len(ln) < 100:
                cur = start(ln)
                last_rating = None
                continue
        nxt = next((x for x in lines[i + 1 : i + 4] if x), "")
        key = _title_key(ln)
        titled = (
            len(ln) < 80 and bool(key) and (key in titles or any(len(k) >= 5 and key.startswith(k) for k in titles))
        )
        if (
            not is_rating
            and not QUESTION.match(ln)
            and not DATE_LINE.match(ln)
            and (DATE_LINE.match(nxt) or (titled and (RATING.match(nxt) or RATING_ALT.match(nxt))))
        ):
            cur = start(ln)
            last_rating = None
            continue
        if cur is None:
            continue
        m = DATE_LINE.match(ln)
        if m:
            cur["date"] = _date(m["date"])
            continue
        m = is_rating
        if m:
            score = None if m["score"] in ("", "NA", "N/A", "U") else _num(m["score"])
            last_rating = {"name": m["name"].strip(), "score": score, "comment": m["comment"].strip(), "ask": ""}
            cur["ratings"].append(last_rating)
            continue
        bare = BULLET.sub("", ln)
        m = QUESTION.match(bare)
        if last_rating is not None and (m or (bare != ln and HOW_MANY.search(bare))):
            q = (m["q"] if m else bare).strip()
            if q:
                last_rating["ask"] = q
            continue
        if (
            last_rating is not None
            and len(last_rating["comment"]) < 2000
            and not ln.lower().startswith(("transcript", "to "))
        ):
            last_rating["comment"] = (last_rating["comment"] + " " + ln).strip()
    return [s for s in sections if s["ratings"] and s["title"] and s["title"].casefold() != "template"]


def match_name(name: str, members: dict[str, str]) -> str | None:
    """Find a member ID for a first name like 'Sam' among {user_id: 'Sammy (Sam) sam_the_great'}."""
    key = name.casefold().strip()
    if len(key) < 3:
        return None
    for uid, names in members.items():
        words = re.findall(r"[a-z0-9]+", names.casefold())
        if key in words or any(w.startswith(key) for w in words if len(key) >= 4):
            return uid
    return None


def _close(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if len(min(a, b, key=len)) >= 5 and (a.startswith(b) or b.startswith(a)):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def import_history(
    lib: dict[str, Any],
    sheet: list[dict[str, Any]],
    doc: list[dict[str, Any]],
    known_series: list[str],
    members: dict[str, str] | None = None,
) -> dict[str, int]:
    """Merge a spreadsheet log and a ratings document into the library. Safe to run again."""
    members = members or {}
    stats = {"movies": 0, "entries": 0, "ratings": 0, "skipped": 0}
    existing = {(e.get("date"), e["movie_id"]) for e in lib["history"]}
    by_movie_date: dict[tuple[str | None, str], dict[str, Any]] = {
        (e.get("date"), e["movie_id"]): e for e in lib["history"]
    }
    for row in sheet:
        before = len(lib["movies"])
        m = upsert_movie(
            lib, {k: row[k] for k in ("title", "year", "runtime", "imdb_rating", "director", "writer", "stars")}
        )
        stats["movies"] += len(lib["movies"]) - before
        key = (row["date"], m["id"])
        if key in existing:
            stats["skipped"] += 1
            continue
        e = new_entry(m, row["date"], series_name(row["session"], known_series), source="import")
        if row.get("group_avg") is not None:
            e["group_avg"] = row["group_avg"]
        lib["history"].append(e)
        existing.add(key)
        by_movie_date[key] = e
        stats["entries"] += 1
    # Ratings: match each document section to a history entry by date, then by title.
    for sec in doc:
        entry = None
        if sec["date"]:
            same_day = [e for e in lib["history"] if e.get("date") == sec["date"]]
            entry = next(
                (
                    e
                    for e in same_day
                    if norm(lib["movies"][e["movie_id"]]["title"]) in norm(sec["title"])
                    or norm(sec["title"]) in norm(lib["movies"][e["movie_id"]]["title"])
                ),
                None,
            )
            entry = entry or (same_day[0] if len(same_day) == 1 else None)
        if entry is None:
            t = re.sub(r"\(\d{4}\)|\d+(\.\d+)?\s*$|:\s*remake", "", sec["title"], flags=re.I).strip()
            year = re.search(r"\((\d{4})\)", sec["title"])
            cands = [e for e in lib["history"] if norm(lib["movies"][e["movie_id"]]["title"]) == norm(t)]
            if year:
                cands = [e for e in cands if str(lib["movies"][e["movie_id"]].get("year")) == year[1]] or cands
                if not cands:  # same year, nearly the same title ('Birdemic' / 'Birdemic: Shock and Terror')
                    cands = [
                        e
                        for e in lib["history"]
                        if str(lib["movies"][e["movie_id"]].get("year")) == year[1]
                        and _close(norm(lib["movies"][e["movie_id"]]["title"]), norm(t))
                    ]
            entry = cands[0] if len(cands) == 1 else None
        if entry is None:
            stats["skipped"] += 1
            continue
        asked = ""
        for r in sec["ratings"]:
            uid = match_name(r["name"], members)
            key = uid or f"name:{r['name'].casefold()}"
            if key in entry["ratings"]:
                asked = r.get("ask", "")
                continue
            add_rating(
                entry,
                key,
                name=r["name"],
                score=r["score"],
                comment=r["comment"],
                user_id=uid,
                ask=r.get("ask", ""),
                answered=asked,
            )
            entry["ratings"][key]["at"] = 0  # imported: keep document order stable
            asked = r.get("ask", "")
            stats["ratings"] += 1
    lib["history"].sort(key=lambda e: (e.get("date") or "9999", e.get("created", 0)))
    return stats


def summary(lib: dict[str, Any]) -> dict[str, Any]:
    days = [e["date"] for e in lib["history"] if e.get("date")]
    return {
        "movies": len(lib["movies"]),
        "nights": len(lib["history"]),
        "since": min(days) if days else None,
        "today": date.today().isoformat(),
    }
