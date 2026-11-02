"""Birthdays: members save theirs (month and day), and Pingwin posts a shout-out that day.

The shout-out goes out at the chosen hour in each member's own time zone (/pingwin timezone set), or the
server's if they haven't picked one.
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime, tzinfo
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import movies, store, zones

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


_MON = {m.casefold()[:3]: i + 1 for i, m in enumerate(MONTHS)}


def parse_birthday(text: str) -> tuple[int, int, int | None] | None:
    """(month, day, year or None) from 'Sep 2', 'September 2, 1964', '9/2', '9/2/1964' or '1964-09-02'."""
    t = re.sub(r"\s+", " ", (text or "").strip().casefold().replace(",", " "))
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    elif m := re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{4}))?", t):
        mo, d, y = int(m[1]), int(m[2]), int(m[3]) if m[3] else None
    elif m := re.fullmatch(r"([a-z]{3})[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?", t):
        if m[1] not in _MON:
            return None
        mo, d, y = _MON[m[1]], int(m[2]), int(m[3]) if m[3] else None
    else:
        return None
    return (mo, d, y) if valid_date(mo, d) else None


def others_due(others: list, posted: dict[str, str], today: date) -> list[tuple[str, dict]]:
    """(key, person) for people without Discord whose birthday is today and who haven't been wished yet."""
    out = []
    for o in others:
        name = str(getattr(o, "name", "") or "").strip()
        when = parse_birthday(str(getattr(o, "date", "") or ""))
        if not name or when is None:
            continue
        key = name.casefold()
        if is_birthday(when[0], when[1], today) and posted.get(key) != today.isoformat():
            died = str(getattr(o, "died", "") or "").strip()
            person = {"name": name, "m": when[0], "d": when[1], "y": when[2], "note": getattr(o, "note", "")}
            out.append((key, {**person, "died": int(died) if died.isdigit() else None}))
    return out


def watched_with(lib: dict, name: str, limit: int = 3) -> list[str]:
    """Movie nights with someone in the cast or crew, newest first: ['The Matrix (1999)', ...]."""
    key = name.casefold()
    seen, out = set(), []
    for e in reversed(lib.get("history", [])):
        m = lib["movies"].get(e["movie_id"], {})
        names = {
            re.sub(r"\s*\(.*?\)\s*$", "", n).strip().casefold()  # 'Dan O'Bannon (screenplay)' -> the name
            for k in ("stars", "director", "writer")
            for n in re.split(r"[,\n]", str(m.get(k) or ""))
        }
        if m.get("id") in seen or key not in names:
            continue
        seen.add(m.get("id"))
        out.append(f"{m['title']} ({m['year']})" if m.get("year") else m["title"])
        if len(out) >= limit:
            break
    return out


def other_message(template: str, person: dict, today: date, films: list[str]) -> str:
    age = ""
    if person.get("y") and person["y"] < today.year:
        years = today.year - person["y"]
        age = f", who would have been {years}" if person.get("died") else f", {years} today"
    note = f" {person['note'].strip()}" if str(person.get("note") or "").strip() else ""
    text = template.replace("{name}", person["name"]).replace("{age}", age).replace("{note}", note)
    if films:
        text += (
            "\n-# We've watched them in "
            + (films[0] if len(films) == 1 else ", ".join(films[:-1]) + " and " + films[-1])
            + "."
        )
    return text


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
        others = {}
        for o in getattr(self.cfg, "others", []) or []:
            when = parse_birthday(str(getattr(o, "date", "") or ""))
            if when and str(getattr(o, "name", "")).strip():
                others["*" + o.name.strip()] = {"m": when[0], "d": when[1]}
        rows = upcoming({**people, **others}, today)
        if not rows:
            await interaction.response.send_message(
                "No birthdays saved yet. Add yours with `/pingwin birthday set`.", ephemeral=True
            )
            return
        lines = [
            f"**{_fmt(m, d)}**: "
            + (f"{uid[1:]} ⭐" if uid.startswith("*") else f"<@{uid}>")
            + (" (today! 🎉)" if days == 0 else f" (in {days} days)")
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

    def _others(self) -> list[dict[str, str]]:
        return [
            {k: str(getattr(o, k, "") or "") for k in ("name", "date", "note", "died")}
            for o in getattr(self.cfg, "others", []) or []
        ]

    def _save_others(self, rows: list[dict[str, str]]) -> None:
        from ..configedit import save_section

        save_section("birthdays", {"others": rows})
        self.bot.reload_settings()

    @app_commands.command(name="add_other", description="Add a birthday for someone without Discord, like a celebrity")
    @app_commands.describe(
        name="Their name, like Keanu Reeves",
        birthday="Like Sep 2, 9/2 or 1964-09-02 (the year gives their age)",
        note="Optional, like 'Neo in The Matrix'",
    )
    async def add_other(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 60],
        birthday: app_commands.Range[str, 1, 30],
        note: app_commands.Range[str, 0, 100] | None = None,
    ) -> None:
        if await self._off(interaction):
            return
        if not self.bot.is_mod(interaction.user):
            await interaction.response.send_message("Only mods can add these.", ephemeral=True)
            return
        when = parse_birthday(birthday)
        if when is None:
            await interaction.response.send_message(
                "I couldn't read that date. Try `Sep 2`, `9/2` or `1964-09-02`.", ephemeral=True
            )
            return
        rows = [r for r in self._others() if r["name"].casefold() != name.strip().casefold()]
        rows.append({"name": name.strip(), "date": birthday.strip(), "note": (note or "").strip(), "died": ""})
        self._save_others(rows)
        year = f" ({when[2]})" if when[2] else ""
        await interaction.response.send_message(
            f"Saved **{name.strip()}**: {_fmt(when[0], when[1])}{year}. 🎂", ephemeral=True
        )

    async def other_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=r["name"][:100], value=r["name"][:100])
            for r in self._others()
            if current.casefold() in r["name"].casefold()
        ][:25]

    @app_commands.command(name="remove_other", description="Remove a birthday for someone without Discord")
    @app_commands.autocomplete(name=other_autocomplete)
    async def remove_other(self, interaction: discord.Interaction, name: str) -> None:
        if not self.bot.is_mod(interaction.user):
            await interaction.response.send_message("Only mods can remove these.", ephemeral=True)
            return
        rows = self._others()
        keep = [r for r in rows if r["name"].casefold() != name.strip().casefold()]
        if len(keep) == len(rows):
            await interaction.response.send_message(f"There's no birthday saved for **{name}**.", ephemeral=True)
            return
        self._save_others(keep)
        await interaction.response.send_message(f"Removed **{name}**.", ephemeral=True)

    async def _wish_others(self, state: dict, now: datetime) -> bool:
        """Happy birthday to people without Discord (at the birthday hour, in the server's time zone)."""
        others = getattr(self.cfg, "others", []) or []
        if not others or now.hour < self.cfg.hour:
            return False
        posted = state.setdefault("others_posted", {})
        due = others_due(others, posted, now.date())
        if not due:
            return False
        channel = self.bot.text_channel(self.cfg.channel)
        if channel is None:
            return False
        lib = movies.load()
        template = getattr(self.cfg, "others_message", "") or "🎉 Happy birthday to **{name}**{age}!{note}"
        for key, person in due:
            try:
                await channel.send(
                    other_message(template, person, now.date(), watched_with(lib, person["name"]))[:2000],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException as e:
                log.warning("Couldn't post %s's birthday: %s", person["name"], e)
                continue
            posted[key] = now.date().isoformat()
            log.info("Wished %s a happy birthday", person["name"])
        return True

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

        if await self._wish_others(state, now):
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
