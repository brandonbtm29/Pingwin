"""Breaks (weeks off) for a series."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import discord

from ... import breaks, franchise, movies, store
from ...features import slug

if TYPE_CHECKING:
    pass
from .common import GOLD, STATE, _same_year, is_franchise, log, series_label
from .views import BreakButton


class WeeksOffMixin:
    """Part of the movie night cog (MovieNight, in cog.py)."""

    # ------------------------------------------------------------ breaks (weeks off)
    def series_breaks(self, s) -> list[dict[str, Any]]:
        return list(store.load(STATE).get(slug(s.name), {}).get("breaks", []))

    def skipped(self, s, day: date) -> dict[str, Any] | None:
        """The break a showing on `day` falls in, if any."""
        return breaks.covering(self.series_breaks(s), day)

    def next_showing(self, s, after: datetime | None = None) -> datetime | None:
        """The series' next showing that isn't in a break (None without a showtime)."""
        after = after or datetime.now(self.bot.features.tz)
        return breaks.next_showing(getattr(s, "showtime", "") or "", after, self.series_breaks(s))

    def add_break(self, s, weeks: int, start: date | None, why: str = "", by: str = "") -> str:
        """Skip `weeks` showings, from the first one on or after `start` (default: the next one). Pingwin
        moves the series' Discord Event to the first showing after it. Returns a line for the mods."""
        if not getattr(s, "showtime", ""):
            return f"{series_label(s)} has no showtime, so there's nothing to skip."
        tz = self.bot.features.tz
        now = datetime.now(tz)
        # From a date: that day's showing counts even if it has already started (tonight is off after all).
        begin = now if start is None else datetime(start.year, start.month, start.day, tzinfo=tz)
        state = store.load(STATE)
        data = self._data(state, s)
        dates = breaks.plan(s.showtime, begin, weeks, data.get("breaks", []))
        if dates is None:
            return "I couldn't work out the showings to skip."
        data["breaks"] = breaks.prune(breaks.add(data.get("breaks", []), *dates, why=why, by=by), now.date())
        data["event_sync"] = True  # the Discord Event moves to the first showing after the break
        store.save(STATE, state)
        self._poke_events()
        b = breaks.covering(data["breaks"], dates[0])
        back = breaks.next_showing(s.showtime, now, data["breaks"])
        log.info("%s: break %s to %s (%s), by %s", s.name, b["from"], b["until"], why or "no reason", by)
        return f"🏖️ {series_label(s)}: {breaks.describe(b, back)}"

    def end_break(self, s, by: str = "") -> str:
        """End the current break and cancel the coming ones."""
        tz = self.bot.features.tz
        now = datetime.now(tz)
        state = store.load(STATE)
        data = self._data(state, s)
        before = data.get("breaks", [])
        data["breaks"] = breaks.end(before, now.date())
        if len(data["breaks"]) == len(before):
            return f"{series_label(s)} isn't on a break."
        data["event_sync"] = True
        store.save(STATE, state)
        self._poke_events()
        nxt = breaks.next_showing(getattr(s, "showtime", "") or "", now, data["breaks"])
        log.info("%s: break ended by %s", s.name, by)
        return f"✅ {series_label(s)} is back on" + (f": next showing <t:{int(nxt.timestamp())}:F>." if nxt else ".")

    def _poke_events(self) -> None:
        """Have the event reminders look at the Discord Events again soon (they move them past breaks)."""
        events = self.bot.get_cog("EventReminders") if hasattr(self.bot, "get_cog") else None
        if events is not None:
            events._rules_at = -1e9

    def event_breaks(self, event_name: str) -> tuple[Any, list[dict[str, Any]], bool] | None:
        """For the event reminders: the series a Discord Event is for, its breaks, and whether a break just
        changed (so its event should be lined up again even if it isn't in a break now)."""
        s = self.series_for_event(event_name)
        if s is None or not getattr(s, "showtime", ""):
            return None
        data = store.load(STATE).get(slug(s.name), {})
        return s, list(data.get("breaks", [])), bool(data.get("event_sync"))

    def event_synced(self, s) -> None:
        state = store.load(STATE)
        if self._data(state, s).pop("event_sync", None):
            store.save(STATE, state)

    async def _ask_break(self, s, q: dict[str, Any]) -> None:
        """A franchise is done: ask the movie mods how long a break to take before the next one."""
        channel = self.approval_channel()
        if channel is None:
            return
        key = slug(s.name)
        view = discord.ui.View(timeout=None)
        for weeks in (0, 2, 3, 4):
            view.add_item(BreakButton(key, weeks))
        up_next = self._data(store.load(STATE), s).get("next_franchise")
        nxt = self.next_showing(s)
        lines = [f"**{q['name']}** is finished. Taking a break before the next franchise?"]
        if up_next:
            lines.append(f"Up next: **{up_next}**.")
        if nxt:
            lines.append(f"Without a break, the next showing is <t:{int(nxt.timestamp())}:F>.")
        lines.append("Pingwin moves the Discord Event and skips polls and reminders until it's back.")
        embed = discord.Embed(title=f"🏖️ {series_label(s)}: a break?", description="\n".join(lines), color=GOLD)
        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            log.warning("Couldn't ask the movie mods about a break: %s", e)

    async def _franchise_wrap_up(self, s) -> None:
        """After a franchise's last part is logged: say so (once) and start the franchise that's up next."""
        if not is_franchise(s):
            return
        state = store.load(STATE)
        data = self._data(state, s)
        q = data.get("queue")
        if not franchise.finished(q) or q.get("wrapped"):
            return
        q["wrapped"] = True
        store.save(STATE, state)
        up_next = data.get("next_franchise")
        note = f"**{up_next}** already won the vote, so it's up next. 🍿" if up_next else ""
        await self._queue_post(s, "franchise_done", q, None, when=None, note=note)
        self._start_next_franchise(s)
        await self._ask_break(s, q)

    def _night_logged(self, s, m: dict[str, Any], day: str, title: str = "") -> None:
        """After a movie night is logged by hand (the movie pages, a voice card, /movie tonight): it comes off
        the series' watchlist, and if it's the franchise queue's next part, that part is done, so the queue
        moves on instead of announcing it again. `title` is the title as typed, if it differs."""
        state = store.load(STATE)
        data = self._data(state, s)
        when = datetime.fromisoformat(day).replace(hour=20, tzinfo=UTC).isoformat()
        hit = False
        for n in data["nominations"]:
            if n.get("watched"):
                continue
            # The same title from another year is a remake or a namesake, so it stays on the watchlist.
            if n.get("movie_id") == m["id"] or (
                movies.norm(n["title"]) == movies.norm(m["title"]) and _same_year(m, n.get("year"))
            ):
                n["watched"] = when
                hit = True
        if is_franchise(s):
            part = franchise.mark_logged(data.get("queue"), [m["title"], title], m.get("year"), day)
            if part is not None:
                name = data["queue"].get("name")
                log.info("%s: %s was logged by hand, so the %s queue moves on", s.name, part["title"], name)
                hit = True
        if hit:
            store.save(STATE, state)
