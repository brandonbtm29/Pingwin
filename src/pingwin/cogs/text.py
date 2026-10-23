"""-pingwin text commands, for quick typing next to the /pingwin menus.

Only the everyday ones are here (movies, birthdays, time zones, status); the rest point to /pingwin.
Needs the Message Content intent (turned on for the mod log); without it Pingwin can't read them.
"""

from __future__ import annotations

import logging
import re
import shlex
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from .. import __version__, store, zones

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.text")
PREFIX = re.compile(r"^\s*-pingwin\b\s*", re.I)

HELP = """**Pingwin commands** (type them, or use the **/pingwin** menu):
`-pingwin movie nominate MMM The Thing` · suggest a movie (add a year at the end if you like: `The Thing 1982`)
`-pingwin movie list MMM` · a watchlist
`-pingwin movie history [search]` · what we've watched and how we rated it
`-pingwin movie page` · link to the movie pages
`-pingwin birthday set 9 3` · your birthday (month day) · `-pingwin birthday upcoming`
`-pingwin timezone set Mountain` · your time zone · `-pingwin timezone show`
`-pingwin status` · which PC is answering
Everything else (polls, rating, game days, mod tools) is in the **/pingwin** menu."""


def split_args(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def title_and_year(words: list[str]) -> tuple[str, int | None]:
    """'The Thing 1982' -> ('The Thing', 1982); a lone number stays part of the title ('1917')."""
    if len(words) > 1 and re.fullmatch(r"(18|19|20)\d\d", words[-1].strip("()")):
        return " ".join(words[:-1]), int(words[-1].strip("()"))
    return " ".join(words), None


class TextCommands(commands.Cog):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not PREFIX.match(message.content or ""):
            return
        if not self.bot.features.bot.text_commands:
            return
        if not self.bot.is_active:
            return  # the PC that's answering handles it
        args = split_args(PREFIX.sub("", message.content, count=1))
        try:
            reply = await self.run(message, [a for a in args if a])
        except Exception:
            log.exception("-pingwin command failed: %s", message.content)
            reply = "Something went wrong on my end. A mod can check the log."
        if reply:
            await message.reply(reply[:2000], mention_author=False, allowed_mentions=discord.AllowedMentions.none())

    async def run(self, message: discord.Message, args: list[str]) -> str | None:
        member = message.author
        if not args or args[0].lower() in ("help", "?", "commands"):
            return HELP
        cmd, rest = args[0].lower(), args[1:]
        if cmd in ("movie", "movies", "movienight"):
            return await self.movie(message, member, rest)
        if cmd == "birthday":
            return self.birthday(member, rest)
        if cmd in ("timezone", "tz"):
            return await self.timezone(member, rest)
        if cmd == "status":
            return f"Answering from **{self.bot.node.name}** · version {__version__}"
        return f"I don't know `-pingwin {cmd}`. Try `-pingwin help`, or the **/pingwin** menu."

    async def movie(self, message: discord.Message, member: discord.Member, args: list[str]) -> str | None:
        cog = self.bot.get_cog("MovieNight")
        if cog is None or not self.bot.features.movienight.enabled:
            return "Movie night isn't turned on."
        sub = args[0].lower() if args else "help"
        if sub in ("nominate", "suggest", "add") and len(args) >= 3:
            s = cog.series(args[1])
            if s is None:
                return "Pick a series: " + ", ".join(x.name for x in cog.cfg.series if x.name) + "."
            title, year = title_and_year(args[2:])
            return await cog.nominate_text(member, s, title, year)
        if sub == "list" and len(args) >= 2:
            s = cog.series(args[1])
            if s is None:
                return "Pick a series: " + ", ".join(x.name for x in cog.cfg.series if x.name) + "."
            await message.reply(
                embed=cog.list_embed(s), mention_author=False, allowed_mentions=discord.AllowedMentions.none()
            )
            return None
        if sub == "history":
            await message.reply(embed=cog.history_embed(" ".join(args[1:]) or None), mention_author=False)
            return None
        if sub in ("page", "link", "site"):
            url = cog.page_url()
            return f"🎞️ {url} (sign in with Discord)" if url else "The movie pages aren't online yet."
        return (
            "Try `-pingwin movie nominate MMM The Thing`, `-pingwin movie list MMM`, "
            "`-pingwin movie history`, or `-pingwin movie page`. Polls and ratings are in the /pingwin menu."
        )

    def birthday(self, member: discord.Member, args: list[str]) -> str:
        from .birthdays import STATE, upcoming, valid_date

        if not self.bot.features.birthdays.enabled:
            return "Birthdays aren't turned on."
        sub = args[0].lower() if args else ""
        state = store.load(STATE)
        people = state.setdefault("people", {})
        if sub == "set" and len(args) >= 3 and args[1].isdigit() and args[2].isdigit():
            m, d = int(args[1]), int(args[2])
            if not valid_date(m, d):
                return "That date doesn't exist. Use month then day, like `-pingwin birthday set 9 3`."
            people[str(member.id)] = {"m": m, "d": d}
            store.save(STATE, state)
            return f"Saved: {datetime(2024, m, d):%B} {d}. 🎂"
        if sub == "remove":
            people.pop(str(member.id), None)
            store.save(STATE, state)
            return "Removed your birthday."
        if sub == "upcoming":
            today = datetime.now(zones.for_member(member.id, self.bot.features.tz)).date()
            rows = upcoming(people, today, 8)
            if not rows:
                return "No birthdays saved yet."
            return "🎂 **Upcoming birthdays**\n" + "\n".join(
                f"{datetime(2024, m, 1):%B} {d}: <@{uid}>" + (" (today! 🎉)" if days == 0 else f" (in {days} days)")
                for days, uid, m, d in rows
            )
        return (
            "Try `-pingwin birthday set 9 3` (month day), `-pingwin birthday upcoming`, or `-pingwin birthday remove`."
        )

    async def timezone(self, member: discord.Member, args: list[str]) -> str:
        sub = args[0].lower() if args else ""
        if sub == "set" and len(args) >= 2:
            tz = zones.zone(" ".join(args[1:]))
            if tz is None:
                return (
                    "I don't know that time zone. Try Eastern, Central, Mountain, Pacific, "
                    "or a name like America/Denver."
                )
            zones.set_for(member.id, tz.key)
            from .roles import sync_zone_roles

            self.bot.loop.create_task(sync_zone_roles(self.bot, member, tz.key))
            return f"Saved: **{zones.label(tz)}**."
        if sub == "show":
            mine = zones.get(member.id)
            return (
                f"Yours is **{zones.label(mine)}**."
                if mine
                else (f"You haven't picked one, so I use the server's: **{zones.label(self.bot.features.tz)}**.")
            )
        return "Try `-pingwin timezone set Mountain` or `-pingwin timezone show`."


async def setup(bot: Pingwin) -> None:
    await bot.add_cog(TextCommands(bot))
