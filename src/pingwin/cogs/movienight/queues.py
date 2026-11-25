"""Franchise queues, and the links and share posts for the movie pages."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

import aiohttp
import discord

from ... import breaks, franchise, movieaccess, movies, store

if TYPE_CHECKING:
    pass
from .common import DEFAULT_RUNTIME, GOLD, STATE, is_franchise, log, movie_card, series_label


class QueuesMixin:
    """Part of the movie night cog (MovieNight, in cog.py)."""

    # ------------------------------------------------------------ franchise queues
    async def run_queues(self) -> None:
        """Announce each franchise part before showtime, then log it and move on when the movie starts.

        With queue_announce_hours at 0 nothing is announced, but each part is still logged at showtime."""
        hours = float(getattr(self.cfg, "queue_announce_hours", 6) or 0)
        for s in self.all_series:
            if not is_franchise(s):
                continue
            self._start_next_franchise(s)
            if not getattr(s, "showtime", ""):
                continue
            now = datetime.now(self.bot.features.tz)
            show = self.next_showing(s, now)  # weeks off are skipped
            if show is None:
                continue
            state = store.load(STATE)
            data = self._data(state, s)
            q = data.get("queue")
            last = breaks.last_showing(s.showtime, now)
            what = franchise.due(q, show, now, hours, last=last)
            if what == "log" and last is not None and breaks.covering(data.get("breaks"), last.date()):
                what = "cancel"  # a break was added after it was announced: that night didn't happen
            if what is None:
                continue
            item = franchise.next_part(q)
            if what == "note":  # no announcement wanted: just remember the showing, so it's logged
                q["announced_for"] = show.date().isoformat()
                store.save(STATE, state)
            elif what == "cancel":  # it started while the queue was paused: that showing was skipped
                q["announced_for"] = None
                store.save(STATE, state)
                log.info("%s: the %s queue was paused at showtime, so that night isn't logged", s.name, q["name"])
            elif what == "announce":
                q["announced_for"] = show.date().isoformat()
                store.save(STATE, state)
                await self._queue_post(s, "franchise_next", q, item, when=show)
            else:  # "log": the announced showing has started: the movie mods confirm it happened
                q["logged_for"] = last.date().isoformat()
                store.save(STATE, state)
                part, total = franchise.part_number(q, item)
                m = await self._card_movie(item["title"], item.get("year"), item.get("imdb_id"))
                runtime = (m or {}).get("runtime") or DEFAULT_RUNTIME
                await self.ask_night(
                    s,
                    last.date().isoformat(),
                    {k: item.get(k) for k in ("title", "year", "imdb_id") if item.get(k)},
                    f"Part {part} of {total} of {q['name']} was on",
                    last.timestamp() + (runtime + 10) * 60,
                )
                log.info("%s: asked the movie mods to confirm %s from the %s queue", s.name, item["title"], q["name"])

    def _start_next_franchise(self, s) -> None:
        """Start the franchise that already won a poll, once the current one has nothing left to watch."""
        state = store.load(STATE)
        data = self._data(state, s)
        name = data.get("next_franchise")
        if not name or franchise.next_part(data.get("queue")) is not None:
            return
        data["queue"] = franchise.new_queue(name, paused=True)  # a mod adds the movies
        data.pop("next_franchise")
        store.save(STATE, state)
        log.info("%s: starting the next franchise, %s", s.name, name)

    async def suggest_franchise(self, name: str) -> list[tuple[str, int | None]]:
        """A starting list for a franchise: OMDb movies whose titles match, oldest first (mods trim it)."""
        key = self.bot.features.secret(movies.SECRET)
        if not key or self._omdb_paused():
            return []
        found: dict[str, tuple[str, int | None]] = {}
        spent = 0
        try:
            for page in (1, 2):
                spent += 1
                async with self._http().get(
                    movies.OMDB, params={"apikey": key, "s": name, "type": "movie", "page": str(page)}
                ) as r:
                    d = await r.json(content_type=None)
                for hit in d.get("Search") or []:
                    year = re.match(r"\d{4}", hit.get("Year", "") or "")
                    found.setdefault(hit["imdbID"], (hit["Title"], int(year[0]) if year else None))
                if len(d.get("Search") or []) < 10:
                    break
        except (TimeoutError, aiohttp.ClientError) as e:
            log.info("OMDb franchise search failed: %s", e)
        finally:
            lib = movies.load()
            b = self._budget(lib)
            b["used"] = b.get("used", 0) + spent
            movies.save(lib)
        return sorted(found.values(), key=lambda t: (t[1] or 9999, t[0]))

    def announce_channel(self, s) -> discord.TextChannel | None:
        """Where movie night announcements go: the announcements channel if one is set, else the series'."""
        return self.bot.text_channel(getattr(self.cfg, "announce_channel", None)) or self.bot.text_channel(s.channel)

    async def _queue_post(self, s, key: str, q: dict, item: dict | None, when: datetime | None, note: str = "") -> None:
        channel = self.announce_channel(s)
        if channel is None:
            log.warning("%s: no channel for franchise announcements", s.name)
            return
        role = self.bot.role_named(channel.guild, s.role)
        values = {"series": series_label(s), "short": s.name, "franchise": q["name"]}
        if item is not None:
            part, total = franchise.part_number(q, item)
            values.update(title=franchise.label(item), part=part, total=total)
        if when is not None:
            values["when"] = f"<t:{int(when.timestamp())}:t> (<t:{int(when.timestamp())}:R>)"
        text = self.bot.persona.say(key, **values)
        if note:
            text += "\n" + note
        url = self.page_url()
        if url and key == "franchise_done":
            text += f"\nThe whole {q['name']} run, with everyone's ratings: <{self.view_url()}>"
        embeds = []
        if (
            item is not None
            and key == "franchise_next"
            and getattr(getattr(self.bot.features, "events", None), "movie_cards", True)
        ):
            part, total = franchise.part_number(q, item)
            m = await self._card_movie(item["title"], item.get("year"), item.get("imdb_id"))
            embeds.append(movie_card(m, f"Part {part} of {total} of {q['name']}"))
        try:
            await channel.send(
                ((role.mention + " ") if role and key == "franchise_next" else "") + text,
                embeds=embeds,
                allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[role] if role else False),
            )
        except discord.HTTPException as e:
            log.warning("Couldn't post the %s franchise update: %s", s.name, e)

    def queue_embed(self, s) -> discord.Embed:
        data = self._data(store.load(STATE), s)
        q = data.get("queue")
        if not q or not q.get("items"):
            name = (
                f"**{q['name']}** is up next, but its movies aren't listed yet." if q else "No franchise in progress."
            )
            return discord.Embed(
                title=f"🎬 {series_label(s)}",
                description=name + " A mod can line them up on the movie pages or with `/pingwin movie franchise`.",
                color=GOLD,
            )
        nxt = franchise.next_part(q)
        lines = []
        for item in franchise.counted(q):
            part, total = franchise.part_number(q, item)
            mark = "✅" if item.get("done") else ("👉" if item is nxt else "▫️")
            lines.append(f"{mark} {part}. {franchise.label(item)}")
        show = self.next_showing(s)
        foot = (
            "Finished! Time to pick the next franchise."
            if nxt is None
            else ("Paused." if q.get("paused") else (f"Next: <t:{int(show.timestamp())}:F>" if show else ""))
        )
        if data.get("next_franchise"):
            foot = (foot + "\n" if foot else "") + f"The next franchise (already picked): **{data['next_franchise']}**"
        return discord.Embed(
            title=f"🎬 {series_label(s)}: {q['name']}",
            description="\n".join(lines)[:3900] + (f"\n\n{foot}" if foot else ""),
            color=GOLD,
        )

    def view_url(self) -> str | None:
        """The shared view-only link to the movie pages (no sign-in needed to browse)."""
        url = self.page_url()
        return f"{url}?view={movieaccess.share_token()}" if url else None

    def _share_text(self) -> str:
        """The pinned post that explains the movie pages and how to get in."""
        names = " and ".join(f"**{series_label(s)}**" for s in self.cfg.series if s.name) or "our movie nights"
        days = [e["date"] for e in movies.load()["history"] if e.get("date")]
        since = f" since {min(days)[:4]}" if days else ""
        return (
            "🎞️ **Our movie nights have their own website now!** 🐧\n"
            f"Everything from {names}, in one place:\n"
            f"• **History**: every movie night{since}, with our ratings next to IMDb's\n"
            "• **Reviews**: everyone's scores and comments (and the question chain!)\n"
            "• **Watchlists**: what's coming up, and the franchise we're working through\n"
            "• **Stats**: favorites, hot takes vs IMDb, and who's reviewed the most\n\n"
            f"👀 **Just browsing?** Open {self.view_url()} (no sign-in needed).\n"
            "✍️ **Want to write a review or suggest a movie?** Type `/pingwin movie page` and I'll send you "
            "your own sign-in link (only you can see it).\n"
            "⭐ After each movie I'll still post a **Rate it** button right here."
        )

    async def post_share_link(self, channel_id: int) -> str:
        """Post the view link in a channel and pin it (replacing an earlier pinned post)."""
        if not self.view_url():
            return "Put the dashboard online first (Status → Remote access), so the link works outside this PC."
        channel = self.bot.text_channel(channel_id)
        if channel is None:
            return "Pingwin can't post in that channel."
        old = movieaccess.posted()
        msg = await channel.send(self._share_text(), allowed_mentions=discord.AllowedMentions.none())
        pinned = True
        try:
            await msg.pin(reason="Pingwin: movie pages link")
        except discord.HTTPException:
            pinned = False
        movieaccess.remember_post(channel.id, msg.id)
        if old and old[1] != msg.id:
            try:
                prev = self.bot.get_channel(old[0])
                if prev is not None:
                    await prev.get_partial_message(old[1]).delete()
            except discord.HTTPException:
                pass
        return f"Posted in #{channel.name}" + (" and pinned it." if pinned else " (couldn't pin it).")

    async def refresh_share_post(self) -> None:
        """After the link is reset, update the pinned post so it shows the new link."""
        post = movieaccess.posted()
        url = self.view_url()
        if not post or not url:
            return
        channel = self.bot.get_channel(post[0])
        if channel is None:
            return
        try:
            await channel.get_partial_message(post[1]).edit(content=self._share_text())
        except discord.NotFound:
            movieaccess.remember_post(None, None)
        except discord.HTTPException as e:
            log.warning("Couldn't update the pinned movie pages link: %s", e)

    def page_url(self) -> str | None:
        links = store.load("dashboards")
        if not links:
            return None
        mine = links.get(self.bot.node.name)
        info = mine or next(iter(links.values()))
        return f"{info['url']}/movies"

    def approval_channel(self) -> discord.TextChannel | None:
        welcome = getattr(self.bot.features, "welcome", None)
        return self.bot.text_channel(getattr(self.cfg, "approval_channel", None)) or self.bot.text_channel(
            getattr(welcome, "approval_channel", None)
        )
