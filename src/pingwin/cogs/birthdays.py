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


# --------------------------------------------------------------------- the Star birthdays message
def parse_died(text: str) -> tuple[int, int | None, int | None] | None:
    """(year, month, day) from '2015', 'Aug 30, 2015' or '2015-08-30'; month and day are None for a bare year."""
    t = str(text or "").strip()
    if re.fullmatch(r"\d{4}", t):
        return int(t), None, None
    p = parse_birthday(t)
    return (p[2], p[0], p[1]) if p and p[2] else None


def star_people(others: list) -> list[dict]:
    """The people without Discord, read from the settings rows (rows that can't be read are skipped)."""
    out = []
    for o in others:
        name = str(getattr(o, "name", "") or "").strip()
        when = parse_birthday(str(getattr(o, "date", "") or ""))
        if not name or when is None:
            continue
        died = parse_died(str(getattr(o, "died", "") or ""))
        out.append(
            {
                "name": name,
                "m": when[0],
                "d": when[1],
                "y": when[2],
                "note": str(getattr(o, "note", "") or "").strip(),
                "died": died[0] if died else None,
                "died_on": died,
            }
        )
    return out


def age_on(y: int, m: int, d: int, when: date) -> int:
    return when.year - y - ((when.month, when.day) < (m, d))


def _day(m: int, d: int, y: int | None = None) -> str:
    return f"{MONTHS[m - 1][:3]} {d}" + (f", {y}" if y else "")


def life_line(p: dict) -> str:
    """'Born Sep 2, 1964' or 'Aug 2, 1939 – Aug 30, 2015 · died at 76'."""
    if not p.get("died_on"):
        return f"Born {_day(p['m'], p['d'], p.get('y'))}" if p.get("y") else ""
    dy, dm, dd = p["died_on"]
    if not p.get("y"):
        return f"Died {_day(dm, dd, dy) if dm else dy}"
    if dm is None:
        return f"{p['y']}–{dy}"
    at = age_on(p["y"], p["m"], p["d"], date(dy, dm, dd))
    return f"{_day(p['m'], p['d'], p['y'])} – {_day(dm, dd, dy)} · died at {at}"


def nights_with(lib: dict, name: str) -> list[tuple[str, str | None]]:
    """(title, date watched) for each movie night with someone in the cast or crew, oldest first, one per movie."""
    key = name.casefold()
    seen, out = set(), []
    for e in lib.get("history", []):
        m = lib["movies"].get(e["movie_id"], {})
        names = {
            re.sub(r"\s*\(.*?\)\s*$", "", n).strip().casefold()
            for k in ("stars", "director", "writer")
            for n in re.split(r"[,\n]", str(m.get(k) or ""))
        }
        if key in names and m.get("id") not in seen:
            seen.add(m.get("id"))
            out.append((m.get("title", "?"), e.get("date")))
    return out


def films_line(nights: list[tuple[str, str | None]], limit: int = 8) -> str:
    if not nights:
        return ""

    def one(t: str, day: str | None) -> str:
        if not day:
            return t
        y, mo = day[:4], int(day[5:7])
        return f"{t} ({MONTHS[mo - 1][:3]} {y})"

    shown = [one(t, d) for t, d in nights[:limit]]
    more = len(nights) - len(shown)
    return f"🍿 Our movie nights with them ({len(nights)}): " + ", ".join(shown) + (f" and {more} more" if more else "")


def star_board(people: list[dict], lib: dict, today: date, days: int = 14, template: str = "") -> tuple[str, bool]:
    """The Star birthdays message, and whether someone's birthday is today."""
    template = template or "🎉 Happy birthday to **{name}**{age}!{note}"
    lines = ["⭐ **Star birthdays**"]
    todays = [p for p in people if is_birthday(p["m"], p["d"], today)]
    for p in todays:
        lines += ["", other_message(template, p, today, [])]
        lines += [f"-# {x}" for x in (life_line(p), films_line(nights_with(lib, p["name"]))) if x]
    soon = []
    for p in people:
        if p in todays:
            continue
        for year in (today.year, today.year + 1):
            when = date(year, p["m"], min(p["d"], calendar.monthrange(year, p["m"])[1]))
            if when > today:
                soon.append(((when - today).days, year, p))
                break
    soon.sort(key=lambda x: (x[0], x[2]["name"]))
    window = [x for x in soon if x[0] <= days] or [x for x in soon if x[0] == soon[0][0]]
    if window:
        lines += ["", "📅 **Coming up**" if any(x[0] <= days for x in window) else "📅 **Next up**"]
        for away, year, p in window:
            age = ""
            if p.get("y"):
                n = year - p["y"]
                age = f", would be {n}" if p.get("died") else f", turning {n}"
            lines.append(
                f"**{_day(p['m'], p['d'])}** · {p['name']}{age} · " + ("tomorrow" if away == 1 else f"in {away} days")
            )
    if not todays and not window:
        lines.append("No birthdays saved yet.")
    lines.append(f"-# Updated {_day(today.month, today.day, today.year)}")
    text = "\n".join(lines)
    return (text if len(text) <= 2000 else text[:1990] + "…"), bool(todays)


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

    async def _star_board(self, state: dict, now: datetime) -> bool:
        """Keep one Star birthdays message: edited in place each day, and deleted and posted again on a day with a
        birthday (Discord doesn't notify anyone about edits)."""
        people = star_people(getattr(self.cfg, "others", []) or [])
        board = state.setdefault("star_board", {})
        channel = self.bot.text_channel(self.cfg.channel)
        if not people or channel is None or now.hour < self.cfg.hour:
            return False
        text, has_today = star_board(
            people,
            movies.load(),
            now.date(),
            int(getattr(self.cfg, "stars_days", 14) or 14),
            getattr(self.cfg, "others_message", "") or "",
        )
        today = now.date().isoformat()
        new_day = board.get("day") != today
        if board.get("text") == text and not new_day:
            return False
        old = board.get("message")
        same_channel = board.get("channel") == channel.id
        if old and (not same_channel or (new_day and has_today)):
            old_channel = channel if same_channel else self.bot.get_channel(int(board.get("channel") or 0))
            if old_channel is not None:
                try:
                    await old_channel.get_partial_message(int(old)).delete()
                except discord.HTTPException:
                    pass  # already gone
            old = None
        if old:
            try:
                await channel.get_partial_message(int(old)).edit(content=text)
            except discord.HTTPException:
                old = None  # someone deleted it: post a new one
        if not old:
            try:
                msg = await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as e:
                log.warning("Couldn't post the Star birthdays message: %s", e)
                return False
            board["message"], board["channel"] = msg.id, channel.id
            log.info("Posted the Star birthdays message%s", " (a birthday today)" if has_today else "")
        board["day"], board["text"] = today, text
        return True

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

        stars = self._star_board if getattr(self.cfg, "stars_board", True) else self._wish_others
        if await stars(state, now):
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
