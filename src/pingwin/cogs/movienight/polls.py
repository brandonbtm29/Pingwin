"""Movie polls: starting, voting, swapping, and finishing them."""

from __future__ import annotations

import asyncio
import random
import secrets
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord

from ... import breaks, franchise, movies, rankedvote, store

if TYPE_CHECKING:
    pass
from .common import (
    DEFAULT_RUNTIME,
    GOLD,
    PINGWIN_POLLS,
    POLL_MAX,
    POLL_NAMES,
    POLL_TYPES,
    STATE,
    _new_nomination,
    _same_title,
    _same_year,
    is_franchise,
    log,
    parse_typed_movie,
    pick_movies,
    poll_close,
    poll_details_embed,
    series_label,
    tallies,
    voter_count,
    winners,
)
from .views import poll_view


class PollsMixin:
    """Part of the movie night cog (MovieNight, in cog.py)."""

    async def look_up_typed(self, text: str) -> tuple[list[dict[str, Any]], list[str]]:
        """Movies typed one per line, looked up for their proper title and year. Returns (movies, the lines
        that couldn't be found: IMDb links with no title that OMDb doesn't know)."""
        found: list[dict[str, Any]] = []
        missed: list[str] = []
        for line in (text or "").splitlines()[: POLL_MAX * 2]:
            t = parse_typed_movie(line)
            if t is None:
                continue
            m = await self.details(t["title"], t["year"], t["imdb_id"])
            if m:
                t = {"title": m["title"], "year": m.get("year") or t["year"], "movie_id": m["id"]}
            elif not t["title"]:
                missed.append(t["imdb_id"])
                continue
            if not any(_same_title(x, t["title"], t.get("year")) for x in found):
                found.append(t)
        return found, missed

    async def poll_from_dashboard(
        self,
        s,
        ids: list[str],
        typed: str,
        hours: int | None,
        question: str,
        poll_type: str | None,
        by: int,
        ranks: int | None = None,
    ) -> str:
        """Start a poll from the dashboard: watchlist movies by id plus typed ones (added to the watchlist).
        Raises ValueError with what to fix."""
        data = self._data(store.load(STATE), s)
        if any(p["status"] == "open" for p in data["polls"]):
            raise ValueError(f"There's already a {s.name} poll running. End it first.")
        unwatched = {n["id"]: n for n in data["nominations"] if not n.get("watched")}
        picks = [unwatched[i] for i in dict.fromkeys(ids) if i in unwatched]
        found, missed = await self.look_up_typed(typed)
        if missed:
            raise ValueError("Couldn't find " + ", ".join(missed) + ". Type the title instead.")
        new = []
        for t in found:
            n = next((n for n in unwatched.values() if _same_title(n, t["title"], t.get("year"))), None)
            if n is None:
                new.append(t)
            elif all(n["id"] != p["id"] for p in picks):
                picks.append(n)
        total = len(picks) + len(new)
        if not 2 <= total <= POLL_MAX:
            raise ValueError(f"Pick 2 to {POLL_MAX} movies (that's {total}).")
        picks += [self.add_to_watchlist(s, t, by) for t in new]
        closes, hours = self.poll_timing(s, hours)
        ranked, multiple = self.poll_style(s, poll_type)
        ranked = ranked or (ranks is not None and poll_type is None)
        msg, problem = await self.post_poll(
            s,
            picks,
            hours,
            question or f"{s.name}: what should we watch?",
            closes,
            ranked,
            by=by,
            multiple=multiple,
            ranks=ranks,
        )
        if problem:
            raise ValueError(problem)
        return f"Poll posted in #{msg.channel.name} with {total} movies."

    def add_to_watchlist(self, s, data: dict[str, Any], by: int) -> dict[str, Any]:
        """Put a movie typed for a poll on the series' watchlist (or find it there already), straight away: whoever
        starts a poll may pick its movies. Returns its watchlist entry."""
        state = store.load(STATE)
        noms = self._data(state, s)["nominations"]
        n = next((x for x in noms if not x.get("watched") and _same_title(x, data["title"], data.get("year"))), None)
        if n is None:
            n = _new_nomination(data, by)
            noms.append(n)
            store.save(STATE, state)
            log.info("Added %s to the %s watchlist for a poll", data["title"], s.name)
        return n

    def _update_poll(self, s, message_id: int, **changes: Any) -> dict | None:
        """Change one poll in the saved state. It re-loads first (with nothing to wait on in between), so
        nominations and other changes made while we were waiting on Discord are kept."""
        state = store.load(STATE)
        p = next((x for x in self._data(state, s)["polls"] if x.get("message_id") == message_id), None)
        if p is None:
            return None
        p.update(changes)
        store.save(STATE, state)
        return p

    # ------------------------------------------------------------ polls
    def poll_style(self, s, poll_type: str | None) -> tuple[bool, bool | None]:
        """(ranked, more than one vote each) for a poll-type. None: the series' Ranked polls, and the server's
        "Let people vote for more than one" (the None)."""
        if poll_type in POLL_TYPES:
            return POLL_TYPES[poll_type]
        return bool(getattr(s, "ranked", False)), None

    def choose_movies(self, s, how: str, data: dict[str, Any]) -> list[dict]:
        """Pingwin picks a poll's movies the way `how` says (see pick_movies), from the series' watchlist."""
        done = [p for p in data["polls"] if p.get("status") != "open" and p.get("options")]
        last = max(done, key=lambda p: str(p.get("ends") or "")).get("options") if done else None
        mix = []
        for row in getattr(self.cfg, "pick_mix", ()) or ():
            name, k = str(getattr(row, "name", "")).strip(), str(getattr(row, "count", "")).strip()
            if name and k.isdigit() and int(k) > 0:
                mix.append((name, int(k)))
        genres = None
        if how == "by_genre":
            genres = {mid: str(m.get("genre") or "") for mid, m in movies.load()["movies"].items()}
        return pick_movies(data["nominations"], how, self.cfg.poll_options, last=last, mix=mix, genres=genres)

    async def run_auto_polls(self) -> None:
        """Series with "Post the next poll by itself": once a showing is over (auto_poll_minutes after its
        showtime) and a movie mod has answered that night's "did it happen?" card, post next week's poll,
        picked the way "Automatic polls pick" says. Turning it on starts with the next showing."""
        wait = timedelta(minutes=int(getattr(self.cfg, "auto_poll_minutes", 90) or 0))
        now = datetime.now(self.bot.features.tz)
        for s in self.all_series:
            if not (getattr(s, "auto_poll", False) and getattr(s, "showtime", "")) or is_franchise(s):
                continue
            last = breaks.last_showing(s.showtime, now)
            if last is None or now < last + wait:
                continue
            day = last.date().isoformat()
            state = store.load(STATE)
            data = self._data(state, s)
            done = data.get("auto_poll")
            if done is not None and done >= day:
                continue
            why = ""
            if done is None:
                why = "it was just turned on"
            elif self.skipped(s, last.date()) is not None:
                why = "that week was off"
            elif any(p["status"] == "open" for p in data["polls"]):
                why = "a poll is already running"
            elif self._pending_night(movies.load(), s, day) is not None:
                continue  # wait for a movie mod to say what we watched
            data["auto_poll"] = day  # saved before posting, so a slow post isn't repeated
            store.save(STATE, state)
            if why:
                log.info("%s: no automatic poll after %s (%s)", s.name, day, why)
                continue
            picks = self.choose_movies(s, getattr(self.cfg, "auto_pick", "carryover"), data)
            if len(picks) < 2:
                log.warning("%s: not enough movies on the watchlist for an automatic poll", s.name)
                continue
            closes, hours = self.poll_timing(s, None)
            ranked, multiple = self.poll_style(s, None)
            _, problem = await self.post_poll(
                s, picks, hours, f"{s.name}: what should we watch?", closes, ranked, multiple=multiple
            )
            if problem:
                log.warning("%s: couldn't post the automatic poll: %s", s.name, problem)
            else:
                log.info("%s: posted next week's poll by itself (%d movies)", s.name, len(picks))

    def poll_timing(self, s, hours: int | None) -> tuple[datetime, int]:
        """When a new poll for this series closes, and its length in hours (see poll_close)."""
        return poll_close(
            getattr(s, "showtime", ""),
            datetime.now(self.bot.features.tz),
            hours,
            self.cfg.poll_hours,
            self.series_breaks(s),
        )

    async def start_poll(
        self,
        interaction: discord.Interaction,
        s,
        picks: list[dict],
        hours: int,
        question: str,
        closes: datetime | None = None,
        ranked: bool = False,
        multiple: bool | None = None,
        ranks: int | None = None,
    ) -> discord.Message | None:
        """Post a poll for a slash command or its menu; a problem goes back to whoever ran it."""
        msg, problem = await self.post_poll(
            s,
            picks,
            hours,
            question,
            closes,
            ranked,
            by=interaction.user.id,
            fallback=interaction.channel,
            multiple=multiple,
            ranks=ranks,
        )
        if problem:
            await interaction.followup.send(problem, ephemeral=True)
        return msg

    async def post_poll(
        self,
        s,
        picks: list[dict],
        hours: int,
        question: str,
        closes: datetime | None = None,
        ranked: bool = False,
        *,
        by: int = 0,
        fallback=None,
        multiple: bool | None = None,
        ranks: int | None = None,
    ) -> tuple[discord.Message | None, str]:
        """Post a movie poll (from Discord or the dashboard) as Pingwin's own message. Returns (the message, "")
        or (None, what went wrong). `ranked`: a Vote button that ranks `ranks` movies (None: "Ranked polls:
        choices to rank"); otherwise a button per movie, one vote each or, with `multiple`, as many as you like
        (None: "Let people vote for more than one")."""
        # The polls channel when there is one (like #polls), else the series' own channel.
        channel = (
            self.bot.text_channel(getattr(self.cfg, "polls_channel", None))
            or self.bot.text_channel(s.channel)
            or fallback
        )
        if channel is None:
            return None, f"There's no channel to post it in: set *Polls go to* or the {s.name} channel."
        closes = closes or discord.utils.utcnow() + timedelta(hours=hours)
        early = closes < discord.utils.utcnow() + timedelta(hours=hours) - timedelta(minutes=1)
        if multiple is None:
            multiple = getattr(self.cfg, "multiple", False)
        kind = "ranked" if ranked else "approval" if multiple else "plurality"
        return await self._start_own_poll(s, channel, picks, question, closes, early, by, kind, ranks)

    async def _info_hint(self) -> str:
        """The line under a poll that points to /pingwin movie pollinfo (Features → Movie night → Polls)."""
        if not getattr(self.cfg, "poll_details", False):
            return ""
        mention = getattr(self.bot, "command_mention", None)
        cmd = await mention("movie pollinfo") if mention else "`/pingwin movie pollinfo`"
        return f"\n-# 🎬 IMDb links and plots for these movies: {cmd} (just for you)"

    async def poll_info_embeds(self, s=None) -> list[discord.Embed]:
        """The movies in each running poll (one series', or every series'), for /pingwin movie pollinfo."""
        state = store.load(STATE)
        lib = movies.load()
        embeds = []
        for series in [s] if s else self.all_series:
            data = self._data(state, series)
            noms = {n["id"]: n for n in data["nominations"]}
            for p in data["polls"]:
                if p.get("status") != "open":
                    continue
                items = []
                for oid in p.get("options", []):
                    n = noms.get(oid)
                    if n is None:  # taken off the watchlist since: the ranked poll still has its name
                        if oid not in p.get("labels", {}):
                            continue
                        n = {"title": p["labels"][oid], "year": None}
                    m = lib["movies"].get(n.get("movie_id") or "") or _same_year(
                        movies.find_movie(lib, n["title"], n.get("year")), n.get("year")
                    )
                    if m is None or movies.needs_details(m, time.time()):  # misses wait a week, as elsewhere
                        m = await self.details(n["title"], n.get("year")) or m
                    items.append((n, m))
                embed = poll_details_embed(items, f"🎬 {series_label(series)}: the movies in the poll")
                if embed is not None:
                    embeds.append(embed)
        return embeds

    # ------------------------------------------------------------ Pingwin's polls
    async def _start_own_poll(
        self, s, channel, picks, question, closes, early, by, kind: str, ranks: int | None = None
    ) -> tuple[discord.Message | None, str]:
        """Pingwin's own poll message: a button per movie (one vote each, or approval), or a Vote button that
        ranks them. Unlike a Discord poll, it can rank, and a movie in it can be swapped while it runs."""
        pid = secrets.token_hex(4)
        record = {
            "kind": kind,
            "pid": pid,
            "question": question[:250],
            "labels": {n["id"]: n["title"] + (f" ({n['year']})" if n.get("year") else "") for n in picks},
            "ballots": {},
            "channel_id": channel.id,
            "options": [n["id"] for n in picks],
            "ends": closes.astimezone(discord.utils.utcnow().tzinfo).isoformat(),
            "created": discord.utils.utcnow().isoformat(),
            "status": "open",
            "by": by,
        }
        if kind == "ranked":  # how many each voter ranks: this poll's, else the setting (never more than it has)
            want = int(ranks or getattr(self.cfg, "ranked_choices", 3) or 3)
            record["ranks"] = max(2, min(want, rankedvote.MAX_RANKS, len(picks)))
        role = self.bot.role_named(channel.guild, s.role)
        ts = int(closes.timestamp())
        note = f"Voting closes <t:{ts}:F> (<t:{ts}:R>)" + (", right when the movie starts. 🍿" if early else ".")
        if kind == "approval":
            note = f"Vote for every {'franchise' if is_franchise(s) else 'movie'} you'd be happy to watch. " + note
        note += await self._info_hint()
        try:
            msg = await channel.send(
                content=((role.mention + " ") if role else "") + note,
                embed=self.poll_embed(s, record),
                view=poll_view(record),
                allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[role] if role else False),
            )
        except discord.HTTPException as e:
            return None, f"Couldn't post the poll in #{channel.name}: {e.text or e}"
        record["message_id"] = msg.id
        state = store.load(STATE)
        self._data(state, s)["polls"].append(record)
        store.save(STATE, state)
        log.info("%s poll started (%s) with %d movies", s.name, kind, len(picks))
        return msg, ""

    def poll_embed(self, s, p: dict[str, Any], result: dict[str, Any] | None = None) -> discord.Embed:
        """The poll message: each movie's votes so far (first choices, on a ranked poll), or once closed the final
        count and, ranked, how the rounds went. Swaps are noted under the list."""
        labels = p.get("labels", {})
        kind = p.get("kind") or "plurality"
        if result is not None:
            counts = result["final"]
        elif kind == "ranked":
            counts = rankedvote.first_choices(p.get("ballots", {}), p["options"])
        else:
            counts = tallies(p)
        top = max(counts.values(), default=0) or 1
        lines = []
        for c in p["options"]:
            n = counts.get(c, 0)
            filled = round(8 * n / top)
            won = result is not None and c in result["winners"]
            lines.append(f"`{'█' * filled}{'░' * (8 - filled)}` **{n}** · {'🏆 ' if won else ''}{labels.get(c, '?')}")
        desc = "\n".join(lines)
        thing = "franchise" if is_franchise(s) else "movie"
        voters = voter_count(p)
        if result is not None:
            if len(result.get("rounds", [])) > 1:
                desc += "\n\n**How the count went**\n" + rankedvote.rounds_text(result, lambda c: labels.get(c, "?"))
            footer = f"{POLL_NAMES.get(kind, 'Poll')} · {voters} voted · closed"
        elif kind == "ranked":
            footer = (
                f"Ranked choice: tap Vote to rank up to {p.get('ranks', 3)}. The bars are first choices. If no "
                f"{thing} gets more than half, the last one drops out and its votes go to their next choice. "
                f"{voters} voted so far."
            )
        elif kind == "approval":
            footer = (
                f"Approval: tap every {thing} you'd be happy to watch, and tap one again to take it off. Most votes "
                f"wins. {voters} voted so far."
            )
        else:
            footer = (
                f"One vote each: tap a {thing} to vote, another to change your vote, or the same one to take it "
                f"back. {voters} voted so far."
            )
        swaps = (p.get("swaps") or [])[-5:]
        if swaps:
            desc += "\n\n" + "\n".join(f"*🔁 {x['in']} took the place of {x['out']}*" for x in swaps)
        title = f"🗳️ {p.get('question') or 'Poll'}" + (" (closed)" if result is not None else "")
        embed = discord.Embed(title=title[:256], description=desc[:4000], color=GOLD)
        embed.set_footer(text=footer[:2000])
        return embed

    def find_poll(self, pid: str) -> tuple[Any, dict[str, Any]] | None:
        state = store.load(STATE)
        for s in self.all_series:
            for p in self._data(state, s).get("polls", []):
                if p.get("pid") == pid:
                    return s, p
        return None

    def tap_vote(self, pid: str, uid: str, i: int) -> str:
        """Someone tapped movie number i on a one-vote or approval poll: vote for it, or take that vote back.
        On a one-vote poll it replaces their vote. Returns what to tell them."""
        state = store.load(STATE)
        for s in self.all_series:
            for p in self._data(state, s).get("polls", []):
                if p.get("pid") != pid:
                    continue
                if p["status"] != "open" or discord.utils.utcnow() >= discord.utils.parse_time(p["ends"]):
                    return "This poll has closed."
                if p.get("kind") == "ranked":
                    return "Tap Vote to rank your picks."
                if not 0 <= i < len(p["options"]):
                    return "That one isn't in the poll anymore."
                c, labels, approval = p["options"][i], p.get("labels", {}), p.get("kind") == "approval"
                ballots = p.setdefault("ballots", {})
                mine = [x for x in ballots.get(uid, []) if x in p["options"]]
                added = c not in mine
                mine = ([*mine, c] if approval else [c]) if added else [x for x in mine if x != c]
                if mine:
                    ballots[uid] = mine
                else:
                    ballots.pop(uid, None)
                store.save(STATE, state)
                name = labels.get(c, "that one")
                if not approval:
                    if added:
                        return (
                            f"🗳️ You voted for **{name}**.\n-# Tap another to change your vote, or this one again "
                            "to take it back."
                        )
                    return f"Took back your vote for **{name}**."
                picks = " · ".join(labels.get(x, "?") for x in p["options"] if x in mine)
                text = f"🗳️ Added **{name}**." if added else f"Took **{name}** off."
                text += f" Your picks: {picks}" if mine else " You haven't picked any."
                return text + "\n-# Tap one again to take it off, any time before the poll closes."
        return "I can't find that poll anymore."

    def swap_movie(self, s, old: str, new: str, by: int) -> tuple[str, dict[str, Any] | None, list[tuple[str, bool]]]:
        """Swap a movie in the series' running poll for another from its watchlist. Votes for the old one come off
        (on a ranked ballot, the picks after it move up). Returns (problem, the poll, [(voter, still has a vote)])
        for everyone who had voted for it."""
        state = store.load(STATE)
        data = self._data(state, s)
        p = next((x for x in data["polls"] if x["status"] == "open"), None)
        if p is None:
            return "No poll is running.", None, []
        if p.get("kind") not in PINGWIN_POLLS:
            return (
                "That one's a Discord poll, and Discord doesn't let anyone change a poll once it's posted. Polls "
                "started from now on are Pingwin's own, so they can be changed.",
                None,
                [],
            )
        if old not in p["options"]:
            return "That movie isn't in the poll.", None, []
        n = next((x for x in data["nominations"] if x["id"] == new and not x.get("watched")), None)
        if n is None:
            return f"Pick a movie from the {s.name} watchlist (nominate it first if it isn't there).", None, []
        if new in p["options"]:
            return "That movie is already in the poll.", None, []
        labels = p.setdefault("labels", {})
        was = labels.get(old) or "?"
        labels[new] = n["title"] + (f" ({n['year']})" if n.get("year") else "")
        p["options"][p["options"].index(old)] = new
        voters = []
        ballots = p.setdefault("ballots", {})
        for uid, ballot in list(ballots.items()):
            if old in ballot:
                rest = [x for x in ballot if x != old]
                voters.append((uid, bool(rest)))
                if rest:
                    ballots[uid] = rest
                else:
                    del ballots[uid]
        p.setdefault("swaps", []).append(
            {"out": was, "in": labels[new], "by": by, "at": discord.utils.utcnow().isoformat()}
        )
        store.save(STATE, state)
        log.info("%s poll: swapped %s for %s (%d votes taken off)", s.name, was, labels[new], len(voters))
        return "", p, voters

    async def _tell_swapped(self, s, p: dict[str, Any], voters: list[tuple[str, bool]], link: str) -> int:
        """DM everyone whose vote went with a swapped-out movie, with a link back to the poll. Returns how many
        couldn't get the DM (DMs turned off, or they left)."""
        swap = p["swaps"][-1]
        rest = "Your other picks moved up." if p.get("kind") == "ranked" else "Your other picks still count."
        missed = 0
        for uid, kept in voters:
            if kept:
                after = f"{rest} The poll: {link}" if link else rest
            else:
                after = f"You can vote again: {link}" if link else "You can vote again on the poll."
            text = (
                f"🔁 **{swap['out']}** was swapped out of the {series_label(s)} poll for **{swap['in']}**, so your "
                f"vote for it came off. {after}"
            )
            try:
                user = self.bot.get_user(int(uid)) or await self.bot.fetch_user(int(uid))
                await user.send(text)
            except (discord.HTTPException, ValueError):
                missed += 1
        return missed

    def save_ballot(self, pid: str, uid: str, ranking: list[str]) -> tuple[str, dict[str, Any] | None]:
        """Save someone's ranking (replacing their last one). Returns (problem, the poll)."""
        state = store.load(STATE)
        for s in self.all_series:
            for p in self._data(state, s).get("polls", []):
                if p.get("pid") != pid:
                    continue
                if p["status"] != "open" or discord.utils.utcnow() >= discord.utils.parse_time(p["ends"]):
                    return "This poll has closed.", None
                ballot = rankedvote.clean(ranking, p["options"])
                if not ballot:
                    return "Pick at least your first choice.", None
                p.setdefault("ballots", {})[uid] = ballot
                store.save(STATE, state)
                return "", p
        return "I can't find that poll anymore.", None

    def refresh_poll_soon(self, pid: str, delay: float = 2.0) -> None:
        """Show the new counts on the poll message, a moment later (votes in a burst make one edit)."""
        if pid in self._ranked_refresh:
            return
        self._ranked_refresh.add(pid)

        async def go() -> None:
            await asyncio.sleep(delay)
            self._ranked_refresh.discard(pid)
            found = self.find_poll(pid)
            if found is None or found[1]["status"] != "open":
                return
            s, p = found
            channel = self.bot.get_channel(p["channel_id"])
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.get_partial_message(p["message_id"]).edit(embed=self.poll_embed(s, p))
                except discord.HTTPException:
                    pass

        task = asyncio.create_task(go())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _finish_own(self, s, p: dict[str, Any]) -> bool:
        """Close one of Pingwin's polls: count it, show the result on the poll, and announce the winner."""
        ranked = p.get("kind") == "ranked"
        if ranked:
            result = rankedvote.instant_runoff(p.get("ballots", {}), p["options"])
        else:
            counts = tallies(p)
            result = {"winners": winners(list(counts.items())), "final": counts, "rounds": [], "voters": voter_count(p)}
        channel = self.bot.get_channel(p["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            self._update_poll(s, p["message_id"], status="gone")
            return True
        msg = channel.get_partial_message(p["message_id"])
        try:
            await msg.edit(embed=self.poll_embed(s, p, result), view=None)
        except discord.HTTPException as e:
            log.warning("Couldn't show the %s poll's result: %s", s.name, e)
        final, rounds = result["final"], len(result["rounds"])
        active = sum(final.values())

        def votes_for(pick: str) -> str:
            n = final.get(pick, 0)
            if not ranked:
                return f"{n} vote{'s' if n != 1 else ''}"
            if rounds > 1:
                return f"{n} of {active} votes after {rounds} rounds"
            return f"{n} of {result['voters']} first-choice votes"

        return await self._announce_winner(
            s, p, channel, msg, result["winners"], votes_for, {"result": {k: result[k] for k in ("winners", "final")}}
        )

    def _poll_showing(self, s, ends: str) -> datetime:
        """The showing a poll picked the movie for: the series' showtime the poll was closing for (a poll
        ended early on Sunday is still for Monday's showing), else when the poll closed."""
        closed = discord.utils.parse_time(ends).astimezone(self.bot.features.tz)
        showing = breaks.next_showing(
            getattr(s, "showtime", "") or "", closed - timedelta(hours=6), self.series_breaks(s)
        )
        return showing or closed

    async def finish(self, s, p: dict) -> bool:
        """Close a poll whose time is up: announce the winner, then log it for its showing (or line up the
        next franchise). False while Discord is still counting.

        This waits on Discord (and OMDb) along the way, so `p` is only read here: changes are saved by
        re-loading the state right before saving, which keeps nominations made in the meantime."""
        if p.get("kind") in PINGWIN_POLLS:
            return await self._finish_own(s, p)
        channel = self.bot.get_channel(p["channel_id"])
        try:
            msg = await channel.fetch_message(p["message_id"])
        except (discord.HTTPException, AttributeError):
            self._update_poll(s, p["message_id"], status="gone")
            return True
        poll = msg.poll
        if poll is None:
            self._update_poll(s, p["message_id"], status="gone")
            return True
        if not poll.is_finalised() and p.get("end_early") and not p.get("end_sent"):
            # Showtime: close it now instead of waiting for Discord's whole-hour timer.
            self._update_poll(s, p["message_id"], end_sent=True)
            try:
                await msg.end_poll()
                await asyncio.sleep(3)
                msg = await channel.fetch_message(p["message_id"])
                poll = msg.poll or poll
            except discord.HTTPException as e:
                log.warning("Couldn't end the %s poll at showtime: %s", s.name, e)
        late = discord.utils.utcnow() - discord.utils.parse_time(p["ends"]) > timedelta(minutes=5)
        if not poll.is_finalised() and not late:
            return False  # Discord is still counting
        answers = sorted(poll.answers, key=lambda a: a.id)
        counts = [(p["options"][i], a.vote_count) for i, a in enumerate(answers) if i < len(p["options"])]

        def votes_for(pick: str) -> str:
            votes = dict(counts)[pick]
            return f"{votes} vote{'s' if votes != 1 else ''}"

        return await self._announce_winner(s, p, channel, msg, winners(counts), votes_for)

    async def _announce_winner(
        self, s, p: dict, channel, msg, top: list[str], votes_for, save: dict | None = None
    ) -> bool:
        """After a poll closes: announce the winner (or a tie), then line up the next franchise or ask the
        movie mods to confirm the night. `top` is the options with the most votes, `votes_for(id)` how many."""
        noms = {n["id"]: n for n in self._data(store.load(STATE), s)["nominations"]}
        labels = p.get("labels", {})

        def title_of(i: str) -> str:
            return (noms.get(i) or {}).get("title") or labels.get(i) or "the winner"

        role = self.bot.role_named(channel.guild, s.role)
        winner = None
        if not top:
            text = self.bot.persona.say("movie_poll_none", series=series_label(s))
        elif len(top) > 1 and self.cfg.tie == "mods":
            names = " and ".join(f"**{title_of(i)}**" for i in top)
            text = self.bot.persona.say("movie_poll_tie", names=names)
        else:
            pick = random.choice(top) if len(top) > 1 else top[0]
            extra = " (picked at random from a tie)" if len(top) > 1 else ""
            key = "franchise_poll_winner" if is_franchise(s) else "movie_poll_winner"
            text = self.bot.persona.say(
                key, title=title_of(pick), votes=votes_for(pick), extra=extra, series=series_label(s)
            )
            winner = noms.get(pick)
        # Save the result: re-loaded first, with nothing to wait on before saving.
        state = store.load(STATE)
        data = self._data(state, s)
        cur = next((x for x in data["polls"] if x.get("message_id") == p["message_id"]), None)
        if cur is None or cur.get("status") != "open":
            return True  # handled meanwhile
        cur["status"] = "done"
        cur.update(save or {})
        if winner:
            if is_franchise(s) and franchise.next_part(data.get("queue")) is not None:
                # The current franchise still has parts to go: the winner starts once its last part is logged.
                data["next_franchise"] = winner["title"]
                log.info("%s: the next franchise is %s, after %s", s.name, winner["title"], data["queue"]["name"])
            elif is_franchise(s):
                # The winner is the next franchise: start its queue (a mod adds the movies), not a movie night.
                data["queue"] = franchise.new_queue(winner["title"], paused=True)
                data.pop("next_franchise", None)
                log.info("%s: the next franchise is %s", s.name, winner["title"])
        store.save(STATE, state)
        try:
            await msg.reply(
                ((role.mention + " ") if role else "") + text,
                allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[role] if role else False),
            )
        except discord.HTTPException as e:
            log.warning("Couldn't announce the %s winner: %s", s.name, e)
        if winner and not is_franchise(s) and self.cfg.mark_watched:
            # The movie mods confirm it once that showing is over (it joins the history and gets marked
            # watched then), so a skipped week doesn't end up in the history.
            when = self._poll_showing(s, cur["ends"])
            m = await self._card_movie(winner["title"], winner.get("year"))
            runtime = (m or {}).get("runtime") or DEFAULT_RUNTIME
            await self.ask_night(
                s,
                when.date().isoformat(),
                {k: winner.get(k) for k in ("title", "year") if winner.get(k)},
                "The poll picked",
                when.timestamp() + (runtime + 10) * 60,
            )
        log.info("%s poll finished: %s", s.name, text)
        return True
