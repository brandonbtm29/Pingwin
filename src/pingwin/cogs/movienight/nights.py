"""Noticing movie nights in voice, the night cards, and where a series' vote stands."""

from __future__ import annotations

import re
import secrets
import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord

from ... import breaks, franchise, movies, rankedvote, store, voicewatch
from ...features import slug

if TYPE_CHECKING:
    pass
from .common import (
    GENERAL,
    GOLD,
    PINGWIN_POLLS,
    STATE,
    _day_label,
    _duration,
    _with_first,
    _yr,
    candidate_label,
    is_franchise,
    log,
    series_label,
    tallies,
    voter_count,
)
from .views import NightPick, RequestButton, StarterPick


class NightsMixin:
    """Part of the movie night cog (MovieNight, in cog.py)."""

    # ------------------------------------------------------------ noticing movie nights in voice
    async def watch_voice(self) -> None:
        """Around each showtime, note who's in the group's voice channel. When the night is over and enough
        people stayed long enough, ask the movie mods which movie it was (unless it's already logged)."""
        if not getattr(self.cfg, "voice_check", False):
            return
        guild = self.bot.home_guild
        if guild is None:
            return
        now_dt = datetime.now(self.bot.features.tz)
        now = time.time()
        state = store.load(STATE)
        watch = state.setdefault("voice_watch", {})
        changed = False
        for s in self.all_series:
            key = slug(s.name)
            show = voicewatch.current_show(getattr(s, "showtime", "") or "", now_dt)
            if show is not None and breaks.covering(state.get(key, {}).get("breaks"), show.date()):
                show = None  # a week off
            day = show.date().isoformat() if show else None
            sess = watch.get(key)
            if sess and sess["day"] != day:  # that night's window has closed
                if not sess.get("done"):
                    await self._voice_verdict(s, sess)
                watch.pop(key)
                changed, sess = True, None
            if show is None:
                continue
            if sess is None:
                sess = watch[key] = voicewatch.new_session(day, now)
            elif sess.get("done") or now - sess["last"] < 55:  # about once a minute is plenty
                continue
            channel = self._movie_voice_channel(guild, s, show)
            here = {str(m.id) for m in (channel.members if channel else []) if not m.bot}
            voicewatch.sample(sess, here, now, channel.id if channel else None)
            changed = True
            if voicewatch.is_over(sess, show, now):
                await self._voice_verdict(s, sess)
                sess["done"] = True
        if changed:
            # Re-load first: the watchlists may have changed while we waited on Discord. Only the voice
            # sessions are ours to save.
            state = store.load(STATE)
            state["voice_watch"] = watch
            store.save(STATE, state)

    def _movie_voice_channel(self, guild: discord.Guild, s, show: datetime):
        """The voice channel of this series' Discord Event (if there is one around showtime), else the busiest."""
        names = {n.casefold() for n in (s.name, getattr(s, "full_name", "")) if n}
        near = [
            e
            for e in guild.scheduled_events
            if e.channel is not None
            and e.status in (discord.EventStatus.scheduled, discord.EventStatus.active)
            and abs((e.start_time - show).total_seconds()) < 3 * 3600
        ]
        named = [e for e in near if any(n in e.name.casefold() for n in names)]
        for e in named + near:
            if isinstance(e.channel, discord.VoiceChannel | discord.StageChannel):
                return e.channel
        rooms = [*guild.voice_channels, *guild.stage_channels]
        busiest = max(rooms, key=lambda c: sum(1 for m in c.members if not m.bot), default=None)
        return busiest if busiest and any(not m.bot for m in busiest.members) else None

    async def _voice_verdict(self, s, sess: dict[str, Any]) -> None:
        need_people = int(getattr(self.cfg, "voice_min_people", 3) or 3)
        need_minutes = int(getattr(self.cfg, "voice_min_minutes", 45) or 45)
        people = voicewatch.stayed(sess, need_minutes)
        day = sess["day"]
        if len(people) < need_people:
            log.info("%s on %s: %d stayed in voice, not a movie night", s.name, day, len(people))
            return
        voice = {"people": len(people), "minutes": voicewatch.minutes_together(sess), "peak": sess.get("peak", 0)}
        lib = movies.load()
        logged = next((e for e in lib["history"] if e.get("date") == day and e.get("series") == s.name), None)
        if logged is not None:  # already in the history (a mod logged it)
            logged["voice"] = voice
            movies.save(lib)
            log.info("%s on %s: %d in voice, already logged", s.name, day, len(people))
            return
        guild = self.bot.home_guild
        names = []
        for uid in people:
            m = guild.get_member(int(uid)) if guild else None
            names.append(m.display_name if m else "someone")
        await self._night_request(s, day, voice=voice, names=names)
        log.info("%s on %s: %d stayed in voice; asked the movie mods which movie", s.name, day, len(people))

    async def ask_night(
        self, s, day: str, expected: dict[str, Any] | None, why: str, show_end: float | None = None
    ) -> None:
        """A poll, the franchise queue or /movie tonight says what's on for a night: ask the movie mods to
        confirm it once it's over, instead of adding it to the history straight away (some weeks get skipped)."""
        skip = breaks.covering(self.series_breaks(s), date.fromisoformat(day))
        if skip is not None:
            log.info("%s on %s is in a break, so the movie mods aren't asked about it", s.name, day)
            return
        lib = movies.load()
        if any(e.get("date") == day and e.get("series") == s.name for e in lib["history"]):
            return  # a mod already logged that night
        await self._night_request(s, day, expected=expected, why=why, show_end=show_end)

    def _pending_night(self, lib: dict[str, Any], s, day: str) -> dict[str, Any] | None:
        return next(
            (
                r
                for r in lib["pending"].values()
                if r.get("kind") == "detected"
                and r.get("status") == "pending"
                and r.get("series") == s.name
                and r["data"].get("date") == day
            ),
            None,
        )

    async def _night_request(
        self,
        s,
        day: str,
        *,
        expected: dict[str, Any] | None = None,
        why: str = "",
        show_end: float | None = None,
        voice: dict[str, Any] | None = None,
        names: list[str] | None = None,
    ) -> None:
        """One "did this movie night happen, and with which movie?" card per series and night. A second
        source for the same night (the voice check after the poll, or /movie tonight after the poll) adds to
        that card instead of posting another."""
        lib = movies.load()
        req = self._pending_night(lib, s, day)
        if req is not None:
            d = req["data"]
            if expected:
                d["expected"], d["why"] = expected, why
                if show_end:
                    d["show_end"] = show_end
                d["candidates"] = _with_first(d.get("candidates", []), {**expected, "why": why})
            if voice:
                d["voice"], d["names"] = voice, names or []
            movies.save(lib)
            await self._refresh_night_card(s, req)
            return
        try:
            candidates = await self._night_candidates(s, day)
        except Exception:  # a guess list is nice to have; the card still works without it
            log.exception("Couldn't gather likely movies for %s", s.name)
            candidates = []
        if expected:
            candidates = _with_first(candidates, {**expected, "why": why})
        rid = secrets.token_hex(4)
        data: dict[str, Any] = {"date": day, "candidates": candidates[:24]}
        if expected:
            data.update(expected=expected, why=why, show_end=show_end)
        if voice:
            data.update(voice=voice, names=names or [])
        req = {
            "id": rid,
            "kind": "detected",
            "series": s.name,
            "data": data,
            "by": 0,
            "by_name": "Pingwin",
            "at": time.time(),
            "status": "pending",
        }
        channel = self.approval_channel()
        if channel is not None:
            embed, view = self._night_card(s, req)
            try:
                msg = await channel.send(embed=embed, view=view)
                req["card"] = [channel.id, msg.id]
            except discord.HTTPException as e:
                log.warning("Couldn't post the movie night card: %s", e)
        lib = movies.load()  # re-loaded: posting the card waited on Discord
        if self._pending_night(lib, s, day) is None:
            lib["pending"][rid] = req
            movies.save(lib)

    def _night_card(self, s, req: dict[str, Any]) -> tuple[discord.Embed, discord.ui.View]:
        d = req["data"]
        exp, voice = d.get("expected"), d.get("voice")
        lines = []
        if exp:
            lines.append(f"{d.get('why') or 'Up tonight'}: **{exp['title']}**{_yr(exp)}.")
        if voice:
            who = f": {', '.join(d.get('names') or [])}" if d.get("names") else ""
            lines.append(
                f"**{voice['people']} people** were in voice for about **{_duration(voice['minutes'])}**{who}."
            )
        rate = " and posts the Rate it button" if self.cfg.rate else ""
        if exp:
            lines.append(
                f"Once it's over, confirm it: that adds it to the history{rate}. Watched something else? Pick it "
                "from the list. Didn't happen? Click **We skipped it**."
            )
        else:
            lines.append(f"Which movie was it? Picking one adds it to the history{rate}.")
        if self.cfg.rate and d.get("starter"):
            lines.append(f"🎬 **{d['starter']['name']}** rates first (I'll ping them when the Rate it button is up).")
        label = f"{series_label(s)} on {_day_label(d['date'])}"
        title = f"🎬 Did {label} happen?" if exp and not voice else f"🎬 Looks like {label} happened!"
        embed = discord.Embed(title=title, description="\n".join(lines), color=GOLD)
        options = [
            discord.SelectOption(
                label=candidate_label(c),
                description=(c.get("why") or "")[:100] or None,
                value=str(i),
            )
            for i, c in enumerate(d.get("candidates", [])[:24])
        ]
        options.append(discord.SelectOption(label="Something else…", value="other", emoji="✏️"))
        view = discord.ui.View(timeout=None)
        if exp:
            view.add_item(RequestButton(req["id"], "ok", label=f"Yes, we watched {exp['title']}"[:80]))
        view.add_item(NightPick(req["id"], options))
        view.add_item(RequestButton(req["id"], "no", label="We skipped it"))
        if self.cfg.rate:
            view.add_item(StarterPick(req["id"]))
        return embed, view

    def set_night_starter(
        self, rid: str, member, picked: bool = True
    ) -> tuple[str, tuple[discord.Embed, discord.ui.View] | None]:
        """A movie mod picks (or clears) who rates first on a night's card. Returns (problem, new card)."""
        lib = movies.load()
        req = lib["pending"].get(rid)
        s = self.series(req["series"]) if req else None
        if req is None or req.get("status") != "pending" or req.get("kind") != "detected" or s is None:
            return "This one was already handled.", None
        if picked and member is None:
            return "I can't find that person in the server.", None
        if member is not None and getattr(member, "bot", False):
            return "Bots don't rate movies.", None
        if member is None:
            req["data"].pop("starter", None)
        else:
            req["data"]["starter"] = {"uid": str(member.id), "name": member.display_name}
        movies.save(lib)
        return "", self._night_card(s, req)

    async def _refresh_night_card(self, s, req: dict[str, Any]) -> None:
        card = req.get("card")
        channel = self.bot.get_channel(card[0]) if card else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed, view = self._night_card(s, req)
        try:
            await channel.get_partial_message(card[1]).edit(embed=embed, view=view)
        except discord.HTTPException as e:
            log.info("Couldn't update the movie night card: %s", e)

    async def _read_polls(self, s, after: datetime, before: datetime | None = None) -> list[dict[str, Any]]:
        """The series' polls, oldest first: Pingwin's own, and Discord polls in the movie polls channel (and the
        series' channel), like older movie polls or a Horror / Not Horror poll."""
        channels = []
        for cid in (getattr(self.cfg, "polls_channel", None), getattr(s, "channel", None)):
            ch = self.bot.text_channel(cid)
            if ch is not None and ch not in channels:
                channels.append(ch)
        polls = []
        for ch in channels:
            try:  # newest first: with `after`, Discord would otherwise give the oldest 200 since then
                async for m in ch.history(limit=200, after=after, before=before, oldest_first=False):
                    if m.poll is None:
                        continue
                    q = m.poll.question
                    polls.append(
                        {
                            "q": str(getattr(q, "text", q) or ""),
                            "answers": [(str(a.text or ""), a.vote_count or 0) for a in m.poll.answers],
                            "at": m.created_at.timestamp(),
                        }
                    )
            except discord.HTTPException as e:
                log.info("Couldn't read polls in #%s: %s", ch.name, e)
        # Pingwin's own polls: where each stands (the votes so far, or a ranked poll's runoff), or its final count.
        for rp in self._data(store.load(STATE), s).get("polls", []):
            if rp.get("kind") not in PINGWIN_POLLS or not rp.get("created"):
                continue
            at = discord.utils.parse_time(rp["created"]).timestamp()
            if at < after.timestamp() or (before is not None and at > before.timestamp()):
                continue
            final = (rp.get("result") or {}).get("final")
            if final is None:
                ballots = rp.get("ballots", {})
                ranked = rp["kind"] == "ranked"
                final = rankedvote.instant_runoff(ballots, rp["options"])["final"] if ranked else tallies(rp)
            labels = rp.get("labels", {})
            polls.append(
                {
                    "q": rp.get("question", ""),
                    "answers": [(labels.get(c, "?"), final.get(c, 0)) for c in rp["options"]],
                    "at": at,
                    "voters": voter_count(rp),
                }
            )
        polls.sort(key=lambda p: p["at"])
        return polls

    def series_for_event(self, name: str):
        """The series a Discord Event is for, by its short or full name in the event's name."""
        text = name.casefold()
        for s in self.all_series:
            if s is GENERAL:  # the General watchlist isn't a movie night
                continue
            names = [n for n in (getattr(s, "full_name", ""), s.name) if n]
            if any(re.search(r"(?<!\w)" + re.escape(n.casefold()) + r"(?!\w)", text) for n in names):
                return s
        return None

    async def reminder_info(self, event_name: str, start: datetime) -> dict[str, Any] | None:
        """For an event reminder about a movie night: where the vote stands (or tonight's franchise part),
        and a nudge if few people have voted. None if the event isn't a movie night."""
        s = self.series_for_event(event_name)
        if s is None or not self.cfg.enabled:
            return None
        data = self._data(store.load(STATE), s)
        nxt = franchise.next_part(data.get("queue")) if is_franchise(s) else None
        if nxt and not (data.get("queue") or {}).get("paused"):
            part, total = franchise.part_number(data["queue"], nxt)
            name = (data.get("queue") or {}).get("name") or "the franchise"
            return {
                "line": f"🍿 Tonight: **{franchise.label(nxt)}**, part {part} of {total}.",
                "nudge": "",
                "votes": None,
                "movie": await self._card_movie(nxt["title"], nxt.get("year"), nxt.get("imdb_id")),
                "why": f"Part {part} of {total} of {name}",
            }
        polls = await self._read_polls(s, start - timedelta(days=15))
        st = voicewatch.standings(polls, (start - timedelta(days=7)).timestamp())
        if st is None:
            return None
        polls_ch = self.bot.text_channel(getattr(self.cfg, "polls_channel", None))
        rows = []
        for r in getattr(self.cfg, "vote_nudges", []) or []:
            below = str(getattr(r, "below", "") or "").strip()
            if below.isdigit():
                rows.append((int(below), str(getattr(r, "message", "") or "")))
        text = voicewatch.nudge(rows, st["votes"]).replace("{polls}", polls_ch.mention if polls_ch else "the polls")
        movie = None
        if st.get("pick"):
            title, year = voicewatch.split_year(st["pick"])
            movie = await self._card_movie(title, year)
        return {
            "line": voicewatch.standings_line(st),
            "nudge": text,
            "votes": st["votes"],
            "movie": movie,
            "why": "Leading the vote so far",
        }

    async def _card_movie(self, title: str, year: Any = None, imdb_id: str | None = None) -> dict[str, Any] | None:
        """The movie's details for a movie card (looked up on OMDb once if we don't have them)."""
        try:
            m = await self.details(title, year, imdb_id)
        except Exception:  # a card is a bonus: the post still goes out without it
            log.exception("Couldn't look up %s for a movie card", title)
            m = None
        return m or {"title": title, "year": year, "imdb_id": imdb_id}
