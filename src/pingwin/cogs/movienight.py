"""Movie night: watchlists, polls that close at showtime, a history of every movie watched, and ratings.

* Each series (like MMM or FFF) has its own watchlist, role, channel and weekly showtime.
* Members' changes (nominations, removals, watched marks, history fixes) can need a mod's OK: Pingwin posts
  a card with Approve / Deny in the mods' channel. Mods' own changes go straight through.
* After a movie, Pingwin posts a "Rate it" button. Everyone gives a score out of 10, a comment, and can pass
  a silly question on to the next person. When ratings close, Pingwin posts the group average.
* Movie details (year, runtime, IMDb rating, director, stars, poster) come from OMDb, looked up once.

The watchlists live in state/movienight.json and the library (history, ratings, details, requests) in
state/movielib.json (see pingwin.movies).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import secrets
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import movies, store
from ..features import slug
from ..timeparse import next_weekly

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.movienight")
STATE = "movienight"
ANSWER_LIMIT = 55  # Discord's limit for poll answer text
DEFAULT_RUNTIME = 120  # minutes, when we don't know the movie's length
GOLD = discord.Color.gold()


def pick_options(noms: list[dict], how: str, count: int) -> list[dict]:
    unwatched = [n for n in noms if not n.get("watched")]
    if how == "random":
        return random.sample(unwatched, min(count, len(unwatched)))
    return sorted(unwatched, key=lambda n: n["added"])[:count]


SOONEST = timedelta(minutes=5)  # a showtime closer than this means next week's


def poll_close(showtime: str, now: datetime, hours: int | None, default_hours: int) -> tuple[datetime, int]:
    """When a poll should close, and the whole-hour duration to give Discord (it only takes hours).

    Explicit hours win. Otherwise, with a showtime, the poll closes at the next showtime and Pingwin ends
    it then (Discord's own timer runs up to an hour longer). Otherwise the default length.
    """
    if hours is None and showtime:
        start = next_weekly(showtime, now)
        if start is not None:
            if start - now < SOONEST:
                start += timedelta(days=7)
            return start, max(1, min(768, math.ceil((start - now).total_seconds() / 3600)))
    h = hours or default_hours
    return now + timedelta(hours=h), h


def winners(counts: list[tuple[str, int]]) -> list[str]:
    """IDs with the most votes (several on a tie); empty if nobody voted."""
    if not counts or max(c for _, c in counts) == 0:
        return []
    top = max(c for _, c in counts)
    return [i for i, c in counts if c == top]


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
        return f"Mark **{d['title']}** as {'watched' if d.get('watched', True) else 'not watched'} ({req['series']})"
    if kind == "tonight":
        return f"Set tonight's {req['series']} movie to **{d['title']}**"
    if kind == "edit":
        changes = ", ".join(f"{k}: {v}" for k, v in d.get("changes", {}).items())
        return f"Fix **{d.get('label', 'a movie night')}** in the history: {changes}"
    return kind


# --------------------------------------------------------------------- buttons
class RequestButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:mvq:(?P<rid>[0-9a-f]+):(?P<act>ok|no)"
):
    def __init__(self, rid: str, act: str) -> None:
        ok = act == "ok"
        super().__init__(
            discord.ui.Button(
                label="Approve" if ok else "Deny",
                style=discord.ButtonStyle.success if ok else discord.ButtonStyle.danger,
                custom_id=f"pingwin:mvq:{rid}:{act}",
            )
        )
        self.rid, self.act = rid, act

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["rid"], match["act"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        if cog is None:
            return
        if not cog.bot.is_mod(interaction.user):
            await interaction.response.send_message("Only mods can approve these.", ephemeral=True)
            return
        await interaction.response.defer()
        msg = await cog.decide(self.rid, self.act == "ok", interaction.user)
        try:
            await interaction.edit_original_response(content=msg, view=None)
        except discord.HTTPException:
            pass


class RateButton(discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:rate:(?P<hid>[0-9a-f]+)"):
    def __init__(self, hid: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Rate it", emoji="⭐", style=discord.ButtonStyle.primary, custom_id=f"pingwin:rate:{hid}"
            )
        )
        self.hid = hid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["hid"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        await open_rating(interaction, self.hid)


async def open_rating(interaction: discord.Interaction, hid: str) -> None:
    lib = movies.load()
    entry = next((e for e in lib["history"] if e["id"] == hid), None)
    if entry is None:
        await interaction.response.send_message("I can't find that movie night anymore.", ephemeral=True)
        return
    if entry.get("prompt", {}).get("closed"):
        await interaction.response.send_message("Ratings for this one have closed.", ephemeral=True)
        return
    movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
    await interaction.response.send_modal(RateModal(hid, movie, entry, str(interaction.user.id)))


class RateModal(discord.ui.Modal):
    def __init__(self, hid: str, movie: dict[str, Any], entry: dict[str, Any], uid: str) -> None:
        super().__init__(title=f"Rate {movie['title']}"[:45])
        self.hid = hid
        mine = entry.get("ratings", {}).get(uid, {})
        question = movies.open_question({"ratings": {k: v for k, v in entry.get("ratings", {}).items() if k != uid}})
        self.question = question
        self.score = discord.ui.TextInput(
            label="Your score out of 10",
            placeholder="Like 7 or 7.5",
            max_length=4,
            default=None if mine.get("score") is None else f"{mine['score']:g}",
        )
        self.comment = discord.ui.TextInput(
            label="What did you think?",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            default=mine.get("comment") or None,
        )
        self.add_item(self.score)
        self.add_item(self.comment)
        self.answer = None
        if question:
            self.answer = discord.ui.TextInput(
                label="The last person asked you…",
                placeholder=question[:100],
                required=False,
                max_length=300,
                default=mine.get("answer") or None,
            )
            self.add_item(self.answer)
        self.ask = discord.ui.TextInput(
            label="Ask the next person a question",
            placeholder="How many ___ out of ___?",
            required=False,
            max_length=200,
            default=mine.get("ask") or None,
        )
        self.add_item(self.ask)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        score = movies.parse_score(str(self.score.value))
        if score is None:
            await interaction.response.send_message("The score needs to be a number from 0 to 10.", ephemeral=True)
            return
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        lib = movies.load()
        entry = next((e for e in lib["history"] if e["id"] == self.hid), None)
        if entry is None or entry.get("prompt", {}).get("closed"):
            await interaction.response.send_message("Ratings for this one have closed.", ephemeral=True)
            return
        user = interaction.user
        movies.add_rating(
            entry,
            str(user.id),
            name=getattr(user, "display_name", user.name),
            user_id=str(user.id),
            score=score,
            comment=str(self.comment.value or ""),
            answered=self.question if self.answer else "",
            answer=str(self.answer.value or "") if self.answer else "",
            ask=str(self.ask.value or ""),
        )
        movies.save(lib)
        await interaction.response.send_message(f"Thanks! You gave it **{score:g}/10**. ⭐", ephemeral=True)
        if cog:
            await cog.refresh_prompt(entry, lib)


# A watchlist for anything that isn't for a particular series. No role, channel or showtime of its own:
# its polls go to the channel they're started in.
GENERAL = SimpleNamespace(name="General", full_name="General", role="", channel=None, showtime="")


# --------------------------------------------------------------------- the cog
class MovieNight(commands.GroupCog, group_name="movie", group_description="Movie nights: watchlists, polls, history"):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.http: aiohttp.ClientSession | None = None
        self._filling = asyncio.Lock()
        self.check.start()
        self.fill_loop.start()

    def cog_unload(self) -> None:
        self.check.cancel()
        self.fill_loop.cancel()
        if self.http and not self.http.closed:
            asyncio.ensure_future(self.http.close())

    @property
    def cfg(self):
        return self.bot.features.movienight

    # ------------------------------------------------------------ helpers
    @property
    def all_series(self) -> tuple:
        """The configured series, plus the built-in General watchlist (unless a series is called that)."""
        mine = tuple(s for s in self.cfg.series if s.name)
        if any(slug(s.name) == slug(GENERAL.name) for s in mine):
            return mine
        return (*mine, GENERAL)

    def series(self, name: str):
        key = slug(name or "")
        return next(
            (
                s
                for s in self.all_series
                if slug(s.name) == key or (getattr(s, "full_name", "") and slug(s.full_name) == key)
            ),
            None,
        )

    def _allowed(self, member: discord.abc.User, mode: str, role: str) -> bool:
        if mode == "everyone":
            return True
        if self.bot.is_mod(member):
            return True
        if mode == "role" and isinstance(member, discord.Member) and role:
            return any(r.name.casefold() == role.casefold() for r in member.roles)
        return False

    async def _check(self, interaction: discord.Interaction, series_name: str):
        if not self.cfg.enabled:
            await interaction.response.send_message("Movie night isn't turned on.", ephemeral=True)
            return None
        s = self.series(series_name)
        if s is None:
            names = ", ".join(x.name for x in self.all_series)
            await interaction.response.send_message(f"Pick a series: {names}.", ephemeral=True)
        return s

    @staticmethod
    def _data(state: dict, s) -> dict:
        return state.setdefault(slug(s.name), {"nominations": [], "polls": []})

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

    async def _ask_omdb(self, title: str, year: int | str | None) -> dict[str, Any] | None:
        """One lookup, counted against today's budget. Pauses itself on limit or key errors."""
        key = self.bot.features.secret(movies.SECRET)
        spent = [0]
        status, pause = "", 0.0
        try:
            return await movies.omdb_lookup(self._http(), key, title, year, spent)
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

    async def details(self, title: str, year: int | str | None = None) -> dict[str, Any] | None:
        """A movie from the library, looked up on OMDb (once) if we don't have its details yet."""
        lib = movies.load()
        m = movies.find_movie(lib, title, year)
        key = self.bot.features.secret(movies.SECRET)
        if (m and m.get("looked_up")) or not key or self._omdb_paused():
            return m
        try:
            info = await self._ask_omdb(title, year)
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
        m = movies.find_movie(lib, info["title"], info.get("year")) or movies.upsert_movie(lib, info)
        movies.merge_details(m, info)
        movies.save(lib)
        return m

    async def fill_details(self, max_requests: int) -> dict[str, Any]:
        """Look up movies that are missing details, a few at a time, within today's budget."""
        if not self.bot.features.secret(movies.SECRET):
            return {"looked": 0, "found": 0, "message": "Add an OMDb API key first."}
        async with self._filling:
            paused = self._omdb_paused()
            if paused:
                return {"looked": 0, "found": 0, "message": paused}
            daily = int(getattr(self.cfg, "omdb_daily", 800) or 800)
            lib = movies.load()
            used0 = self._budget(lib).get("used", 0)
            lib.get("omdb", {}).pop("status", None)
            movies.save(lib)
            todo = movies.details_todo(lib, store.load(STATE), time.time())
            looked = found = 0
            for item in todo:
                used = self._budget(movies.load()).get("used", 0)
                if used - used0 + 3 > max_requests or used + 3 > daily:
                    break
                title, year = item[-2], item[-1]
                try:
                    info = await self._ask_omdb(title, year)
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
                            m = movies.find_movie(lib, info["title"], info.get("year")) or movies.upsert_movie(
                                lib, info
                            )
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

    @tasks.loop(minutes=20)
    async def fill_loop(self) -> None:
        """Fill in movie details in the background, spread over the day (72 runs)."""
        if not (self.cfg.enabled and self.bot.is_active and getattr(self.cfg, "auto_details", True)):
            return
        if not self.bot.features.secret(movies.SECRET):
            return
        daily = int(getattr(self.cfg, "omdb_daily", 800) or 800)
        try:
            await self.fill_details(max(6, daily // 72))
        except Exception:
            log.exception("Filling in movie details failed")

    @fill_loop.before_loop
    async def _wait_fill(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)  # let startup settle first

    def page_url(self) -> str | None:
        links = store.load("dashboards")
        if not links:
            return None
        mine = links.get(self.bot.node.name)
        info = mine or next(iter(links.values()))
        return f"{info['url']}/movies"

    def approval_channel(self) -> discord.TextChannel | None:
        return self.bot.text_channel(self.cfg.approval_channel) or self.bot.text_channel(
            self.bot.features.welcome.approval_channel
        )

    # ------------------------------------------------------------ changes (with approvals)
    async def submit(self, kind: str, series: str, data: dict[str, Any], who: discord.abc.User) -> str:
        """Apply a change now (mods, or approvals off) or send it to the mods. Returns a reply."""
        if not self.cfg.approvals or self.bot.is_mod(who):
            return await self.apply(kind, series, data, who.id)
        channel = self.approval_channel()
        if channel is None:
            return "Changes need a mod's OK, but no approval channel is set up. Ask a mod."
        lib = movies.load()
        rid = secrets.token_hex(4)
        req = {
            "id": rid,
            "kind": kind,
            "series": series,
            "data": data,
            "by": who.id,
            "by_name": getattr(who, "display_name", who.name),
            "at": time.time(),
            "status": "pending",
        }
        view = discord.ui.View(timeout=None)
        view.add_item(RequestButton(rid, "ok"))
        view.add_item(RequestButton(rid, "no"))
        embed = discord.Embed(title="🎬 Movie night change", description=describe_request(req), color=GOLD)
        embed.set_footer(text=f"Asked by {req['by_name']}")
        if data.get("poster"):
            embed.set_thumbnail(url=data["poster"])
        try:
            msg = await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            log.warning("Couldn't post an approval card: %s", e)
            return "I couldn't reach the mods' channel. Ask a mod."
        req["card"] = [channel.id, msg.id]
        lib["pending"][rid] = req
        movies.save(lib)
        log.info("Movie change from %s sent for approval: %s", req["by_name"], describe_request(req))
        return "Sent to the mods for approval. 👍"

    async def decide(self, rid: str, approve: bool, mod: discord.abc.User) -> str:
        lib = movies.load()
        req = lib["pending"].get(rid)
        if req is None or req.get("status") != "pending":
            return "This one was already handled."
        req["status"] = "approved" if approve else "denied"
        req["decided_by"] = getattr(mod, "display_name", mod.name)
        movies.save(lib)
        line = describe_request(req)
        if not approve:
            log.info("%s denied: %s", req["decided_by"], line)
            msg = f"❌ Denied by {req['decided_by']}: {line} (asked by {req['by_name']})"
        else:
            result = await self.apply(req["kind"], req["series"], req["data"], req["by"])
            log.info("%s approved: %s", req["decided_by"], line)
            msg = f"✅ Approved by {req['decided_by']}: {line} (asked by {req['by_name']}). {result}"
        card = req.get("card")
        channel = self.bot.get_channel(card[0]) if card else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.get_partial_message(card[1]).edit(content=msg, embed=None, view=None)
            except discord.HTTPException:
                pass
        return msg

    def import_list(self, series: str, rows: list[dict[str, Any]], by: int, label: str = "") -> dict[str, int]:
        """Add a list's waiting movies to a watchlist at once (no channel posts). Skips watched ones."""
        s = self.series(series)
        if s is None:
            raise ValueError(f"There's no {series} series.")
        state = store.load(STATE)
        noms = self._data(state, s)["nominations"]
        lib = movies.load()
        seen = {movies.norm(n["title"]) for n in noms if not n.get("watched")}
        watched = {
            movies.norm(lib["movies"][e["movie_id"]]["title"]) for e in lib["history"] if e["movie_id"] in lib["movies"]
        }
        stats = {"added": 0, "watched": 0, "already": 0}
        for r in rows:
            key = movies.norm(r["title"])
            if r.get("done") or key in watched:
                stats["watched"] += 1
                continue
            if key in seen:
                stats["already"] += 1
                continue
            seen.add(key)
            found = movies.find_movie(lib, r["title"], r.get("year"))
            noms.append(
                {
                    "id": secrets.token_hex(4),
                    "title": r["title"],
                    "year": r.get("year"),
                    "movie_id": found["id"] if found else None,
                    "by": by,
                    "added": discord.utils.utcnow().isoformat(),
                    "watched": None,
                    "list": label[:40],
                    "note": r.get("note", ""),
                }
            )
            stats["added"] += 1
        store.save(STATE, state)
        return stats

    async def apply(self, kind: str, series: str, data: dict[str, Any], by: int) -> str:
        s = self.series(series)
        if kind == "edit":
            return self._apply_edit(data)
        if s is None:
            return f"There's no {series} series anymore."
        state = store.load(STATE)
        noms = self._data(state, s)["nominations"]
        if kind == "nominate":
            if any(movies.norm(n["title"]) == movies.norm(data["title"]) and not n.get("watched") for n in noms):
                return f"**{data['title']}** is already on the watchlist."
            noms.append(
                {
                    "id": secrets.token_hex(4),
                    "title": data["title"],
                    "year": data.get("year"),
                    "movie_id": data.get("movie_id"),
                    "by": by,
                    "added": discord.utils.utcnow().isoformat(),
                    "watched": None,
                }
            )
            store.save(STATE, state)
            channel = self.bot.text_channel(s.channel)
            if channel:
                try:
                    await channel.send(
                        f"🎬 **{data['title']}** joined the {s.name} watchlist (suggested by <@{by}>).",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass
            return f"Added **{data['title']}** to the {s.name} watchlist."
        n = next((x for x in noms if x["id"] == data.get("id")), None)
        if kind == "remove":
            if n is None:
                return "That movie isn't on the list anymore."
            noms.remove(n)
            store.save(STATE, state)
            return f"Removed **{n['title']}** from the {s.name} watchlist."
        if kind == "watched":
            if n is None:
                return "That movie isn't on the list anymore."
            n["watched"] = discord.utils.utcnow().isoformat() if data.get("watched", True) else None
            store.save(STATE, state)
            if n["watched"]:
                await self.log_watched(s, n["title"], n.get("year"), when=datetime.now(self.bot.features.tz))
            return f"**{n['title']}** marked as {'watched' if n['watched'] else 'not watched'}."
        if kind == "tonight":
            showtime = next_weekly(
                getattr(s, "showtime", "") or "", datetime.now(self.bot.features.tz) - timedelta(hours=6)
            )
            entry = await self.log_watched(
                s, data["title"], data.get("year"), when=showtime or datetime.now(self.bot.features.tz)
            )
            return f"Tonight's {s.name} movie is **{data['title']}**." + (
                " I'll ask everyone to rate it afterwards." if entry and self.cfg.rate else ""
            )
        return "Unknown change."

    def _apply_edit(self, data: dict[str, Any]) -> str:
        lib = movies.load()
        e = next((x for x in lib["history"] if x["id"] == data.get("hid")), None)
        if e is None:
            return "That movie night isn't in the history anymore."
        ch = data.get("changes", {})
        if "date" in ch:
            e["date"] = ch["date"] or None
        if "series" in ch:
            e["series"] = ch["series"]
        if "title" in ch or "year" in ch:
            old = lib["movies"].get(e["movie_id"], {})
            m = movies.find_movie(lib, ch.get("title", old.get("title", "")), ch.get("year", old.get("year")))
            if m is None:
                m = movies.upsert_movie(
                    lib, {"title": ch.get("title", old.get("title")), "year": ch.get("year", old.get("year"))}
                )
            e["movie_id"] = m["id"]
        if "poster" in ch and e["movie_id"] in lib["movies"]:
            lib["movies"][e["movie_id"]]["poster"] = ch["poster"] or None
        movies.save(lib)
        return "History updated."

    async def log_watched(self, s, title: str, year: Any, when: datetime) -> dict[str, Any] | None:
        """Add a movie night to the history and schedule its Rate it button."""
        m = await self.details(title, year)
        lib = movies.load()
        if m is None:
            m = movies.upsert_movie(lib, {"title": title, "year": year})
        day = when.date().isoformat()
        e = next((x for x in lib["history"] if x.get("date") == day and x["movie_id"] == m["id"]), None)
        if e is None:
            e = movies.new_entry(m, day, s.name)
            lib["history"].append(e)
        if self.cfg.rate and not e.get("prompt"):
            runtime = m.get("runtime") or DEFAULT_RUNTIME
            start = when if when.timestamp() > time.time() - 6 * 3600 else datetime.now(self.bot.features.tz)
            e["rate_at"] = start.timestamp() + (runtime + 10) * 60
            if e["rate_at"] < time.time():  # already over: ask now
                e["rate_at"] = time.time()
            e["rate_channel"] = s.channel
        movies.save(lib)
        return e

    # ------------------------------------------------------------ ratings
    def _prompt_embed(
        self, entry: dict[str, Any], movie: dict[str, Any], closes: float, closed: bool = False
    ) -> discord.Embed:
        people = movies.load().get("people", {})
        rs = sorted(entry.get("ratings", {}).items(), key=lambda kv: kv[1].get("at", 0))
        avg = movies.ratings_avg(entry)
        lines = [
            f"**{people.get(k) or r['name']}**: {r['score']:g}/10"
            for k, r in rs
            if isinstance(r.get("score"), (int, float))
        ]
        desc = (
            "Ratings are closed."
            if closed
            else f"How was it? Rate it out of 10 and say what you thought. Ratings close <t:{int(closes)}:R>."
        )
        if lines:
            desc += "\n\n" + "\n".join(lines)
        if avg is not None:
            desc += f"\n\n**Group average: {avg:g}/10**" + (
                f" · IMDb {movie['imdb_rating']:g}" if movie.get("imdb_rating") else ""
            )
        embed = discord.Embed(title=f"⭐ {movies.label(movie)}", description=desc[:4000], color=GOLD)
        if movie.get("poster"):
            embed.set_thumbnail(url=movie["poster"])
        q = movies.open_question(entry)
        if q and not closed:
            embed.set_footer(text=f"Next person's question: {q}"[:2000])
        return embed

    async def post_prompt(self, entry: dict[str, Any], lib: dict[str, Any]) -> None:
        channel = self.bot.text_channel(entry.get("rate_channel"))
        if channel is None:
            entry["prompt"] = {"skipped": True}
            return
        movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
        closes = time.time() + self.cfg.rate_hours * 3600
        view = discord.ui.View(timeout=None)
        view.add_item(RateButton(entry["id"]))
        try:
            msg = await channel.send(embed=self._prompt_embed(entry, movie, closes), view=view)
        except discord.HTTPException as e:
            log.warning("Couldn't post the Rate it button: %s", e)
            entry["prompt"] = {"skipped": True}
            return
        entry["prompt"] = {"channel": channel.id, "message": msg.id, "closes": closes, "closed": False}
        log.info("Asked everyone to rate %s", movie.get("title"))

    async def refresh_prompt(self, entry: dict[str, Any], lib: dict[str, Any]) -> None:
        p = entry.get("prompt") or {}
        channel = self.bot.get_channel(p.get("channel")) if p.get("channel") else None
        if not isinstance(channel, discord.TextChannel):
            return
        movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
        try:
            await channel.get_partial_message(p["message"]).edit(
                embed=self._prompt_embed(entry, movie, p["closes"], p.get("closed", False)),
                view=None if p.get("closed") else discord.utils.MISSING,
            )
        except discord.HTTPException:
            pass

    async def close_prompt(self, entry: dict[str, Any], lib: dict[str, Any]) -> None:
        p = entry["prompt"]
        p["closed"] = True
        await self.refresh_prompt(entry, lib)
        channel = self.bot.get_channel(p.get("channel"))
        movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
        avg = movies.ratings_avg(entry)
        n = sum(1 for r in entry.get("ratings", {}).values() if isinstance(r.get("score"), (int, float)))
        if not isinstance(channel, discord.TextChannel) or not n:
            return
        text = f"📊 **{movies.label(movie)}**: {n} rating{'s' if n != 1 else ''}, group average **{avg:g}/10**"
        if movie.get("imdb_rating"):
            diff = avg - movie["imdb_rating"]
            verdict = "kinder" if diff > 0 else "harsher" if diff < 0 else "spot on"
            text += f" (IMDb {movie['imdb_rating']:g}, we were {verdict})"
        url = self.page_url()
        if url:
            text += f"\nAll the comments: <{url}>"
        try:
            await channel.get_partial_message(p["message"]).reply(text, mention_author=False)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------ autocomplete
    async def series_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=s.name, value=s.name)
            for s in self.all_series
            if s.name and current.casefold() in s.name.casefold()
        ][:25]

    async def title_autocomplete(self, interaction: discord.Interaction, current: str):
        s = self.series(getattr(interaction.namespace, "series", "") or "")
        if s is None:
            return []
        noms = self._data(store.load(STATE), s)["nominations"]
        return [
            app_commands.Choice(name=(("✓ " if n.get("watched") else "") + n["title"])[:100], value=n["id"])
            for n in noms
            if current.casefold() in n["title"].casefold()
        ][:25]

    # ------------------------------------------------------------ commands
    @app_commands.command(name="nominate", description="Suggest a movie for a series' watchlist")
    @app_commands.describe(
        series="Which movie night", title="The movie (or franchise)", year="Optional, helps find the right one"
    )
    @app_commands.autocomplete(series=series_autocomplete)
    async def nominate(
        self,
        interaction: discord.Interaction,
        series: str,
        title: app_commands.Range[str, 1, 100],
        year: app_commands.Range[int, 1888, 2100] | None = None,
    ) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self.nominate_text(interaction.user, s, title, year), ephemeral=True)

    async def nominate_text(self, member: discord.abc.User, s, title: str, year: int | None = None) -> str:
        """Shared by /pingwin movie nominate and -pingwin movie nominate."""
        if not self._allowed(member, self.cfg.nominate, self.cfg.nominate_role):
            return "You can't nominate movies here."
        title = title.strip()
        noms = self._data(store.load(STATE), s)["nominations"]
        if any(movies.norm(n["title"]) == movies.norm(title) and not n.get("watched") for n in noms):
            return f"**{title}** is already on the {s.name} watchlist."
        mine = [n for n in noms if n["by"] == member.id and not n.get("watched")]
        if self.cfg.max_per_person and len(mine) >= self.cfg.max_per_person and not self.bot.is_mod(member):
            return (
                f"You already have {len(mine)} nominations waiting (the limit is {self.cfg.max_per_person}). "
                "Remove one with `/pingwin movie remove` first."
            )
        m = await self.details(title, year)
        note = ""
        lib = movies.load()
        seen = [e for e in lib["history"] if m and e["movie_id"] == m["id"]]
        if seen:
            last = seen[-1]
            avg = movies.ratings_avg(last)
            note = (
                f"\n-# Heads up: we watched this {('on ' + last['date']) if last.get('date') else 'before'}"
                + (f" and gave it {avg:g}/10" if avg is not None else "")
                + "."
            )
        data = {
            "title": m["title"] if m else title,
            "year": (m or {}).get("year") or year,
            "movie_id": (m or {}).get("id"),
            "poster": (m or {}).get("poster"),
        }
        return (await self.submit("nominate", s.name, data, member)) + note

    @app_commands.command(name="list", description="Show a series' watchlist")
    @app_commands.autocomplete(series=series_autocomplete)
    async def show(self, interaction: discord.Interaction, series: str) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        await interaction.response.send_message(
            embed=self.list_embed(s), allowed_mentions=discord.AllowedMentions.none()
        )

    def list_embed(self, s) -> discord.Embed:
        noms = self._data(store.load(STATE), s)["nominations"]
        waiting = [n for n in noms if not n.get("watched")]
        lines = [f"{i}. **{n['title']}**{_yr(n)} · <@{n['by']}>" for i, n in enumerate(waiting[:30], 1)]
        if len(waiting) > 30:
            lines.append(f"…and {len(waiting) - 30} more")
        embed = discord.Embed(
            title=f"🍿 {s.name} watchlist",
            color=GOLD,
            description="\n".join(lines) or "Nothing yet. Suggest one with `/pingwin movie nominate`.",
        )
        url = self.page_url()
        embed.set_footer(text=f"{len(waiting)} waiting" + (f" · full lists and history: {url}" if url else ""))
        return embed

    @app_commands.command(name="remove", description="Take a movie off a watchlist")
    @app_commands.autocomplete(series=series_autocomplete, movie=title_autocomplete)
    async def remove(self, interaction: discord.Interaction, series: str, movie: str) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        noms = self._data(store.load(STATE), s)["nominations"]
        n = next((x for x in noms if x["id"] == movie or x["title"].casefold() == movie.casefold()), None)
        if n is None:
            await interaction.response.send_message("That movie isn't on the list.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            await self.submit("remove", s.name, {"id": n["id"], "title": n["title"]}, interaction.user), ephemeral=True
        )

    @app_commands.command(name="watched", description="Mark a watchlist movie as watched (or not)")
    @app_commands.autocomplete(series=series_autocomplete, movie=title_autocomplete)
    async def watched(self, interaction: discord.Interaction, series: str, movie: str) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        n = next((x for x in self._data(store.load(STATE), s)["nominations"] if x["id"] == movie), None)
        if n is None:
            await interaction.response.send_message("That movie isn't on the list.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        data = {"id": n["id"], "title": n["title"], "watched": not n.get("watched")}
        await interaction.followup.send(await self.submit("watched", s.name, data, interaction.user), ephemeral=True)

    @app_commands.command(name="tonight", description="Set tonight's movie (for nights without a poll)")
    @app_commands.describe(series="Which movie night", title="The movie", year="Optional, helps find the right one")
    @app_commands.autocomplete(series=series_autocomplete)
    async def tonight(
        self,
        interaction: discord.Interaction,
        series: str,
        title: app_commands.Range[str, 1, 100],
        year: app_commands.Range[int, 1888, 2100] | None = None,
    ) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        await interaction.response.defer(ephemeral=True)
        m = await self.details(title, year)
        data = {
            "title": m["title"] if m else title.strip(),
            "year": (m or {}).get("year") or year,
            "poster": (m or {}).get("poster"),
        }
        await interaction.followup.send(await self.submit("tonight", s.name, data, interaction.user), ephemeral=True)

    @app_commands.command(name="history", description="Movies we've watched, with our ratings")
    @app_commands.describe(search="Part of a title, director or year (optional)")
    async def history(self, interaction: discord.Interaction, search: str | None = None) -> None:
        await interaction.response.send_message(embed=self.history_embed(search), ephemeral=True)

    def history_embed(self, search: str | None) -> discord.Embed:
        lib = movies.load()
        q = (search or "").casefold().strip()
        rows = []
        for e in reversed(lib["history"]):
            m = lib["movies"].get(e["movie_id"], {})
            hay = " ".join(str(m.get(k, "")) for k in ("title", "year", "director", "stars")).casefold()
            if q and q not in hay:
                continue
            avg = movies.ratings_avg(e)
            rows.append(
                f"`{e.get('date') or '   ?    '}` **{movies.label(m) if m else '?'}** · {e['series']}"
                + (f" · ⭐ {avg:g}" if avg is not None else "")
            )
            if len(rows) >= 15:
                break
        url = self.page_url()
        embed = discord.Embed(
            title="🎞️ Movie history" + (f": {search}" if search else ""),
            color=GOLD,
            description="\n".join(rows) or "Nothing found.",
        )
        embed.set_footer(text=f"{len(lib['history'])} movie nights so far" + (f" · everything: {url}" if url else ""))
        return embed

    @app_commands.command(name="rate", description="Rate the latest movie (if you missed the button)")
    async def rate(self, interaction: discord.Interaction) -> None:
        lib = movies.load()
        open_ = [e for e in lib["history"] if (e.get("prompt") or {}).get("message") and not e["prompt"].get("closed")]
        if not open_:
            await interaction.response.send_message("There's nothing to rate right now.", ephemeral=True)
            return
        await open_rating(interaction, open_[-1]["id"])

    @app_commands.command(name="page", description="Link to the movie pages (history, ratings, watchlists)")
    async def page(self, interaction: discord.Interaction) -> None:
        url = self.page_url()
        await interaction.response.send_message(
            f"🎞️ Movie history, ratings and watchlists: {url}\n-# Sign in with Discord."
            if url
            else "The movie pages aren't online yet. A mod can turn on remote access in the dashboard.",
            ephemeral=True,
        )

    @app_commands.command(name="poll", description="Start a poll from the watchlist")
    @app_commands.describe(
        series="Which movie night",
        hours="How long the poll runs (default: until the series' showtime)",
        question="What the poll asks (optional)",
    )
    @app_commands.autocomplete(series=series_autocomplete)
    async def poll(
        self,
        interaction: discord.Interaction,
        series: str,
        hours: app_commands.Range[int, 1, 768] | None = None,
        question: app_commands.Range[str, 1, 300] | None = None,
    ) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        if not self._allowed(interaction.user, self.cfg.run_polls, self.cfg.run_role):
            await interaction.response.send_message("You can't start movie polls here.", ephemeral=True)
            return
        data = self._data(store.load(STATE), s)
        if any(p["status"] == "open" for p in data["polls"]):
            await interaction.response.send_message(
                f"There's already a {s.name} poll running. End it with `/pingwin movie endpoll` first.", ephemeral=True
            )
            return
        unwatched = [n for n in data["nominations"] if not n.get("watched")]
        if len(unwatched) < 2:
            await interaction.response.send_message(
                "The watchlist needs at least 2 unwatched movies for a poll.", ephemeral=True
            )
            return
        closes, hours = poll_close(
            getattr(s, "showtime", ""), datetime.now(self.bot.features.tz), hours, self.cfg.poll_hours
        )
        question = question or f"{s.name}: what should we watch?"
        if self.cfg.pick == "choose" and len(unwatched) > 2:
            view = ChooseView(self, s, unwatched, hours, question, interaction.user.id, closes)
            await interaction.response.send_message(
                f"Pick up to {min(self.cfg.poll_options, len(unwatched))} movies for the poll:",
                view=view,
                ephemeral=True,
            )
            return
        picks = pick_options(
            data["nominations"], "random" if self.cfg.pick == "random" else "oldest", self.cfg.poll_options
        )
        await interaction.response.defer(ephemeral=True)
        msg = await self.start_poll(interaction, s, picks, hours, question, closes)
        if msg:
            await interaction.followup.send(f"Poll posted: {msg.jump_url}", ephemeral=True)

    @app_commands.command(name="endpoll", description="End a series' running poll now")
    @app_commands.autocomplete(series=series_autocomplete)
    async def endpoll(self, interaction: discord.Interaction, series: str) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        if not self._allowed(interaction.user, self.cfg.run_polls, self.cfg.run_role):
            await interaction.response.send_message("You can't end movie polls here.", ephemeral=True)
            return
        state = store.load(STATE)
        p = next((p for p in self._data(state, s)["polls"] if p["status"] == "open"), None)
        if p is None:
            await interaction.response.send_message("No poll is running.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        channel = self.bot.get_channel(p["channel_id"])
        try:
            msg = await channel.fetch_message(p["message_id"])
            if msg.poll and not msg.poll.is_finalised():
                await msg.end_poll()
        except (discord.HTTPException, AttributeError):
            pass
        p["ends"] = discord.utils.utcnow().isoformat()
        p["end_sent"] = True
        store.save(STATE, state)
        await interaction.followup.send("Ending the poll; the winner is announced in a moment.", ephemeral=True)

    # ------------------------------------------------------------ polls
    async def start_poll(
        self,
        interaction: discord.Interaction,
        s,
        picks: list[dict],
        hours: int,
        question: str,
        closes: datetime | None = None,
    ) -> discord.Message | None:
        channel = self.bot.text_channel(s.channel) or interaction.channel
        closes = closes or discord.utils.utcnow() + timedelta(hours=hours)
        early = closes < discord.utils.utcnow() + timedelta(hours=hours) - timedelta(minutes=1)
        poll = discord.Poll(question=question[:300], duration=timedelta(hours=hours), multiple=self.cfg.multiple)
        for n in picks:
            t = n["title"] + (f" ({n['year']})" if n.get("year") else "")
            poll.add_answer(text=t if len(t) <= ANSWER_LIMIT else t[: ANSWER_LIMIT - 1] + "…")
        role = self.bot.role_named(channel.guild, s.role)
        ts = int(closes.timestamp())
        note = f"Voting closes <t:{ts}:F> (<t:{ts}:R>)" + (", right when the movie starts. 🍿" if early else ".")
        try:
            msg = await channel.send(
                content=((role.mention + " ") if role else "") + note,
                poll=poll,
                allowed_mentions=discord.AllowedMentions(roles=[role] if role else False),
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"Couldn't post the poll in #{channel.name}: {e.text or e}", ephemeral=True)
            return None
        state = store.load(STATE)
        self._data(state, s)["polls"].append(
            {
                "message_id": msg.id,
                "channel_id": channel.id,
                "options": [n["id"] for n in picks],
                "ends": closes.astimezone(discord.utils.utcnow().tzinfo).isoformat(),
                "end_early": early,
                "status": "open",
                "by": interaction.user.id,
            }
        )
        store.save(STATE, state)
        log.info("%s poll started with %d movies", s.name, len(picks))
        return msg

    @tasks.loop(seconds=20)
    async def check(self) -> None:
        if not (self.cfg.enabled and self.bot.is_active):
            return
        state = store.load(STATE)
        now = discord.utils.utcnow()
        changed = False
        for s in self.all_series:
            data = self._data(state, s)
            for p in data["polls"]:
                if p["status"] != "open" or now < discord.utils.parse_time(p["ends"]):
                    continue
                if await self.finish(s, data, p):
                    changed = True
            data["polls"] = [p for p in data["polls"] if p["status"] == "open"][-5:] + [
                p for p in data["polls"] if p["status"] != "open"
            ][-5:]
        if changed:
            store.save(STATE, state)
        # Rate it buttons: post after the movie, close when the time is up.
        lib = movies.load()
        dirty = False
        t = time.time()
        for e in lib["history"]:
            if e.get("rate_at") and not e.get("prompt") and t >= e["rate_at"]:
                await self.post_prompt(e, lib)
                dirty = True
            p = e.get("prompt") or {}
            if p.get("message") and not p.get("closed") and t >= p.get("closes", 0):
                await self.close_prompt(e, lib)
                dirty = True
        if dirty:
            movies.save(lib)

    @check.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    async def finish(self, s, data: dict, p: dict) -> bool:
        channel = self.bot.get_channel(p["channel_id"])
        try:
            msg = await channel.fetch_message(p["message_id"])
        except (discord.HTTPException, AttributeError):
            p["status"] = "gone"
            return True
        poll = msg.poll
        if poll is None:
            p["status"] = "gone"
            return True
        if not poll.is_finalised() and p.get("end_early") and not p.get("end_sent"):
            # Showtime: close it now instead of waiting for Discord's whole-hour timer.
            p["end_sent"] = True
            try:
                await msg.end_poll()
                await asyncio.sleep(3)
                msg = await channel.fetch_message(p["message_id"])
                poll = msg.poll or poll
            except discord.HTTPException as e:
                log.warning("Couldn't end the %s poll at showtime: %s", s.name, e)
        late = discord.utils.utcnow() - discord.utils.parse_time(p["ends"]) > timedelta(minutes=5)
        if not poll.is_finalised() and not late:
            return False  # Discord is still counting
        answers = sorted(poll.answers, key=lambda a: a.id)
        counts = [(p["options"][i], a.vote_count) for i, a in enumerate(answers) if i < len(p["options"])]
        top = winners(counts)
        noms = {n["id"]: n for n in data["nominations"]}
        role = self.bot.role_named(channel.guild, s.role)
        winner = None
        if not top:
            text = f"🍿 The {s.name} poll closed with no votes."
        elif len(top) > 1 and self.cfg.tie == "mods":
            names = " and ".join(f"**{noms[i]['title']}**" for i in top if i in noms)
            text = f"🍿 It's a tie between {names}! A mod will pick (`/pingwin movie tonight`)."
        else:
            pick = random.choice(top) if len(top) > 1 else top[0]
            votes = dict(counts)[pick]
            title = noms[pick]["title"] if pick in noms else "the winner"
            extra = " (picked at random from a tie)" if len(top) > 1 else ""
            text = f"🍿 The votes are in! **{title}** wins with {votes} vote{'s' if votes != 1 else ''}{extra}."
            if pick in noms:
                winner = noms[pick]
                if self.cfg.mark_watched:
                    noms[pick]["watched"] = discord.utils.utcnow().isoformat()
        try:
            await msg.reply(
                ((role.mention + " ") if role else "") + text,
                allowed_mentions=discord.AllowedMentions(roles=[role] if role else False),
            )
        except discord.HTTPException as e:
            log.warning("Couldn't announce the %s winner: %s", s.name, e)
        p["status"] = "done"
        if winner:
            when = discord.utils.parse_time(p["ends"]).astimezone(self.bot.features.tz)
            await self.log_watched(s, winner["title"], winner.get("year"), when=when)
        log.info("%s poll finished: %s", s.name, text)
        return True


class ChooseView(discord.ui.View):
    """Lets whoever started the poll pick which watchlist movies go in it."""

    def __init__(
        self, cog: MovieNight, s, unwatched: list[dict], hours: int, question: str, owner: int, closes=None
    ) -> None:
        super().__init__(timeout=600)
        self.cog, self.s, self.hours, self.question, self.owner = cog, s, hours, question, owner
        self.closes = closes
        self.noms = {n["id"]: n for n in unwatched}
        limit = min(cog.cfg.poll_options, len(unwatched), 25)
        select = discord.ui.Select(
            placeholder="Choose movies…",
            min_values=2,
            max_values=limit,
            options=[
                discord.SelectOption(label=n["title"][:100], value=n["id"])
                for n in sorted(unwatched, key=lambda n: n["added"])[:25]
            ],
        )
        select.callback = self.chosen
        self.add_item(select)

    async def chosen(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner:
            await interaction.response.send_message("This menu belongs to whoever started the poll.", ephemeral=True)
            return
        ids = interaction.data.get("values", [])
        picks = [self.noms[i] for i in ids if i in self.noms]
        await interaction.response.edit_message(content="Posting the poll…", view=None)
        msg = await self.cog.start_poll(interaction, self.s, picks, self.hours, self.question, self.closes)
        if msg:
            await interaction.edit_original_response(content=f"Poll posted: {msg.jump_url}")


async def setup(bot: Pingwin) -> None:
    bot.add_dynamic_items(RequestButton, RateButton)
    await bot.add_cog(MovieNight(bot))
