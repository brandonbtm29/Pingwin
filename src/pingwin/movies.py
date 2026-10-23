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
    lib.setdefault("people", {})  # rater key -> the name they count as (set by a mod on the movie pages)
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


class OmdbLimit(Exception):
    """OMDb's daily request limit is used up."""


class OmdbKeyError(ValueError):
    """OMDb didn't accept the API key (wrong, or not activated from the email yet)."""


async def omdb_lookup(
    http: aiohttp.ClientSession,
    key: str,
    title: str,
    year: int | str | None = None,
    spent: list[int] | None = None,
) -> dict[str, Any] | None:
    """Look a movie up on OMDb: the exact title, then a search for the closest match. None if not found.

    `spent` counts the requests made (up to three), for the daily budget."""
    spent = spent if spent is not None else [0]

    async def ask(params: dict[str, str]) -> dict[str, Any]:
        spent[0] += 1
        async with http.get(OMDB, params={"apikey": key, **params}) as r:
            d = await r.json(content_type=None)
        err = str(d.get("Error", ""))
        if "limit" in err.lower():
            raise OmdbLimit(err)
        if "api key" in err.lower():
            raise OmdbKeyError(err or "OMDb didn't accept the API key")
        return d

    y = {"y": str(year)} if year else {}
    d = await ask({"t": title, "type": "movie", **y})
    if d.get("Response") == "True":
        return from_omdb(d)
    hits = (await ask({"s": title, "type": "movie", **y})).get("Search") or []
    if not hits:
        return None
    d = await ask({"i": hits[0]["imdbID"]})
    return from_omdb(d) if d.get("Response") == "True" else None


RETRY_AFTER = 7 * 86400  # look a missed movie up again after a week, up to MAX_TRIES times
MAX_TRIES = 3


def needs_details(item: dict[str, Any], now: float) -> bool:
    if item.get("looked_up"):
        return False
    tries = item.get("omdb_tries", 0)
    return tries == 0 or (tries < MAX_TRIES and now - item.get("omdb_at", 0) > RETRY_AFTER)


def plausible(m: dict[str, Any], info: dict[str, Any]) -> bool:
    """Is an OMDb result really this movie? (A different year usually means a remake or a namesake.)"""
    if m.get("year") and info.get("year"):
        return abs(int(m["year"]) - int(info["year"])) <= 1
    return True


def merge_details(m: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    """Fill in what a library movie is missing from an OMDb result. Keeps its own title and id."""
    for k, v in info.items():
        if k in ("title", "id") or v in (None, "", "N/A"):
            continue
        if k in ("imdb_id", "looked_up") or m.get(k) in (None, ""):
            m[k] = v
    if not m.get("year") and info.get("year"):
        m["year"] = info["year"]
    m.pop("omdb_tries", None)
    m.pop("omdb_at", None)
    return m


def note_miss(item: dict[str, Any], now: float) -> None:
    item["omdb_tries"] = item.get("omdb_tries", 0) + 1
    item["omdb_at"] = now


def details_todo(lib: dict[str, Any], watch: dict[str, Any], now: float) -> list[tuple]:
    """What still needs OMDb details: watched movies (newest first), other library movies, then
    watchlist movies that aren't in the library yet. ('movie', id, title, year) or ('nom', key, id, title, year)."""
    out: list[tuple] = []
    seen: set[str] = set()
    for e in reversed(lib["history"]):
        m = lib["movies"].get(e["movie_id"])
        if m and m["id"] not in seen and needs_details(m, now):
            seen.add(m["id"])
            out.append(("movie", m["id"], m["title"], m.get("year")))
    for m in lib["movies"].values():
        if m["id"] not in seen and needs_details(m, now):
            seen.add(m["id"])
            out.append(("movie", m["id"], m["title"], m.get("year")))
    for key, data in watch.items():
        if not isinstance(data, dict):
            continue
        for n in data.get("nominations", []):
            if n.get("watched") or (n.get("movie_id") and n["movie_id"] in lib["movies"]):
                continue
            if needs_details(n, now):
                out.append(("nom", key, n["id"], n["title"], n.get("year")))
    return out


def details_progress(lib: dict[str, Any], watch: dict[str, Any]) -> dict[str, int]:
    done = sum(1 for m in lib["movies"].values() if m.get("looked_up"))
    noms = [
        n
        for d in watch.values()
        if isinstance(d, dict)
        for n in d.get("nominations", [])
        if not n.get("watched") and not (n.get("movie_id") and n["movie_id"] in lib["movies"])
    ]
    missed = sum(1 for m in lib["movies"].values() if not m.get("looked_up") and m.get("omdb_tries", 0) >= MAX_TRIES)
    missed += sum(1 for n in noms if n.get("omdb_tries", 0) >= MAX_TRIES)
    total = len(lib["movies"]) + len(noms)
    return {"done": done, "total": total, "not_found": missed, "left": total - done - missed}


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


DONE_MARKS = ("✅", "✔️", "✔", "☑️", "☑")
TODO_MARKS = ("🇽", "❌", "✖️", "✖", "⬜", "☐", "🔲", "▫️", "◻️")
LIST_PREFIX = re.compile(r"^(?:[-*•·]|\d{1,3}[.)])\s*")


def parse_list_message(text: str) -> list[dict[str, Any]]:
    """Movies from a list message like a pinned watchlist.

    Lines marked ✅ are watched and 🇽 (or ❌, ⬜) are still waiting. If no line has a mark, every
    line counts as waiting, except a title line at the top ending in ':'."""
    rows = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    marked = any(ln.startswith(DONE_MARKS + TODO_MARKS) for ln in lines)
    for ln in lines:
        done = ln.startswith(DONE_MARKS)
        if marked and not (done or ln.startswith(TODO_MARKS)):
            continue  # a heading like 'Movies list part 2'
        for m in DONE_MARKS + TODO_MARKS:
            if ln.startswith(m):
                ln = ln[len(m) :].strip()
                break
        ln = LIST_PREFIX.sub("", ln).strip(" *_~")
        if not ln or (not marked and ln.endswith(":")):
            continue
        year, note = None, ""
        m = re.search(r"\((\d{4})\b([^)]*)\)\s*$", ln)
        if m:
            year, note, ln = int(m[1]), m[2].strip(" ,;-"), ln[: m.start()].strip()
        else:
            m = re.search(r"\s((?:18|19|20)\d\d)$", ln)
            if m and len(ln) > 5:
                year, ln = int(m[1]), ln[: m.start()].strip()
        if ln:
            rows.append({"title": ln[:100], "year": year, "note": note[:80], "done": done})
    return rows


def rater_links(lib: dict[str, Any]) -> dict[str, str]:
    """The Discord member (user ID) behind each rater key, where there is one."""
    links = {}
    for e in lib["history"]:
        for k in e.get("ratings", {}):
            if k.isdigit():
                links[k] = k
    for k, v in lib.get("people", {}).items():
        if isinstance(v, str) and v.startswith("@") and v[1:].isdigit():
            links[k] = v[1:]
    return links


def rater_names(lib: dict[str, Any], members: dict[str, str] | None = None) -> dict[str, str]:
    """Who each rater key is: the member or name a mod set under People, else their current server
    name (Discord ratings), else the newest name they rated under.

    Keys are 'user-id' for Discord ratings and 'name:sam' for ratings imported by name.
    `members` maps user IDs to current display names."""
    members = members or {}
    latest: dict[str, tuple[float, int, str]] = {}
    n = 0
    for e in lib["history"]:
        for k, r in e.get("ratings", {}).items():
            n += 1
            stamp = (r.get("at") or 0, n, r.get("name") or "?")
            if k not in latest or stamp[:2] >= latest[k][:2]:
                latest[k] = stamp
    names = {k: v[2] for k, v in latest.items()}
    for k in names:
        if k.isdigit() and k in members:
            names[k] = members[k]
    for k, v in lib.get("people", {}).items():
        if not v:
            continue
        if v.startswith("@") and v[1:].isdigit():
            uid = v[1:]
            names[k] = members.get(uid) or names.get(uid) or "A former member"
        else:
            names[k] = v
    return names


def set_people(lib: dict[str, Any], mapping: dict[str, Any]) -> int:
    """Save 'counts as' names for rater keys. A blank name goes back to the name as written."""
    people = lib.setdefault("people", {})
    known = {k for e in lib["history"] for k in e.get("ratings", {})}
    changed = 0
    for key, name in mapping.items():
        key = str(key)[:80]
        if key not in known and key not in people:
            continue
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:40]
        if name.startswith("@") and not name[1:].isdigit():
            name = name[1:]  # only '@<user id>' links a member; '@sam' is just a name
        if name and people.get(key) != name:
            people[key] = name
            changed += 1
        elif not name and key in people:
            people.pop(key)
            changed += 1
    return changed


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
def _series_key(text: str) -> str:
    """'Movie Mondays', 'movie monday' and 'MOVIE-MONDAY' all give 'moviemonday'."""
    key = re.sub(r"[^a-z0-9]", "", (text or "").casefold())
    return key[:-1] if key.endswith("s") and len(key) > 3 else key


def series_name(session: str, known: list[str], full_names: dict[str, str] | None = None) -> str:
    """Which series a spreadsheet's session column means: its short name ('MMM') or its full name
    ('Movie Mondays', from the series settings). Anything else is kept as written."""
    key = _series_key(session)
    for k in known:
        if key and key in (_series_key(k), _series_key((full_names or {}).get(k, ""))):
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
    if len(min(a, b, key=len)) >= 8 and (a.startswith(b) or b.startswith(a)):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def import_history(
    lib: dict[str, Any],
    sheet: list[dict[str, Any]],
    doc: list[dict[str, Any]],
    known_series: list[str],
    members: dict[str, str] | None = None,
    full_names: dict[str, str] | None = None,
) -> dict[str, int]:
    """Merge a spreadsheet log and a ratings document into the library. Safe to run again.

    `full_names` maps each series' short name to its full name, to recognise either in the sheet."""
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
        e = new_entry(m, row["date"], series_name(row["session"], known_series, full_names), source="import")
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
