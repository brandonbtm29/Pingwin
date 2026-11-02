"""Event reminders: ping the right roles for Discord Events, then remind them before they start.

Reminders can be as many as you like (1 day, 2 hours, 15 minutes before...), each for all events or only
those with certain keywords, with its own message. For movie nights they also say where the vote stands."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from .. import store, voicewatch

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


def reminder_list(cfg) -> list[dict]:
    """The reminders to send: the default one, then the extra rows. Each: minutes, keywords, message, ping."""
    out = []
    if getattr(cfg, "remind_minutes", 0):
        out.append({"minutes": int(cfg.remind_minutes), "keywords": [], "message": "", "ping": True})
    for r in getattr(cfg, "reminders", []) or []:
        minutes = voicewatch.parse_before(getattr(r, "before", ""))
        if minutes:
            out.append(
                {
                    "minutes": minutes,
                    "keywords": [k for k in (getattr(r, "keywords", []) or []) if k],
                    "message": str(getattr(r, "message", "") or ""),
                    "ping": bool(getattr(r, "ping", False)),
                }
            )
    return out


def due_reminder(
    reminders: list[dict], text: str, minutes_left: float, sent: set[int]
) -> tuple[dict | None, list[int]]:
    """The reminder to send now (the nearest one that's due and not sent) and every due one to mark as sent,
    so a PC that was off doesn't post a pile of reminders at once."""
    due = [
        r
        for r in reminders
        if 0 < minutes_left <= r["minutes"]
        and (not r["keywords"] or any(re.search(r"(?<!\w)" + re.escape(k), text, re.I) for k in r["keywords"]))
    ]
    fresh = [r for r in due if r["minutes"] not in sent]
    if not fresh:
        return None, []
    return min(fresh, key=lambda r: r["minutes"]), sorted({r["minutes"] for r in due})


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
        reminders = reminder_list(self.cfg)
        for event in guild.scheduled_events:
            if event.status == discord.EventStatus.scheduled and reminders:
                left = (event.start_time - now).total_seconds() / 60
                record = store.load(STATE).get(str(event.id), {})
                sent = {int(k.split(":")[1]) for k in record if k.startswith("reminded:")}
                if record.get("reminded") and getattr(self.cfg, "remind_minutes", 0):
                    sent.add(int(self.cfg.remind_minutes))  # sent before reminders were a list
                pick, mark = due_reminder(reminders, f"{event.name}\n{event.description or ''}", left, sent)
                if pick is not None:
                    await self.post(event, "reminded", pick, mark)
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

    async def post(
        self, event: discord.ScheduledEvent, kind: str, reminder: dict | None = None, mark: list[int] | None = None
    ) -> None:
        state = store.load(STATE)
        record = state.setdefault(str(event.id), {})
        key = f"reminded:{reminder['minutes']}" if reminder else kind
        if record.get(key):
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
        if reminder is not None and not reminder["ping"]:
            roles = []
        mentions = [r.mention for r in roles]
        users: list[discord.abc.User] = []
        if self.cfg.ping_interested and kind != "announced" and (reminder is None or reminder["ping"]):
            try:
                users = [u async for u in event.users(limit=100) if not u.bot]
            except discord.HTTPException:
                users = []
            mentions += [u.mention for u in users]
        start = int(event.start_time.timestamp())
        if kind == "announced":
            text = self.bot.persona.say("event_new", event=event.name, when=f"<t:{start}:F> (<t:{start}:R>)")
        elif kind == "reminded":
            text = await self._reminder_text(event, reminder, start)
        else:
            text = self.bot.persona.say("event_starting", event=event.name)
        content = (" ".join(mentions) + "\n" if mentions else "") + f"{text}\n{event.url}"
        try:
            await channel.send(
                content[:2000],
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=roles, users=users),
            )
        except discord.HTTPException as e:
            log.warning("Couldn't post the event reminder in #%s: %s", channel.name, e)
            return
        stamp = discord.utils.utcnow().isoformat()
        record[key] = stamp
        for minutes in mark or []:
            record.setdefault(f"reminded:{minutes}", stamp)
        store.save(STATE, state)
        log.info("Event %s: %s (pinged %s)", kind, event.name, ", ".join(r.name for r in roles) or "nobody")

    async def _reminder_text(self, event: discord.ScheduledEvent, reminder: dict | None, start: int) -> str:
        """The reminder, with where a movie night's vote stands (and a nudge) when there is one."""
        info = None
        movie = self.bot.get_cog("MovieNight")
        if movie is not None and hasattr(movie, "reminder_info"):
            try:
                info = await movie.reminder_info(event.name, event.start_time)
            except Exception:  # standings are a bonus: the reminder still goes out
                log.exception("Couldn't work out the movie poll standings for %s", event.name)
        poll = (info or {}).get("line", "")
        nudge = (info or {}).get("nudge", "")
        when = f"<t:{start}:R>"
        template = (reminder or {}).get("message", "").strip()
        if template:
            return (
                template.replace("{event}", event.name)
                .replace("{when}", when)
                .replace("{poll}", poll)
                .replace("{nudge}", nudge)
                .strip()
            )
        text = self.bot.persona.say("event_soon", event=event.name, when=when)
        return "\n".join(t for t in (text, poll, nudge) if t)


async def setup(bot: Pingwin) -> None:
    await bot.add_cog(EventReminders(bot))
