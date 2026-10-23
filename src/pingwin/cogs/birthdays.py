"""Birthdays: members save theirs (month and day), and Pingwin posts a shout-out that day.

The shout-out goes out at the chosen hour in each member's own time zone (/pingwin timezone set), or the
server's if they haven't picked one.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, tzinfo
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import store, zones

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.birthdays")
STATE = "birthdays"
MONTHS = list(calendar.month_name)[1:]


def valid_date(month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= calendar.monthrange(2024, month)[1]  # 2024: allows Feb 29


def is_birthday(month: int, day: int, today: date) -> bool:
    if (month, day) == (today.month, today.day):
        return True
    # Feb 29 birthdays are celebrated on Feb 28 in other years.
    return (month, day) == (2, 29) and not calendar.isleap(today.year) and (today.month, today.day) == (2, 28)


def upcoming(people: dict[str, dict], today: date, limit: int = 10) -> list[tuple[int, str, int, int]]:
    """(days away, user id, month, day), soonest first."""
    out = []
    for uid, p in people.items():
        m, d = p["m"], p["d"]
        for year in (today.year, today.year + 1):
            dd = min(d, calendar.monthrange(year, m)[1])
            when = date(year, m, dd)
            if when >= today:
                out.append(((when - today).days, uid, m, d))
                break
    return sorted(out)[:limit]


def due_now(
    people: dict[str, dict],
    posted_for: dict[str, str],
    saved_zones: dict[str, str],
    default_tz: tzinfo,
    hour: int,
    now: datetime,
) -> list[tuple[str, str]]:
    """(user id, their local date) for everyone whose shout-out is due and not yet posted."""
    out = []
    for uid, p in people.items():
        local = now.astimezone(zones.for_member(int(uid), default_tz, saved_zones))
        key = local.date().isoformat()
        if local.hour >= hour and is_birthday(p["m"], p["d"], local.date()) and posted_for.get(uid) != key:
            out.append((uid, key))
    return out


def _fmt(month: int, day: int) -> str:
    return f"{MONTHS[month - 1]} {day}"


class Birthdays(commands.GroupCog, group_name="birthday", group_description="Birthdays"):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.check.start()

    def cog_unload(self) -> None:
        self.check.cancel()

    @property
    def cfg(self):
        return self.bot.features.birthdays

    async def _off(self, interaction: discord.Interaction) -> bool:
        if not self.cfg.enabled:
            await interaction.response.send_message("Birthdays aren't turned on.", ephemeral=True)
            return True
        return False

    def _save(self, user_id: int, month: int | None, day: int | None) -> None:
        state = store.load(STATE)
        people = state.setdefault("people", {})
        if month is None:
            people.pop(str(user_id), None)
        else:
            people[str(user_id)] = {"m": month, "d": day}
        store.save(STATE, state)

    @app_commands.command(name="set", description="Save your birthday (month and day only)")
    @app_commands.describe(month="1-12", day="1-31")
    async def set_mine(
        self,
        interaction: discord.Interaction,
        month: app_commands.Range[int, 1, 12],
        day: app_commands.Range[int, 1, 31],
    ) -> None:
        if await self._off(interaction):
            return
        if not valid_date(month, day):
            await interaction.response.send_message("That date doesn't exist.", ephemeral=True)
            return
        self._save(interaction.user.id, month, day)
        await interaction.response.send_message(f"Saved: {_fmt(month, day)}. 🎂", ephemeral=True)

    @app_commands.command(name="remove", description="Remove your birthday")
    async def remove_mine(self, interaction: discord.Interaction) -> None:
        self._save(interaction.user.id, None, None)
        await interaction.response.send_message("Removed your birthday.", ephemeral=True)

    @app_commands.command(name="upcoming", description="Show the next birthdays")
    async def show(self, interaction: discord.Interaction) -> None:
        if await self._off(interaction):
            return
        people = store.load(STATE).get("people", {})
        today = datetime.now(zones.for_member(interaction.user.id, self.bot.features.tz)).date()
        rows = upcoming(people, today)
        if not rows:
            await interaction.response.send_message(
                "No birthdays saved yet. Add yours with `/pingwin birthday set`.", ephemeral=True
            )
            return
        lines = [
            f"**{_fmt(m, d)}**: <@{uid}>" + (" (today! 🎉)" if days == 0 else f" (in {days} days)")
            for days, uid, m, d in rows
        ]
        await interaction.response.send_message(
            "🎂 **Upcoming birthdays**\n" + "\n".join(lines), allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(name="set_for", description="Set someone's birthday (mods only)")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def set_for(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        month: app_commands.Range[int, 1, 12],
        day: app_commands.Range[int, 1, 31],
    ) -> None:
        if not valid_date(month, day):
            await interaction.response.send_message("That date doesn't exist.", ephemeral=True)
            return
        self._save(member.id, month, day)
        await interaction.response.send_message(
            f"Saved {member.mention}: {_fmt(month, day)}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="remove_for", description="Remove someone's birthday (mods only)")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def remove_for(self, interaction: discord.Interaction, member: discord.Member) -> None:
        self._save(member.id, None, None)
        await interaction.response.send_message(
            f"Removed {member.mention}'s birthday.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @tasks.loop(minutes=5)
    async def check(self) -> None:
        if not (self.cfg.enabled and self.bot.is_active):
            return
        guild = self.bot.home_guild
        if guild is None:
            return
        now = datetime.now(self.bot.features.tz)
        state = store.load(STATE)
        people = state.get("people", {})
        saved_zones = zones.all_members()
        posted_for = state.setdefault("posted_for", {})
        for day, uids in state.pop("posted", {}).items():  # older format: {date: [user ids]}
            for uid in uids:
                posted_for[uid] = max(posted_for.get(uid, ""), day)
        changed = False

        def local_key(uid: str) -> str:
            return now.astimezone(zones.for_member(int(uid), self.bot.features.tz, saved_zones)).date().isoformat()

        role = self.bot.role_named(guild, self.cfg.birthday_role)
        if role:  # take the birthday role away once their day is over
            for uid, given in list(state.setdefault("role_given", {}).items()):
                if given != local_key(uid):
                    member = guild.get_member(int(uid)) or await self._fetch(guild, int(uid))
                    if member and role in member.roles:
                        await member.remove_roles(role, reason="Pingwin: birthday is over")
                    state["role_given"].pop(uid)
                    changed = True

        due = due_now(people, posted_for, saved_zones, self.bot.features.tz, self.cfg.hour, now)
        if due:
            channel = self.bot.text_channel(self.cfg.channel)
            if channel is None:
                log.warning("Birthdays: no channel set, or Pingwin can't see it")
                if changed:
                    store.save(STATE, state)
                return
            uids = [u for u, _ in due]
            ping = self.bot.role_named(guild, self.cfg.ping_role)
            mentions = " ".join(f"<@{u}>" for u in uids)
            text = self.cfg.message.replace("{mentions}", mentions).replace("{mention}", mentions)
            if ping:
                text = f"{ping.mention} {text}"
            await channel.send(
                text[:2000],
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, users=[discord.Object(int(u)) for u in uids], roles=[ping] if ping else False
                ),
            )
            log.info("Posted birthdays for %d member(s)", len(uids))
            for uid, key in due:
                posted_for[uid] = key
                if role:
                    member = guild.get_member(int(uid)) or await self._fetch(guild, int(uid))
                    if member:
                        await member.add_roles(role, reason="Pingwin: happy birthday")
                        state.setdefault("role_given", {})[uid] = key
            changed = True
        for uid in [u for u in posted_for if u not in people]:
            posted_for.pop(uid)
            changed = True
        if changed:
            store.save(STATE, state)

    @check.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    @staticmethod
    async def _fetch(guild: discord.Guild, user_id: int) -> discord.Member | None:
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None


async def setup(bot: Pingwin) -> None:
    await bot.add_cog(Birthdays(bot))
