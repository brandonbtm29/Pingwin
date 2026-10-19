"""Event reminders: ping the right roles for Discord Events, then remind them before they start."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from .. import store

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.events")
STATE = "events"


def match_roles(text: str, rules) -> list[str]:
    """Role names whose keywords appear in the text (at the start of a word, any case)."""
    found: list[str] = []
    for rule in rules:
        if not rule.role or rule.role in found:
            continue
        for kw in rule.keywords:
            if kw and re.search(r"(?<!\w)" + re.escape(kw), text, re.IGNORECASE):
                found.append(rule.role)
                break
    return found


class EventReminders(commands.Cog):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.check.start()

    def cog_unload(self) -> None:
        self.check.cancel()

    @property
    def cfg(self):
        return self.bot.features.events

    def _ready(self) -> bool:
        return bool(self.cfg.enabled and self.bot.is_active)

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event: discord.ScheduledEvent) -> None:
        if self._ready() and self.cfg.announce_new:
            await self.post(event, "announced")

    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before: discord.ScheduledEvent, after: discord.ScheduledEvent) -> None:
        if (
            self._ready()
            and self.cfg.remind_at_start
            and before.status != discord.EventStatus.active
            and after.status == discord.EventStatus.active
        ):
            await self.post(after, "started")

    @tasks.loop(minutes=1)
    async def check(self) -> None:
        if not self._ready():
            return
        guild = self.bot.home_guild
        if guild is None:
            return
        now = discord.utils.utcnow()
        for event in guild.scheduled_events:
            if event.status == discord.EventStatus.scheduled and self.cfg.remind_minutes:
                until = event.start_time - now
                if timedelta(0) < until <= timedelta(minutes=self.cfg.remind_minutes):
                    await self.post(event, "reminded")
            elif event.status == discord.EventStatus.active and self.cfg.remind_at_start:
                # Covers a start that happened while another PC was answering.
                if now - event.start_time < timedelta(minutes=15):
                    await self.post(event, "started")
        self._prune(guild)

    @check.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    def _prune(self, guild: discord.Guild) -> None:
        state = store.load(STATE)
        live = {str(e.id) for e in guild.scheduled_events}
        stale = [k for k in state if k not in live]
        if stale:
            for k in stale:
                state.pop(k)
            store.save(STATE, state)

    async def post(self, event: discord.ScheduledEvent, kind: str) -> None:
        state = store.load(STATE)
        record = state.setdefault(str(event.id), {})
        if record.get(kind):
            return
        channel = self.bot.text_channel(self.cfg.channel)
        if channel is None:
            log.warning("Event reminders: no channel set, or Pingwin can't see it")
            return
        guild = event.guild
        roles = [
            r
            for name in match_roles(f"{event.name}\n{event.description or ''}", self.cfg.rules)
            if (r := self.bot.role_named(guild, name))
        ]
        mentions = [r.mention for r in roles]
        users: list[discord.abc.User] = []
        if self.cfg.ping_interested and kind != "announced":
            try:
                users = [u async for u in event.users(limit=100) if not u.bot]
            except discord.HTTPException:
                users = []
            mentions += [u.mention for u in users]
        start = int(event.start_time.timestamp())
        if kind == "announced":
            text = f"📅 **New event: {event.name}**\n<t:{start}:F> (<t:{start}:R>)"
        elif kind == "reminded":
            text = f"⏰ **{event.name}** starts <t:{start}:R>!"
        else:
            text = f"▶️ **{event.name}** is starting now!"
        content = (" ".join(mentions) + "\n" if mentions else "") + f"{text}\n{event.url}"
        try:
            await channel.send(
                content[:2000],
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=roles, users=users),
            )
        except discord.HTTPException as e:
            log.warning("Couldn't post the event reminder in #%s: %s", channel.name, e)
            return
        record[kind] = discord.utils.utcnow().isoformat()
        store.save(STATE, state)
        log.info("Event %s: %s (pinged %s)", kind, event.name, ", ".join(r.name for r in roles) or "nobody")


async def setup(bot: Pingwin) -> None:
    await bot.add_cog(EventReminders(bot))
