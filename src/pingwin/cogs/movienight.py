"""Movie night: a watchlist per series, Discord polls that ping the series' role, and winners.

Mods decide how it works on the dashboard: who can nominate, who can start polls, how poll options
are picked, how long polls run, and what happens on a tie.
"""

from __future__ import annotations

import logging
import random
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import store
from ..features import slug

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.movienight")
STATE = "movienight"
ANSWER_LIMIT = 55  # Discord's limit for poll answer text


def pick_options(noms: list[dict], how: str, count: int) -> list[dict]:
    unwatched = [n for n in noms if not n.get("watched")]
    if how == "random":
        return random.sample(unwatched, min(count, len(unwatched)))
    return sorted(unwatched, key=lambda n: n["added"])[:count]


def winners(counts: list[tuple[str, int]]) -> list[str]:
    """IDs with the most votes (several on a tie); empty if nobody voted."""
    if not counts or max(c for _, c in counts) == 0:
        return []
    top = max(c for _, c in counts)
    return [i for i, c in counts if c == top]


class MovieNight(commands.GroupCog, group_name="movienight", group_description="Movie night watchlist and polls"):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.check.start()

    def cog_unload(self) -> None:
        self.check.cancel()

    @property
    def cfg(self):
        return self.bot.features.movienight

    # ------------------------------------------------------------ helpers
    def series(self, name: str):
        key = slug(name or "")
        return next((s for s in self.cfg.series if s.name and slug(s.name) == key), None)

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
            names = ", ".join(x.name for x in self.cfg.series if x.name) or "none set up yet"
            await interaction.response.send_message(f"Pick a series: {names}.", ephemeral=True)
        return s

    @staticmethod
    def _data(state: dict, s) -> dict:
        return state.setdefault(slug(s.name), {"nominations": [], "polls": []})

    async def series_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=s.name, value=s.name)
            for s in self.cfg.series
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
    @app_commands.command(name="nominate", description="Add a movie to a series' watchlist")
    @app_commands.describe(series="Which movie night", title="The movie (or franchise)")
    @app_commands.autocomplete(series=series_autocomplete)
    async def nominate(
        self, interaction: discord.Interaction, series: str, title: app_commands.Range[str, 1, 100]
    ) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        if not self._allowed(interaction.user, self.cfg.nominate, self.cfg.nominate_role):
            await interaction.response.send_message("You can't nominate movies here.", ephemeral=True)
            return
        state = store.load(STATE)
        noms = self._data(state, s)["nominations"]
        title = title.strip()
        if any(n["title"].casefold() == title.casefold() and not n.get("watched") for n in noms):
            await interaction.response.send_message(f"**{title}** is already on the watchlist.", ephemeral=True)
            return
        mine = [n for n in noms if n["by"] == interaction.user.id and not n.get("watched")]
        if self.cfg.max_per_person and len(mine) >= self.cfg.max_per_person and not self.bot.is_mod(interaction.user):
            await interaction.response.send_message(
                f"You already have {len(mine)} nominations waiting (the limit is {self.cfg.max_per_person}). "
                "Remove one with `/movienight remove` first.",
                ephemeral=True,
            )
            return
        noms.append(
            {
                "id": secrets.token_hex(4),
                "title": title,
                "by": interaction.user.id,
                "added": discord.utils.utcnow().isoformat(),
                "watched": None,
            }
        )
        store.save(STATE, state)
        await interaction.response.send_message(
            f"🎬 **{title}** added to the {s.name} watchlist by {interaction.user.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="list", description="Show a series' watchlist")
    @app_commands.autocomplete(series=series_autocomplete)
    async def show(self, interaction: discord.Interaction, series: str) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        noms = self._data(store.load(STATE), s)["nominations"]
        waiting = [n for n in noms if not n.get("watched")]
        watched = len(noms) - len(waiting)
        lines = [f"{i}. **{n['title']}** · <@{n['by']}>" for i, n in enumerate(waiting[:30], 1)]
        if len(waiting) > 30:
            lines.append(f"…and {len(waiting) - 30} more")
        embed = discord.Embed(
            title=f"🍿 {s.name} watchlist",
            color=discord.Color.gold(),
            description="\n".join(lines) or "Nothing yet. Add one with `/movienight nominate`.",
        )
        embed.set_footer(text=f"{len(waiting)} waiting · {watched} watched")
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="remove", description="Take a movie off the watchlist (yours, or any for mods)")
    @app_commands.autocomplete(series=series_autocomplete, movie=title_autocomplete)
    async def remove(self, interaction: discord.Interaction, series: str, movie: str) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        state = store.load(STATE)
        noms = self._data(state, s)["nominations"]
        n = next((x for x in noms if x["id"] == movie or x["title"].casefold() == movie.casefold()), None)
        if n is None:
            await interaction.response.send_message("That movie isn't on the list.", ephemeral=True)
            return
        if n["by"] != interaction.user.id and not self.bot.is_mod(interaction.user):
            await interaction.response.send_message("Only mods can remove other people's picks.", ephemeral=True)
            return
        noms.remove(n)
        store.save(STATE, state)
        await interaction.response.send_message(
            f"Removed **{n['title']}** from the {s.name} watchlist.", ephemeral=True
        )

    @app_commands.command(name="watched", description="Mark a movie as watched, or not (mods)")
    @app_commands.autocomplete(series=series_autocomplete, movie=title_autocomplete)
    async def watched(self, interaction: discord.Interaction, series: str, movie: str) -> None:
        s = await self._check(interaction, series)
        if s is None:
            return
        if not self.bot.is_mod(interaction.user):
            await interaction.response.send_message("Only mods can do that.", ephemeral=True)
            return
        state = store.load(STATE)
        n = next((x for x in self._data(state, s)["nominations"] if x["id"] == movie), None)
        if n is None:
            await interaction.response.send_message("That movie isn't on the list.", ephemeral=True)
            return
        n["watched"] = None if n.get("watched") else discord.utils.utcnow().isoformat()
        store.save(STATE, state)
        await interaction.response.send_message(
            f"**{n['title']}** marked as {'watched' if n['watched'] else 'not watched'}.", ephemeral=True
        )

    @app_commands.command(name="poll", description="Start a poll from the watchlist")
    @app_commands.describe(
        series="Which movie night", hours="How long the poll runs", question="What the poll asks (optional)"
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
                f"There's already a {s.name} poll running. End it with `/movienight endpoll` first.", ephemeral=True
            )
            return
        unwatched = [n for n in data["nominations"] if not n.get("watched")]
        if len(unwatched) < 2:
            await interaction.response.send_message(
                "The watchlist needs at least 2 unwatched movies for a poll.", ephemeral=True
            )
            return
        hours = hours or self.cfg.poll_hours
        question = question or f"{s.name}: what should we watch?"
        if self.cfg.pick == "choose" and len(unwatched) > 2:
            view = ChooseView(self, s, unwatched, hours, question, interaction.user.id)
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
        msg = await self.start_poll(interaction, s, picks, hours, question)
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
        store.save(STATE, state)
        await interaction.followup.send("Ending the poll; the winner is announced in a moment.", ephemeral=True)

    # ------------------------------------------------------------ polls
    async def start_poll(
        self, interaction: discord.Interaction, s, picks: list[dict], hours: int, question: str
    ) -> discord.Message | None:
        channel = self.bot.text_channel(s.channel) or interaction.channel
        poll = discord.Poll(question=question[:300], duration=timedelta(hours=hours), multiple=self.cfg.multiple)
        for n in picks:
            t = n["title"]
            poll.add_answer(text=t if len(t) <= ANSWER_LIMIT else t[: ANSWER_LIMIT - 1] + "…")
        role = self.bot.role_named(channel.guild, s.role)
        try:
            msg = await channel.send(
                content=role.mention if role else None,
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
                "ends": (discord.utils.utcnow() + timedelta(hours=hours)).isoformat(),
                "status": "open",
                "by": interaction.user.id,
            }
        )
        store.save(STATE, state)
        log.info("%s poll started with %d movies", s.name, len(picks))
        return msg

    @tasks.loop(minutes=1)
    async def check(self) -> None:
        if not (self.cfg.enabled and self.bot.is_active):
            return
        state = store.load(STATE)
        now = discord.utils.utcnow()
        changed = False
        for s in self.cfg.series:
            if not s.name:
                continue
            data = self._data(state, s)
            for p in data["polls"]:
                if p["status"] != "open" or now < discord.utils.parse_time(p["ends"]) + timedelta(seconds=15):
                    continue
                if await self.finish(s, data, p):
                    changed = True
            data["polls"] = [p for p in data["polls"] if p["status"] == "open"][-5:] + [
                p for p in data["polls"] if p["status"] != "open"
            ][-5:]
        if changed:
            store.save(STATE, state)

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
        late = discord.utils.utcnow() - discord.utils.parse_time(p["ends"]) > timedelta(minutes=5)
        if not poll.is_finalised() and not late:
            return False  # Discord is still counting
        answers = sorted(poll.answers, key=lambda a: a.id)
        counts = [(p["options"][i], a.vote_count) for i, a in enumerate(answers) if i < len(p["options"])]
        top = winners(counts)
        noms = {n["id"]: n for n in data["nominations"]}
        role = self.bot.role_named(channel.guild, s.role)
        if not top:
            text = f"🍿 The {s.name} poll closed with no votes."
        elif len(top) > 1 and self.cfg.tie == "mods":
            names = " and ".join(f"**{noms[i]['title']}**" for i in top if i in noms)
            text = f"🍿 It's a tie between {names}! A mod will pick."
        else:
            pick = random.choice(top) if len(top) > 1 else top[0]
            votes = dict(counts)[pick]
            title = noms[pick]["title"] if pick in noms else "the winner"
            extra = " (picked at random from a tie)" if len(top) > 1 else ""
            text = f"🍿 The votes are in! **{title}** wins with {votes} vote{'s' if votes != 1 else ''}{extra}."
            if self.cfg.mark_watched and pick in noms:
                noms[pick]["watched"] = discord.utils.utcnow().isoformat()
        try:
            await msg.reply(
                ((role.mention + " ") if role else "") + text,
                allowed_mentions=discord.AllowedMentions(roles=[role] if role else False),
            )
        except discord.HTTPException as e:
            log.warning("Couldn't announce the %s winner: %s", s.name, e)
        p["status"] = "done"
        log.info("%s poll finished: %s", s.name, text)
        return True


class ChooseView(discord.ui.View):
    """Lets whoever started the poll pick which watchlist movies go in it."""

    def __init__(self, cog: MovieNight, s, unwatched: list[dict], hours: int, question: str, owner: int) -> None:
        super().__init__(timeout=600)
        self.cog, self.s, self.hours, self.question, self.owner = cog, s, hours, question, owner
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
        msg = await self.cog.start_poll(interaction, self.s, picks, self.hours, self.question)
        if msg:
            await interaction.edit_original_response(content=f"Poll posted: {msg.jump_url}")


async def setup(bot: Pingwin) -> None:
    await bot.add_cog(MovieNight(bot))
