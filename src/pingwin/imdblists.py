"""IMDb lists, compared with the movie night history and watchlists.

IMDb has no public API, so lists come in as the CSV file IMDb itself makes: open the list on IMDb,
then ⋯ → Export (or Your ratings → Export). Pingwin keeps a copy in the movie library and shows,
for each movie on the list, whether we've watched it (and when), whether it's on a watchlist, or
neither. It also fills in details our library is missing (IMDb ID, runtime, rating, genre, director).
"""

from __future__ import annotations

import csv
import io
import re
import time
from typing import Any

from . import movies

LIST_ID = re.compile(r"\b(ls\d{6,12}|ur\d{6,12})\b")
MAX_ITEMS = 5000


def _num(v: str | None) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_csv(text: str) -> list[dict[str, Any]]:
    """Movies from an IMDb list or ratings export (any column order; extra columns are ignored)."""
    text = text.lstrip("﻿")
    rows = csv.DictReader(io.StringIO(text))
    if not rows.fieldnames:
        return []
    cols = {(f or "").strip().casefold(): f for f in rows.fieldnames}

    def get(row: dict[str, str], *names: str) -> str:
        for n in names:
            f = cols.get(n)
            if f and (row.get(f) or "").strip():
                return row[f].strip()
        return ""

    if "title" not in cols:
        raise ValueError("That doesn't look like an IMDb export (there's no Title column).")
    out = []
    for row in rows:
        title = get(row, "title")
        if not title:
            continue
        kind = get(row, "title type").casefold()
        if kind and not any(k in kind for k in ("movie", "film", "video", "short", "special")):
            continue  # skip TV series and episodes
        year = re.match(r"\d{4}", get(row, "year"))
        runtime = _num(get(row, "runtime (mins)", "runtime"))
        const = get(row, "const", "tconst")
        out.append(
            {
                "imdb_id": const if movies.IMDB_ID.fullmatch(const) else None,
                "title": title[:150],
                "year": int(year[0]) if year else None,
                "position": int(_num(get(row, "position")) or 0) or None,
                "added": get(row, "created", "date rated", "date added")[:10] or None,
                "your_rating": _num(get(row, "your rating")),
                "imdb_rating": _num(get(row, "imdb rating")),
                "runtime": int(runtime) if runtime else None,
                "genre": get(row, "genres") or None,
                "director": get(row, "directors", "director") or None,
            }
        )
        if len(out) >= MAX_ITEMS:
            break
    return out


def list_id(url: str, name: str) -> str:
    m = LIST_ID.search(url or "")
    return m[1] if m else "n-" + (movies.norm(name) or "list")[:40]


def save_list(lib: dict[str, Any], name: str, url: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep (or replace) a list, and fill in what library movies are missing from it."""
    name = re.sub(r"\s+", " ", name).strip()[:60] or "IMDb list"
    url = url.strip() if re.fullmatch(r"https://(www\.|m\.)?imdb\.com/\S{1,200}", url.strip()) else ""
    lid = list_id(url, name)
    lists = lib.setdefault("imdb_lists", {})
    lists[lid] = {"name": name, "url": url, "imported": time.time(), "items": items}
    filled = 0
    for it in items:
        m = match(lib, it)
        if m is None:
            continue
        info = {k: it.get(k) for k in ("imdb_id", "runtime", "imdb_rating", "genre", "director")}
        before = {k: m.get(k) for k in info}
        for k, v in info.items():
            if v not in (None, "") and m.get(k) in (None, ""):
                m[k] = v
        filled += before != {k: m.get(k) for k in info}
    return {"id": lid, "name": name, "items": len(items), "filled": filled}


def match(lib: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    """The library movie a list item is (by IMDb ID, else title and year)."""
    if item.get("imdb_id"):
        for m in lib["movies"].values():
            if m.get("imdb_id") == item["imdb_id"]:
                return m
    m = movies.find_movie(lib, item["title"], item.get("year"))
    if m and item.get("year") and m.get("year") and abs(int(m["year"]) - int(item["year"])) > 1:
        return None  # a remake or a namesake
    return m


def compare(lib: dict[str, Any], lst: dict[str, Any], watchlists: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Each list movie with where it stands for us, plus the movies we watched that aren't on the list.

    `watchlists` maps each series to its nominations (from the movie night state)."""
    nights: dict[str, list[dict[str, Any]]] = {}
    for e in lib["history"]:
        nights.setdefault(e["movie_id"], []).append(e)
    waiting: dict[str, list[str]] = {}
    for series, noms in watchlists.items():
        for n in noms:
            if n.get("watched"):
                continue
            key = n.get("movie_id") or movies.norm(n.get("title", ""))
            waiting.setdefault(key, []).append(series)
    items, on_list = [], set()
    for it in lst.get("items", []):
        m = match(lib, it)
        seen = nights.get(m["id"], []) if m else []
        lists = (waiting.get(m["id"], []) if m else []) or waiting.get(movies.norm(it["title"]), [])
        if m:
            on_list.add(m["id"])
        items.append(
            {
                **{k: it.get(k) for k in ("title", "year", "imdb_id", "position", "added", "your_rating")},
                "poster": (m or {}).get("poster"),
                "watched": [
                    {"date": e.get("date"), "series": e.get("series"), "avg": movies.ratings_avg(e), "hid": e["id"]}
                    for e in seen
                ],
                "lists": sorted(set(lists)),
            }
        )
    missing = []
    for mid, es in nights.items():
        if mid in on_list:
            continue
        m = lib["movies"].get(mid, {})
        last = es[-1]
        missing.append(
            {
                "title": m.get("title", "?"),
                "year": m.get("year"),
                "date": last.get("date"),
                "series": last.get("series"),
            }
        )
    missing.sort(key=lambda x: x.get("date") or "", reverse=True)
    watched = sum(1 for i in items if i["watched"])
    listed = sum(1 for i in items if not i["watched"] and i["lists"])
    return {
        "items": items,
        "missing": missing,
        "counts": {"total": len(items), "watched": watched, "listed": listed, "neither": len(items) - watched - listed},
    }


def preview(
    lib: dict[str, Any], items: list[dict[str, Any]], watchlists: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """What each row of an export would be, before anything is saved: the library movie it matches,
    whether we watched it or it's on a watchlist, and which details it would fill in."""
    out = compare(lib, {"items": items}, watchlists)["items"]
    for i, (it, row) in enumerate(zip(items, out, strict=True)):
        m = match(lib, it)
        row["row"] = i
        row["match"] = {"title": m["title"], "year": m.get("year")} if m else None
        row["fills"] = (
            [k for k in ("imdb_id", "runtime", "imdb_rating", "genre", "director") if it.get(k) and not m.get(k)]
            if m
            else []
        )
        row["kind"] = it.get("kind")
    return out
