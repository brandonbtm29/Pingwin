"""The movie night cog: its settings, background loops and slash commands. Its other methods are in the
mixins beside it: details, queues, changes, nights, weeks_off, ratings and polls."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ... import breaks, movieaccess, movies, store
from ...features import slug
from ...timeparse import next_weekly

if TYPE_CHECKING:
    from ...bot import Pingwin
from .changes import ChangesMixin
from .common import GENERAL, GOLD, STATE, _movie_label, _same_title, _yr, log, parse_from_date, parse_watch_date
from .details import DetailsMixin
from .nights import NightsMixin
from .polls import PollsMixin
from .queues import QueuesMixin
from .ratings import RatingsMixin
from .views import PICKED, ChooseView, FranchiseModal, open_rating, poll_view
from .weeks_off import WeeksOffMixin


# --------------------------------------------------------------------- the cog
class MovieNight(
    DetailsMixin,
    QueuesMixin,
    ChangesMixin,
    NightsMixin,
    WeeksOffMixin,
    RatingsMixin,
    PollsMixin,
    commands.GroupCog,
    group_name="movie",
    group_description="Movie nights: watchlists, polls, history",
):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.http: aiohttp.ClientSession | None = None
        self._search_cache: dict[tuple[str, int | None], tuple[float, list[dict[str, Any]]]] = {}
        self._filling = asyncio.Lock()
        self._ranked_refresh: set[str] = set()  # ranked polls whose message is about to show new counts
        self._tasks: set[asyncio.Task] = set()
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

    def is_movie_mod(self, member: discord.abc.User) -> bool:
        """Server mods, or members with the movie mods role: they approve movie requests."""
        return self.bot.is_mod(member, getattr(self.cfg, "mod_role", "") or None)

    def _allowed(self, member: discord.abc.User, mode: str, role: str) -> bool:
        if mode == "everyone":
            return True
        if self.is_movie_mod(member):
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

    # ------------------------------------------------------------ autocomplete
    async def catalog_autocomplete(self, interaction: discord.Interaction, current: str):
        """Movie suggestions while typing a title. A picked one carries its IMDb ID along."""
        if len(current.strip()) < 3:
            return []
        try:
            found = await asyncio.wait_for(self.search(current), timeout=2.2)
        except TimeoutError:
            return []
        seen, choices = set(), []
        if found.get("warn"):  # picking this just keeps what they typed
            b = self.search_budget()
            choices.append(
                app_commands.Choice(name=f"⚠️ Only {b['left']} movie searches left today", value=current[:100])
            )
        for h in [*found["library"], *found["omdb"]]:
            key = h.get("imdb_id") or movies.movie_id(h["title"], h.get("year"))
            if key in seen:
                continue
            seen.add(key)
            label = f"{h['title']} ({h['year']})" if h.get("year") else h["title"]
            if h.get("watched"):
                label += " · watched before"
            value = f"{h.get('imdb_id') or ''}|{h['title']}|{h.get('year') or ''}"
            if len(value) <= 100:
                choices.append(app_commands.Choice(name=label[:100], value=value))
        return choices[:25]

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
    @app_commands.autocomplete(series=series_autocomplete, title=catalog_autocomplete)
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
        imdb_id = None
        picked = PICKED.fullmatch(title)
        if picked:  # chosen from the suggestions
            imdb_id = picked["id"] or None
            title = picked["title"]
            year = year or (int(picked["year"]) if picked["year"] else None)
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            await self.nominate_text(interaction.user, s, title, year, imdb_id), ephemeral=True
        )

    async def nominate_text(
        self, member: discord.abc.User, s, title: str, year: int | None = None, imdb_id: str | None = None
    ) -> str:
        """Used by /pingwin movie nominate and the movie pages."""
        if not self._allowed(member, self.cfg.nominate, self.cfg.nominate_role):
            return "You can't nominate movies here."
        title = title.strip()
        noms = self._data(store.load(STATE), s)["nominations"]
        if any(_same_title(n, title, year) and not n.get("watched") for n in noms):
            return f"**{title}** is already on the {s.name} watchlist."
        mine = [n for n in noms if n["by"] == member.id and not n.get("watched")]
        if self.cfg.max_per_person and len(mine) >= self.cfg.max_per_person and not self.is_movie_mod(member):
            return (
                f"You already have {len(mine)} nominations waiting (the limit is {self.cfg.max_per_person}). "
                "Remove one with `/pingwin movie remove` first."
            )
        m = await self.details(title, year, imdb_id)
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
    @app_commands.describe(
        date="When you watched it: like 11/2, Nov 2, yesterday or monday (default today)",
        rate="Also post a Rate it button (default no)",
    )
    @app_commands.autocomplete(series=series_autocomplete, movie=title_autocomplete)
    async def watched(
        self,
        interaction: discord.Interaction,
        series: str,
        movie: str,
        date: app_commands.Range[str, 1, 30] | None = None,
        rate: bool = False,
    ) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        n = next((x for x in self._data(store.load(STATE), s)["nominations"] if x["id"] == movie), None)
        if n is None:
            await interaction.response.send_message("That movie isn't on the list.", ephemeral=True)
            return
        day = None
        if date and not n.get("watched"):
            when = parse_watch_date(date, datetime.now(self.bot.features.tz).date())
            if when is None:
                await interaction.response.send_message(
                    "I couldn't read that date (or it's in the future). Try 11/2, Nov 2, yesterday or monday.",
                    ephemeral=True,
                )
                return
            day = when.isoformat()
        await interaction.response.defer(ephemeral=True)
        data = {"id": n["id"], "title": n["title"], "watched": not n.get("watched")}
        if data["watched"]:
            data.update(date=day or datetime.now(self.bot.features.tz).date().isoformat(), rate=rate)
        await interaction.followup.send(await self.submit("watched", s.name, data, interaction.user), ephemeral=True)

    @app_commands.command(name="tonight", description="Set tonight's movie (for nights without a poll)")
    @app_commands.describe(series="Which movie night", title="The movie", year="Optional, helps find the right one")
    @app_commands.autocomplete(series=series_autocomplete, title=catalog_autocomplete)
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
        imdb_id = None
        picked = PICKED.fullmatch(title)
        if picked:  # chosen from the suggestions
            imdb_id = picked["id"] or None
            title = picked["title"]
            year = year or (int(picked["year"]) if picked["year"] else None)
        await interaction.response.defer(ephemeral=True)
        when = self.tonight_showing(s)  # now, not when a mod approves it (that could be the next morning)
        m = await self.details(title, year, imdb_id)
        data = {
            "title": m["title"] if m else title.strip(),
            "year": (m or {}).get("year") or year,
            "poster": (m or {}).get("poster"),
            "when": when.isoformat(),
        }
        await interaction.followup.send(await self.submit("tonight", s.name, data, interaction.user), ephemeral=True)

    def tonight_showing(self, s) -> datetime:
        """Tonight's showing: the series' showtime today (up to 6 hours after it started), else now."""
        now = datetime.now(self.bot.features.tz)
        return next_weekly(getattr(s, "showtime", "") or "", now - timedelta(hours=6)) or now

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
            title=("🎞️ Movie history" + (f": {search}" if search else ""))[:256],
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

    @app_commands.command(name="queue", description="Where a franchise series is up to")
    @app_commands.autocomplete(series=series_autocomplete)
    async def queue(self, interaction: discord.Interaction, series: str) -> None:
        s = self.series(series)
        if s is None:
            await interaction.response.send_message("Pick a series from the list.", ephemeral=True)
            return
        await interaction.response.send_message(embed=self.queue_embed(s), ephemeral=True)

    @app_commands.command(name="franchise", description="Line up a franchise's movies in order (mods)")
    @app_commands.autocomplete(series=series_autocomplete)
    async def set_franchise(self, interaction: discord.Interaction, series: str) -> None:
        s = self.series(series)
        if s is None:
            await interaction.response.send_message("Pick a series from the list.", ephemeral=True)
            return
        if not self.is_movie_mod(interaction.user):
            await interaction.response.send_message("Only movie mods can change the franchise queue.", ephemeral=True)
            return
        await interaction.response.send_modal(FranchiseModal(self, s))

    @app_commands.command(name="page", description="Link to the movie pages (history, ratings, watchlists)")
    async def page(self, interaction: discord.Interaction) -> None:
        url = self.page_url()
        if not url:
            await interaction.response.send_message(
                "The movie pages aren't online yet. A mod can turn on remote access in the dashboard.", ephemeral=True
            )
            return
        user = interaction.user
        code = movieaccess.new_login_code(user.id, user.display_name, user.display_avatar.with_size(64).url)
        base = url.rsplit("/movies", 1)[0]
        await interaction.response.send_message(
            f"🎞️ **Browse** the history, reviews and watchlists: <{self.view_url()}>\n"
            "✍️ **Your personal sign-in link** (to write reviews and suggest movies): "
            f"<{base}/movies/login?code={code}>\n"
            "-# Only you can see this. The sign-in link works once, for 15 minutes.",
            ephemeral=True,
            suppress_embeds=True,
        )

    @app_commands.command(name="poll", description="Start a movie poll: pick from the watchlist or type movies")
    @app_commands.describe(
        series="Which movie night",
        hours="How long the poll runs (default: until the series' showtime)",
        question="What the poll asks (optional)",
        poll_type="Plurality, ranked or approval (default: the series' setting)",
        ranks="Ranked polls: how many movies each voter ranks, 2 to 5 (default: the setting)",
    )
    @app_commands.rename(poll_type="poll-type")
    @app_commands.choices(
        poll_type=[
            app_commands.Choice(name="Plurality: one vote each, most votes wins", value="plurality"),
            app_commands.Choice(name="Ranked: rank your top picks (instant runoff)", value="ranked"),
            app_commands.Choice(name="Approval: vote for every movie you'd watch", value="approval"),
        ]
    )
    @app_commands.autocomplete(series=series_autocomplete)
    async def poll(
        self,
        interaction: discord.Interaction,
        series: str,
        hours: app_commands.Range[int, 1, 768] | None = None,
        question: app_commands.Range[str, 1, 300] | None = None,
        poll_type: app_commands.Choice[str] | None = None,
        ranks: app_commands.Range[int, 2, 5] | None = None,
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
        closes, hours = self.poll_timing(s, hours)
        question = question or f"{s.name}: what should we watch?"
        ranked, multiple = self.poll_style(s, poll_type.value if poll_type else None)
        ranked = ranked or (ranks is not None and poll_type is None)  # How many to rank, alone: a ranked poll
        picks = [] if self.cfg.pick == "choose" or len(unwatched) < 2 else self.choose_movies(s, self.cfg.pick, data)
        if len(picks) < 2:  # pick from the watchlist, type movies, or both
            view = ChooseView(
                self, s, unwatched, hours, question, interaction.user.id, closes, ranked, multiple, ranks=ranks
            )
            view.interaction = interaction
            await interaction.response.send_message(view.render(), view=view, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await self.start_poll(interaction, s, picks, hours, question, closes, ranked, multiple, ranks=ranks)
        if msg:
            await interaction.followup.send(f"Poll posted: {msg.jump_url}", ephemeral=True)

    @app_commands.command(
        name="pollinfo", description="IMDb links and plots of the movies in a running poll (only you see it)"
    )
    @app_commands.describe(series="Which movie night (default: every running poll)")
    @app_commands.autocomplete(series=series_autocomplete)
    async def pollinfo(self, interaction: discord.Interaction, series: str | None = None) -> None:
        s = None
        if series:
            s = await self._check(interaction, series)
            if s is None:
                return
        elif not self.cfg.enabled:
            await interaction.response.send_message("Movie night isn't turned on.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)  # looking movies up can take a moment
        embeds = await self.poll_info_embeds(s)
        if not embeds:
            where = f"for {s.name} " if s else ""
            await interaction.followup.send(f"No movie poll is running {where}right now.", ephemeral=True)
            return
        await interaction.followup.send(embeds=embeds[:10], ephemeral=True)

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
        self._update_poll(s, p["message_id"], ends=discord.utils.utcnow().isoformat(), end_sent=True)
        await interaction.followup.send("Ending the poll; the winner is announced in a moment.", ephemeral=True)

    def _open_poll(self, interaction: discord.Interaction) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """The series' data and its running poll, for /pingwin movie swap's suggestions."""
        s = self.series(getattr(interaction.namespace, "series", "") or "")
        if s is None:
            return {"nominations": [], "polls": []}, None
        data = self._data(store.load(STATE), s)
        return data, next((p for p in data["polls"] if p["status"] == "open"), None)

    async def in_poll_autocomplete(self, interaction: discord.Interaction, current: str):
        data, p = self._open_poll(interaction)
        if p is None:
            return []
        noms = {n["id"]: n for n in data["nominations"]}
        out = []
        for c in p["options"]:
            name = p.get("labels", {}).get(c) or (noms.get(c) or {}).get("title") or c
            if current.casefold() in name.casefold():
                out.append(app_commands.Choice(name=name[:100], value=c[:100]))
        return out[:25]

    async def swap_in_autocomplete(self, interaction: discord.Interaction, current: str):
        data, p = self._open_poll(interaction)
        taken = set(p["options"]) if p else set()
        return [
            app_commands.Choice(name=_movie_label(n)[:100], value=n["id"])
            for n in data["nominations"]
            if not n.get("watched") and n["id"] not in taken and current.casefold() in n["title"].casefold()
        ][:25]

    @app_commands.command(name="swap", description="Swap a movie in a running poll for another from the watchlist")
    @app_commands.describe(
        series="Which movie night",
        old="The movie to take out of the poll",
        new="The watchlist movie to put in its place",
    )
    @app_commands.autocomplete(series=series_autocomplete, old=in_poll_autocomplete, new=swap_in_autocomplete)
    async def swap(self, interaction: discord.Interaction, series: str, old: str, new: str) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        p = next((x for x in self._data(store.load(STATE), s)["polls"] if x["status"] == "open"), None)
        if p is None:
            await interaction.response.send_message("No poll is running.", ephemeral=True)
            return
        if not (self.is_movie_mod(interaction.user) or (p.get("by") and p["by"] == interaction.user.id)):
            await interaction.response.send_message(
                "Only the movie mods and whoever started the poll can change it.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        problem, p, voters = self.swap_movie(s, old, new, interaction.user.id)
        if problem or p is None:
            await interaction.followup.send(problem, ephemeral=True)
            return
        link = ""
        channel = self.bot.get_channel(p["channel_id"])
        if isinstance(channel, discord.TextChannel):
            msg = channel.get_partial_message(p["message_id"])
            link = msg.jump_url
            try:
                await msg.edit(embed=self.poll_embed(s, p), view=poll_view(p))
            except discord.HTTPException as e:
                log.warning("Couldn't show the swap on the %s poll: %s", s.name, e)
        swap = p["swaps"][-1]
        text = f"Swapped **{swap['out']}** for **{swap['in']}**."
        if voters:
            missed = await self._tell_swapped(s, p, voters, link)
            who = "1 person" if len(voters) == 1 else f"{len(voters)} people"
            text += f" {who} had voted for it; I let them know by DM, with a link back to the poll."
            if missed:
                text += f" ({missed} couldn't get it: DMs turned off.)"
        else:
            text += " Nobody had voted for it yet."
        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name="skip", description="Skip weeks of a movie night series (movie mods)")
    @app_commands.describe(
        series="Which movie night",
        weeks="How many showings to skip (default 1)",
        starting="From which showing: like 11/13, Nov 13 or friday (default the next one)",
        reason="Optional, like Thanksgiving or between franchises",
    )
    @app_commands.autocomplete(series=series_autocomplete)
    async def skip(
        self,
        interaction: discord.Interaction,
        series: str,
        weeks: app_commands.Range[int, 1, breaks.MAX_WEEKS] = 1,
        starting: app_commands.Range[str, 1, 30] | None = None,
        reason: app_commands.Range[str, 0, 100] | None = None,
    ) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        if not self.is_movie_mod(interaction.user):
            await interaction.response.send_message("Only movie mods can skip weeks.", ephemeral=True)
            return
        start = None
        if starting:
            start = parse_from_date(starting, datetime.now(self.bot.features.tz).date())
            if start is None:
                await interaction.response.send_message(
                    "I couldn't read that date (or it's in the past). Try 11/13, Nov 13 or friday.", ephemeral=True
                )
                return
        who = getattr(interaction.user, "display_name", interaction.user.name)
        await interaction.response.send_message(self.add_break(s, weeks, start, reason or "", who), ephemeral=True)

    @app_commands.command(name="unskip", description="End a series' break (movie mods)")
    @app_commands.autocomplete(series=series_autocomplete)
    async def unskip(self, interaction: discord.Interaction, series: str) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        if not self.is_movie_mod(interaction.user):
            await interaction.response.send_message("Only movie mods can end a break.", ephemeral=True)
            return
        who = getattr(interaction.user, "display_name", interaction.user.name)
        await interaction.response.send_message(self.end_break(s, who), ephemeral=True)

    @tasks.loop(seconds=20)
    async def check(self) -> None:
        if not (self.cfg.enabled and self.bot.is_active):
            return
        # Franchise queues first: a part that starts just as the poll for the next franchise closes is
        # logged before that poll's winner is lined up.
        await self.run_queues()
        now = discord.utils.utcnow()
        for s in self.all_series:
            polls = self._data(store.load(STATE), s)["polls"]
            due = [p for p in polls if p["status"] == "open" and now >= discord.utils.parse_time(p["ends"])]
            for p in due:
                await self.finish(s, p)
            if due:  # keep the last few (loaded and saved with nothing to wait on in between)
                state = store.load(STATE)
                data = self._data(state, s)
                data["polls"] = [p for p in data["polls"] if p["status"] == "open"][-5:] + [
                    p for p in data["polls"] if p["status"] != "open"
                ][-5:]
                store.save(STATE, state)
        try:
            await self.watch_voice()
        except Exception:
            log.exception("Watching voice for movie nights failed")
        try:
            await self.run_auto_polls()
        except Exception:
            log.exception("Posting the next poll by itself failed")
        # Rate it buttons: post after the movie, close when the time is up. Each change is saved on its own
        # after re-loading, so ratings given while we wait on Discord are kept.
        lib = movies.load()
        t = time.time()
        for e in lib["history"]:
            if e.get("rate_at") and not e.get("prompt") and t >= e["rate_at"]:
                await self.post_prompt(e, lib)
                self._save_prompt(e)
            p = e.get("prompt") or {}
            if p.get("message") and not p.get("closed") and t >= p.get("closes", 0):
                fresh = movies.load()
                cur = next((x for x in fresh["history"] if x["id"] == e["id"]), None)
                if cur is None or not (cur.get("prompt") or {}).get("message") or cur["prompt"].get("closed"):
                    continue
                cur["prompt"]["closed"] = True  # saved before we wait on Discord: no more ratings after this
                movies.save(fresh)
                await self.close_prompt(cur, fresh)

    def _save_prompt(self, entry: dict[str, Any]) -> None:
        """Save a history entry's Rate it post, re-loading first so ratings given meanwhile are kept."""
        lib = movies.load()
        cur = next((x for x in lib["history"] if x["id"] == entry["id"]), None)
        if cur is not None:
            cur["prompt"] = entry.get("prompt")
            if entry.get("starter"):
                cur["starter"] = entry["starter"]
            movies.save(lib)

    @check.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()
