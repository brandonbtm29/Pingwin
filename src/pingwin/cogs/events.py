"""Event reminders: ping the right roles for Discord Events, then remind them before they start.

Reminders can be as many as you like (1 day, 2 hours, 15 minutes before...), each for all events or only
those with certain keywords, with its own message. For movie nights they also say where the vote stands.

Repeating events (a weekly movie night) keep the same Discord Event, which moves to the next date when one
is over, so reminders are kept per date. Pingwin keeps one message per event in the channel: each new post
replaces the last one, and when a repeating event moves on, a "next one is..." post takes its place."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from .. import store, voicewatch

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.events")
STATE = "events"
# A repeating event's date that nobody started is treated as over this long after it was meant to start, and
# Pingwin moves on to the next date itself (Discord only moves a repeating event on once a date is started
# and ended, so one nobody clicked Start on would stay stuck on the old date).
MISSED_AFTER = timedelta(hours=4)


def keyword_in(keyword: str, text: str) -> bool:
    """Whether a keyword appears in the text as whole words, any case. A plural is fine ("game" matches
    "Games") and the words of a phrase can be joined by spaces, dashes or nothing ("game day" matches
    "Gameday"), but part of another word doesn't count ("art" doesn't match "Arthur")."""
    words = re.findall(r"\w+", keyword or "")
    if not words:
        return False
    pattern = r"[\s\-_]*".join(re.escape(w) for w in words) + r"(?:'?s|es)?"
    return re.search(r"(?<!\w)" + pattern + r"(?!\w)", text or "", re.IGNORECASE) is not None


def match_roles(text: str, rules, description: str = "") -> list[str]:
    """Role names whose keywords appear in an event's name (`text`). Only when nothing matches the name is
    the description checked, so an FFF event that says "movie night" in its description doesn't also ping
    the MMM role."""
    for where in (text, description):
        found: list[str] = []
        for rule in rules:
            if rule.role and rule.role not in found and any(keyword_in(kw, where) for kw in rule.keywords):
                found.append(rule.role)
        if found:
            return found
    return []


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
        if 0 < minutes_left <= r["minutes"] and (not r["keywords"] or any(keyword_in(k, text) for k in r["keywords"]))
    ]
    fresh = [r for r in due if r["minutes"] not in sent]
    if not fresh:
        return None, []
    return min(fresh, key=lambda r: r["minutes"]), sorted({r["minutes"] for r in due})


def _stamps(record: dict) -> list[float]:
    out = []
    for k, v in record.items():
        if k in ("start", "msg", "moved") or not isinstance(v, str):
            continue
        try:
            out.append(datetime.fromisoformat(v).timestamp())
        except ValueError:
            pass
    return out


def is_new_date(record: dict, start: int, longest_minutes: int) -> bool:
    """Whether the event has moved to a new date since this record was made (a repeating event's next one, or
    a reschedule). Older records have no start: they belong to an earlier date when everything in them was
    posted well before the earliest reminder for this one."""
    if "start" in record:
        return int(record["start"]) != start
    stamps = _stamps(record)
    return bool(stamps) and max(stamps) < start - (longest_minutes + 90) * 60


def after_move(record: dict, when: datetime) -> datetime:
    """The event's time, counting Pingwin's own clock-change move: Discord's copy of the event can lag a moment
    behind the move, and then still shows the old time."""
    moved = record.get("moved")
    if isinstance(moved, list) and len(moved) == 2 and int(when.timestamp()) == int(moved[0]):
        return datetime.fromtimestamp(int(moved[1]), UTC)
    return when


def own_move(record: dict, start: int) -> bool:
    """Whether the event's date changed only because Pingwin moved it back to its local time (the same date, an
    hour off): what was already sent for it stays sent."""
    moved = record.get("moved")
    return (
        isinstance(moved, list)
        and len(moved) == 2
        and "start" in record
        and int(record["start"]) == int(moved[0])
        and start == int(moved[1])
    )


def shift_weekdays(days, shift: int):
    """Discord's weekdays are UTC days: a move across midnight UTC moves them too."""
    if not days or not shift:
        return days
    return [(int(d) + shift) % 7 for d in days]


def _card(movie: dict, why: str) -> discord.Embed:
    from .movienight import movie_card

    return movie_card(movie, why)


def _at(day, anchor: datetime, tz) -> datetime:
    """`day` at the anchor's local clock time."""
    return datetime(day.year, day.month, day.day, anchor.hour, anchor.minute, anchor.second, tzinfo=tz)


def next_occurrence(rule: dict | None, after: datetime, tz=UTC) -> datetime | None:
    """A repeating event's first date after `after`, from Discord's recurrence rule (daily and weekly rules,
    every N days or weeks), at the rule's local clock time in `tz`. None for other rules or when it has ended."""
    if not rule or not rule.get("start"):
        return None
    try:
        utc = datetime.fromisoformat(rule["start"]).astimezone(UTC)
        end = datetime.fromisoformat(rule["end"]) if rule.get("end") else None
    except ValueError:
        return None
    anchor = utc.astimezone(tz)
    interval = max(1, int(rule.get("interval") or 1))
    if rule.get("frequency") == 3:  # daily
        step, days = interval, None
    elif rule.get("frequency") == 2:  # weekly (Discord's weekdays, 0 = Monday, are UTC days)
        step, days = 7 * interval, rule.get("by_weekday") or None
        if days:  # Saturday 01:30 UTC is a Friday evening here
            shift = (anchor.date() - utc.date()).days
            days = [(int(wd) + shift) % 7 for wd in days]
    else:
        return None
    gap = (after.astimezone(tz).date() - anchor.date()).days
    k = max(0, gap // step - 1)
    for i in range(k, k + 4):
        base = anchor.date() + timedelta(days=i * step)
        options = sorted(base + timedelta(days=wd - base.weekday()) for wd in days) if days else [base]
        for day in options:
            when = _at(day, anchor, tz)
            if when > after and when >= anchor:
                return when if end is None or when <= end else None
    return None


def local_fix(rule: dict | None, start: datetime, tz) -> datetime | None:
    """Where a repeating event's date should be to stay at the rule's local clock time. Discord keeps
    repeating events at the same UTC time, so they move an hour when the clocks change; this undoes that.
    None when it's already right, or when it's off by more than an hour (someone moved it on purpose)."""
    if not rule or not rule.get("start") or rule.get("frequency") not in (2, 3):
        return None
    try:
        anchor = datetime.fromisoformat(rule["start"]).astimezone(tz)
    except ValueError:
        return None
    local = start.astimezone(tz)
    for shift in (0, -1, 1):  # a late-night time can land on the next or previous day
        want = _at(local.date() + timedelta(days=shift), anchor, tz)
        diff = abs((want - start).total_seconds())
        if diff == 0:
            return None
        if diff <= 3600:
            return want
    return None


def effective_start(event, rule: dict | None, now: datetime, tz=UTC) -> datetime:
    """When the event is next on: its own start, or for a repeating event stuck on a date nobody started, the
    next date from its rule."""
    start = event.start_time
    if event.status == discord.EventStatus.scheduled and now - start > MISSED_AFTER:
        nxt = next_occurrence(rule, max(start, now - MISSED_AFTER), tz)
        if nxt is not None:
            return nxt
    return start


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
        longest = max([r["minutes"] for r in reminders] or [0])
        rules = await self._rules(guild)
        tz = self.bot.features.tz
        await self._keep_local_time(guild, now)
        for event in guild.scheduled_events:
            when = effective_start(event, rules.get(event.id), now, tz)
            when = after_move(store.load(STATE).get(str(event.id), {}), when)
            if event.status == discord.EventStatus.scheduled:
                await self._check_date(event, longest, now, when)
            if event.status == discord.EventStatus.scheduled and reminders:
                left = (when - now).total_seconds() / 60
                record = store.load(STATE).get(str(event.id), {})
                sent = {int(k.split(":")[1]) for k in record if k.startswith("reminded:")}
                if record.get("reminded") and getattr(self.cfg, "remind_minutes", 0):
                    sent.add(int(self.cfg.remind_minutes))  # sent before reminders were a list
                pick, mark = due_reminder(reminders, f"{event.name}\n{event.description or ''}", left, sent)
                if pick is not None:
                    await self.post(event, "reminded", pick, mark, when=when)
            elif event.status == discord.EventStatus.active and self.cfg.remind_at_start:
                # Covers a start that happened while another PC was answering.
                if now - event.start_time < timedelta(minutes=15):
                    await self.post(event, "started")
        self._prune(guild)

    async def _rules(self, guild: discord.Guild) -> dict[int, dict]:
        """Each event's recurrence rule (discord.py doesn't keep them), fetched every 10 minutes."""
        if time.monotonic() - getattr(self, "_rules_at", -1e9) < 600:
            return self._rules_cache
        try:
            raw = await self.bot.http.get_scheduled_events(guild.id, False)
            self._rules_cache = {int(e["id"]): e.get("recurrence_rule") for e in raw}
            self._raw_events = raw
        except (discord.HTTPException, AttributeError, KeyError, TypeError) as e:
            log.info("Couldn't read the events' repeat rules: %s", e)
            self._rules_cache = getattr(self, "_rules_cache", {})
        self._rules_at = time.monotonic()
        return self._rules_cache

    async def _keep_local_time(self, guild: discord.Guild, now: datetime) -> None:
        """Move repeating events back to their local time after a clock change (needs Manage Events)."""
        raw, self._raw_events = getattr(self, "_raw_events", None), None  # once per fetch of the events
        if not raw or not getattr(self.cfg, "keep_local_time", True):
            return
        tz = self.bot.features.tz
        tried = self.__dict__.setdefault("_moved", set())
        for e in raw:
            rule = e.get("recurrence_rule")
            if e.get("status") != 1 or not rule:  # only scheduled, repeating events
                continue
            start = datetime.fromisoformat(e["scheduled_start_time"])
            want = local_fix(rule, start, tz)
            if want is None or start - now < timedelta(minutes=10) or f"{e['id']}:{start}" in tried:
                continue
            tried.add(f"{e['id']}:{start}")
            if not guild.me.guild_permissions.manage_events:
                log.warning(
                    "%s is at %s instead of %s (Discord moved it at the clock change). Give Pingwin's role Manage "
                    "Events and it moves it back.",
                    e.get("name"),
                    f"{start.astimezone(tz):%a %H:%M}",
                    f"{want:%a %H:%M}",
                )
                continue
            payload: dict = {"scheduled_start_time": want.isoformat()}
            if e.get("scheduled_end_time"):
                end = datetime.fromisoformat(e["scheduled_end_time"])
                payload["scheduled_end_time"] = (end + (want - start)).isoformat()
            keep = ("frequency", "interval", "by_weekday", "by_n_weekday", "by_month", "by_month_day")
            new_rule = {k: rule.get(k) for k in keep} | {"start": want.isoformat()}
            # Thu 7:30 PM EDT is Thu 23:30 UTC, but 7:30 PM EST is Fri 00:30 UTC: the rule's weekday moves too.
            day_shift = (want.astimezone(UTC).date() - start.astimezone(UTC).date()).days
            new_rule["by_weekday"] = shift_weekdays(new_rule.get("by_weekday"), day_shift)
            route = discord.http.Route(
                "PATCH",
                "/guilds/{guild_id}/scheduled-events/{event_id}",
                guild_id=guild.id,
                event_id=e["id"],
            )
            reason = "Pingwin: keep the event at its local time after the clock change"
            try:
                await self.bot.http.request(route, json=payload | {"recurrence_rule": new_rule}, reason=reason)
            except discord.HTTPException as err:
                log.info("Moving %s with its repeat rule didn't work (%s); trying the date alone", e.get("name"), err)
                try:
                    await self.bot.http.request(route, json=payload, reason=reason)
                except discord.HTTPException as err2:
                    log.warning("Couldn't move %s back to its local time: %s", e.get("name"), err2)
                    continue
            self._remember_move(e["id"], start, want)
            log.info(
                "Moved %s back to %s (its local time) after the clock change",
                e.get("name"),
                f"{want:%a %b %d %H:%M %Z}",
            )
            self._rules_at = -1e9  # read the events again next time

    def _remember_move(self, event_id, old: datetime, new: datetime) -> None:
        """Note Pingwin's own clock-change move (in the shared state, so another PC knows too), so the new time
        isn't taken for a new date: reminders already sent stay sent instead of pinging everyone again."""
        state = store.load(STATE)
        state.setdefault(str(event_id), {})["moved"] = [int(old.timestamp()), int(new.timestamp())]
        store.save(STATE, state)

    async def _check_date(self, event: discord.ScheduledEvent, longest: int, now, when=None) -> None:
        """Start over when a repeating event moves on to its next date, and say when the next one is."""
        when = when or event.start_time
        state = store.load(STATE)
        record = state.get(str(event.id))
        if record is None:
            return
        when = after_move(record, when)
        start = int(when.timestamp())
        if own_move(record, start):  # the same date an hour off: keep what was sent, fix the time shown
            record["start"] = start
            store.save(STATE, state)
            await self._retime_next(event, record, start)
            return
        if not is_new_date(record, start, longest):
            if "start" not in record:
                record["start"] = start
                store.save(STATE, state)
            return
        state[str(event.id)] = {"start": start, **({"msg": record["msg"]} if record.get("msg") else {})}
        store.save(STATE, state)
        if getattr(self.cfg, "post_next", True) and when > now:
            await self.post(event, "next", when=when)

    async def _retime_next(self, event: discord.ScheduledEvent, record: dict, start: int) -> None:
        """Put the new time in Pingwin's "next one is..." post after its own clock-change move. It's an edit, so
        nobody is pinged; a reminder posted since is left as it is."""
        msg = record.get("msg")
        posts = {k: v for k, v in record.items() if k not in ("start", "msg", "moved") and isinstance(v, str)}
        if not msg or not posts or max(posts, key=posts.get) != "next":
            return
        text = self.bot.persona.say("event_next", event=event.name, when=f"<t:{start}:F> (<t:{start}:R>)")
        try:
            where = self.bot.get_channel(int(msg[0]))
            await where.get_partial_message(int(msg[1])).edit(content=f"{text}\n{event.url}"[:2000])
        except (discord.HTTPException, AttributeError) as e:
            log.info("Couldn't put the new time in the post about %s: %s", event.name, e)

    async def _remove_old(self, channel: discord.abc.Messageable, event: discord.ScheduledEvent, record: dict) -> None:
        """Delete Pingwin's last post about this event, so the new one is the only one (at the bottom)."""
        old = record.get("msg")
        if old:
            where = self.bot.get_channel(int(old[0])) or channel
            try:
                await where.get_partial_message(int(old[1])).delete()
            except (discord.HTTPException, AttributeError):
                pass  # already gone
            return
        me = getattr(self.bot, "user", None)
        if me is None or not hasattr(channel, "history"):
            return
        try:  # posts from before Pingwin kept track: look through the channel's last messages
            async for m in channel.history(limit=50):
                if m.author.id == me.id and f"/{event.id}" in (m.content or ""):
                    await m.delete()
        except discord.HTTPException as e:
            log.info("Couldn't tidy up old posts for %s: %s", event.name, e)

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
        self,
        event: discord.ScheduledEvent,
        kind: str,
        reminder: dict | None = None,
        mark: list[int] | None = None,
        when: datetime | None = None,
    ) -> None:
        when = when or event.start_time
        state = store.load(STATE)
        record = state.setdefault(str(event.id), {})
        record.setdefault("start", int(when.timestamp()))
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
            for name in match_roles(event.name, self.cfg.rules, event.description or "")
            if (r := self.bot.role_named(guild, name))
        ]
        if (reminder is not None and not reminder["ping"]) or kind == "next":
            roles = []
        mentions = [r.mention for r in roles]
        users: list[discord.abc.User] = []
        if self.cfg.ping_interested and kind not in ("announced", "next") and (reminder is None or reminder["ping"]):
            try:
                users = [u async for u in event.users(limit=100) if not u.bot]
            except discord.HTTPException:
                users = []
            mentions += [u.mention for u in users]
        start = int(when.timestamp())
        info = await self._movie_info(event, start) if kind != "announced" else None
        embeds = []
        if info and info.get("movie") and getattr(self.cfg, "movie_cards", True):
            why = info.get("why", "")
            if kind == "started" and why.startswith("Leading"):
                why = "Tonight's pick"
            embeds.append(_card(info["movie"], why))
        if kind == "announced":
            text = self.bot.persona.say("event_new", event=event.name, when=f"<t:{start}:F> (<t:{start}:R>)")
        elif kind == "next":
            text = self.bot.persona.say("event_next", event=event.name, when=f"<t:{start}:F> (<t:{start}:R>)")
        elif kind == "reminded":
            text = self._reminder_text(event, reminder, start, info)
        else:
            text = self.bot.persona.say("event_starting", event=event.name)
        content = (" ".join(mentions) + "\n" if mentions else "") + f"{text}\n{event.url}"
        if getattr(self.cfg, "replace_old", True):
            await self._remove_old(channel, event, record)
        try:
            sent = await channel.send(
                content[:2000],
                embeds=embeds,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=roles, users=users),
            )
        except discord.HTTPException as e:
            log.warning("Couldn't post the event reminder in #%s: %s", channel.name, e)
            return
        stamp = discord.utils.utcnow().isoformat()
        record[key] = stamp
        record["msg"] = [channel.id, sent.id]
        for minutes in mark or []:
            record.setdefault(f"reminded:{minutes}", stamp)
        store.save(STATE, state)
        log.info("Event %s: %s (pinged %s)", kind, event.name, ", ".join(r.name for r in roles) or "nobody")

    async def _movie_info(self, event: discord.ScheduledEvent, start: int) -> dict | None:
        """For a movie night: where the vote stands (or tonight's franchise part) and the likely movie."""
        movie = self.bot.get_cog("MovieNight")
        if movie is None or not hasattr(movie, "reminder_info"):
            return None
        try:
            return await movie.reminder_info(event.name, datetime.fromtimestamp(start, UTC))
        except Exception:  # standings are a bonus: the post still goes out
            log.exception("Couldn't work out the movie poll standings for %s", event.name)
            return None

    def _reminder_text(
        self, event: discord.ScheduledEvent, reminder: dict | None, start: int, info: dict | None = None
    ) -> str:
        """The reminder, with where a movie night's vote stands (and a nudge) when there is one."""
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
