"""Movie details from OMDb: looking movies up, the daily budget, filling in details."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiohttp

from ... import movies, store

if TYPE_CHECKING:
    pass
from .common import STATE, _same_year, log


class DetailsMixin:
    """Part of the movie night cog (MovieNight, in cog.py)."""

    def _http(self) -> aiohttp.ClientSession:
        if self.http is None or self.http.closed:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self.http

    # ------------------------------------------------------------ movie details (OMDb)
    def _budget(self, lib: dict[str, Any]) -> dict[str, Any]:
        """Today's OMDb request count (UTC days, like OMDb's own limit)."""
        b = lib.setdefault("omdb", {})
        today = datetime.now(UTC).date().isoformat()
        if b.get("day") != today:
            b.update(day=today, used=0)
        return b

    def _omdb_paused(self) -> str | None:
        b = movies.load().get("omdb", {})
        return b.get("status") if b.get("pause_until", 0) > time.time() else None

    async def _ask_omdb(
        self, title: str, year: int | str | None, imdb_id: str | None = None, search: bool = False
    ) -> Any:
        """One lookup (or a title search), counted against today's budget. Pauses itself on limit or key errors."""
        key = self.bot.features.secret(movies.SECRET)
        spent = [0]
        status, pause = "", 0.0
        try:
            if search:
                return await movies.omdb_search(self._http(), key, title, year, spent)
            return await movies.omdb_lookup(self._http(), key, title, year, spent, imdb_id)
        except movies.OmdbLimit:
            tomorrow = datetime.now(UTC).replace(hour=0, minute=5, second=0, microsecond=0) + timedelta(days=1)
            status, pause = "OMDb's daily limit was reached. Pingwin carries on tomorrow.", tomorrow.timestamp()
            raise
        except movies.OmdbKeyError:
            status = "OMDb didn't accept the API key. Check it, and that you clicked the link in OMDb's email."
            pause = time.time() + 6 * 3600
            raise
        finally:
            lib = movies.load()
            b = self._budget(lib)
            b["used"] = b.get("used", 0) + spent[0]
            if pause:
                b["pause_until"], b["status"] = pause, status
                log.warning("Movie details paused: %s", status)
            movies.save(lib)

    async def details(
        self, title: str, year: int | str | None = None, imdb_id: str | None = None
    ) -> dict[str, Any] | None:
        """A movie from the library, looked up on OMDb (once) if we don't have its details yet.

        `imdb_id` (picked from the search suggestions) pins down exactly which movie it is."""
        lib = movies.load()
        if imdb_id and not movies.IMDB_ID.fullmatch(imdb_id):
            imdb_id = None
        m = next((x for x in lib["movies"].values() if imdb_id and x.get("imdb_id") == imdb_id), None)
        m = m or _same_year(movies.find_movie(lib, title, year), year)
        key = self.bot.features.secret(movies.SECRET)
        if (m and m.get("looked_up")) or not key or self._omdb_paused():
            return m
        try:
            info = await self._ask_omdb(title, year, imdb_id)
        except (TimeoutError, aiohttp.ClientError, ValueError, movies.OmdbLimit) as e:
            log.warning("Movie lookup failed for %s: %s", title, e)
            return m
        lib = movies.load()
        if m is not None:
            m = lib["movies"].get(m["id"])
            if m is None:
                return None
            if info and movies.plausible(m, info):
                movies.merge_details(m, info)
            else:
                movies.note_miss(m, time.time())
            movies.save(lib)
            return m
        if info is None:
            return None
        m = _same_year(movies.find_movie(lib, info["title"], info.get("year")), info.get("year"))
        m = m or movies.upsert_movie(lib, info)
        movies.merge_details(m, info)
        movies.save(lib)
        return m

    def search_budget(self) -> dict[str, int]:
        """Today's OMDb requests: used, the daily cap, and how many are left for people searching."""
        daily = int(getattr(self.cfg, "omdb_daily", 800) or 800)
        used = int(self._budget(movies.load()).get("used", 0))
        return {"used": used, "daily": daily, "left": max(0, daily - used)}

    def _search_warning(self) -> str:
        b = self.search_budget()
        if b["left"] <= max(10, b["daily"] // 10):
            return (
                f"Heads up: only {b['left']} of today's {b['daily']} movie searches are left. "
                "Suggestions from IMDb stop when they run out and come back tomorrow."
            )
        return ""

    async def search(self, query: str, year: int | None = None) -> dict[str, Any]:
        """Suggestions while someone types a title: our own library first, then OMDb's catalog
        (IMDb's movies), cached for a day so typing the same thing again costs nothing."""
        query = re.sub(r"\s+", " ", query).strip()[:80]
        lib = movies.load()
        out: dict[str, Any] = {"library": movies.search_library(lib, query), "omdb": [], "note": "", "warn": ""}
        if len(query) < 3:
            return out
        out["warn"] = self._search_warning() if self.bot.features.secret(movies.SECRET) else ""
        if not self.bot.features.secret(movies.SECRET):
            out["note"] = "Add an OMDb API key (Features → Movie night) for suggestions from IMDb's catalog."
            return out
        if self._omdb_paused():
            out["note"] = "Suggestions from IMDb's catalog are paused for today."
            return out
        ck = (query.casefold(), year)
        hit = self._search_cache.get(ck)
        if hit and time.time() - hit[0] < 86400:
            out["omdb"] = hit[1]
            return out
        daily = int(getattr(self.cfg, "omdb_daily", 800) or 800)
        if self._budget(lib).get("used", 0) >= daily:
            out["note"] = "Today's OMDb requests are used up. Suggestions are back tomorrow."
            return out
        try:
            found = await self._ask_omdb(query, year, search=True)
        except (TimeoutError, aiohttp.ClientError, ValueError, movies.OmdbLimit) as e:
            log.info("Movie search failed for %r: %s", query, e)
            return out
        if len(self._search_cache) > 500:
            self._search_cache.clear()
        self._search_cache[ck] = (time.time(), found)
        out["omdb"] = found
        return out

    async def find_matches(self, query: str, year: int | None = None) -> list[dict[str, Any]]:
        """IMDb entries for an admin matching a movie by hand (always asks OMDb; cached like search)."""
        query = re.sub(r"\s+", " ", query).strip()[:80]
        if not query or not self.bot.features.secret(movies.SECRET):
            return []
        ck = (query.casefold(), year)
        hit = self._search_cache.get(ck)
        if hit and time.time() - hit[0] < 86400:
            return hit[1]
        found = await self._ask_omdb(query, year, search=True)
        self._search_cache[ck] = (time.time(), found)
        return found

    async def match_imdb(self, kind: str, item_id: str, imdb_id: str, series_key: str = "") -> str:
        """Point a library movie (or a watchlist movie) at the IMDb entry an admin picked."""
        if not movies.IMDB_ID.fullmatch(imdb_id or ""):
            raise ValueError("That isn't an IMDb link or ID (they look like tt0091064).")
        if not self.bot.features.secret(movies.SECRET):
            raise ValueError("Add an OMDb API key first.")
        info = await self._ask_omdb("", None, imdb_id)
        if not info or info.get("imdb_id") != imdb_id:
            raise ValueError("OMDb doesn't know that IMDb ID.")
        lib = movies.load()
        if kind == "movie":
            m = lib["movies"].get(item_id)
            if m is None:
                raise ValueError("That movie isn't in the library anymore.")
            movies.set_match(m, info)
            movies.save(lib)
            return f"{m['title']} now points at {info['title']} ({info.get('year') or '?'}) on IMDb."
        state = store.load(STATE)
        n = next((x for x in state.get(series_key, {}).get("nominations", []) if x["id"] == item_id), None)
        if n is None:
            raise ValueError("That watchlist movie isn't there anymore.")
        m = next((x for x in lib["movies"].values() if x.get("imdb_id") == imdb_id), None)
        m = m or movies.upsert_movie(lib, {**info, "title": n["title"], "year": n.get("year") or info.get("year")})
        movies.set_match(m, info)
        movies.save(lib)
        n["movie_id"] = m["id"]
        n["year"] = n.get("year") or m.get("year")
        n.pop("omdb_tries", None)
        store.save(STATE, state)
        return f"{n['title']} now points at {info['title']} ({info.get('year') or '?'}) on IMDb."

    async def fill_details(self, max_requests: int) -> dict[str, Any]:
        """Look up movies that are missing details, a few at a time, within today's budget."""
        if not self.bot.features.secret(movies.SECRET):
            return {"looked": 0, "found": 0, "message": "Add an OMDb API key first."}
        async with self._filling:
            paused = self._omdb_paused()
            if paused:
                return {"looked": 0, "found": 0, "message": paused}
            daily = int(getattr(self.cfg, "omdb_daily", 800) or 800)
            reserve = max(20, daily // 8)  # left for people searching titles on the movie pages and in Discord
            lib = movies.load()
            used0 = self._budget(lib).get("used", 0)
            lib.get("omdb", {}).pop("status", None)
            movies.save(lib)
            todo = movies.details_todo(lib, store.load(STATE), time.time())
            looked = found = 0
            for item in todo:
                used = self._budget(movies.load()).get("used", 0)
                if used - used0 + 4 > max_requests or used + 4 > daily - reserve:
                    break
                title, year = item[-2], item[-1]
                known = movies.load()["movies"].get(item[1], {}) if item[0] == "movie" else {}
                try:  # an IMDb ID (say, from an IMDb list) finds exactly the right movie in one request
                    info = await self._ask_omdb(title, year, known.get("imdb_id"))
                except (movies.OmdbLimit, movies.OmdbKeyError):
                    break
                except (TimeoutError, aiohttp.ClientError) as e:
                    log.info("OMDb didn't answer (%s); trying again later", e)
                    break
                looked += 1
                now = time.time()
                if item[0] == "movie":
                    lib = movies.load()
                    m = lib["movies"].get(item[1])
                    if m is not None:
                        if info and movies.plausible(m, info):
                            movies.merge_details(m, info)
                            found += 1
                        else:
                            movies.note_miss(m, now)
                        movies.save(lib)
                else:
                    state = store.load(STATE)
                    n = next((x for x in state.get(item[1], {}).get("nominations", []) if x["id"] == item[2]), None)
                    if n is not None:
                        if info and movies.plausible(n, info):
                            lib = movies.load()
                            # Not a namesake from another year (The Thing 2011 isn't the 1982 one).
                            m = _same_year(
                                movies.find_movie(lib, info["title"], info.get("year")), info.get("year")
                            ) or movies.upsert_movie(lib, info)
                            movies.merge_details(m, info)
                            movies.save(lib)
                            n["movie_id"] = m["id"]
                            n["year"] = n.get("year") or m.get("year")
                            n.pop("omdb_tries", None)
                            found += 1
                        else:
                            movies.note_miss(n, now)
                        store.save(STATE, state)
                await asyncio.sleep(0.3)
            if looked:
                log.info("Movie details: looked up %d, found %d", looked, found)
            left = movies.details_progress(movies.load(), store.load(STATE))["left"]
            return {
                "looked": looked,
                "found": found,
                "message": self._omdb_paused() or f"Looked up {looked} movies, found {found}. {left} left.",
            }
