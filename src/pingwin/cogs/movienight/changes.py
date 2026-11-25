"""Changes to watchlists and the history, with approvals, and deciding a movie night."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord

from ... import franchise, movies, store, voicewatch
from ...features import slug

if TYPE_CHECKING:
    pass
from .common import (
    DEFAULT_RUNTIME,
    GOLD,
    STATE,
    _known,
    _new_nomination,
    _same_title,
    _same_year,
    candidate_label,
    describe_request,
    is_franchise,
    log,
    series_label,
)
from .views import Decision, RequestButton


class ChangesMixin:
    """Part of the movie night cog (MovieNight, in cog.py)."""

    # ------------------------------------------------------------ changes (with approvals)
    async def submit(self, kind: str, series: str, data: dict[str, Any], who: discord.abc.User) -> str:
        """Apply a change now (mods, or approvals off) or send it to the mods. Returns a reply."""
        if not self.cfg.approvals or self.is_movie_mod(who):
            return await self.apply(kind, series, data, who.id)
        channel = self.approval_channel()
        if channel is None:
            return "Changes need a mod's OK, but no approval channel is set up. Ask a mod."
        rid = secrets.token_hex(4)
        req = {
            "id": rid,
            "kind": kind,
            "series": series,
            "data": data,
            "by": who.id,
            "by_name": getattr(who, "display_name", who.name),
            "at": time.time(),
            "status": "pending",
        }
        view = discord.ui.View(timeout=None)
        view.add_item(RequestButton(rid, "ok"))
        view.add_item(RequestButton(rid, "no"))
        embed = discord.Embed(title="🎬 Movie night change", description=describe_request(req), color=GOLD)
        embed.set_footer(text=f"Asked by {req['by_name']}")
        if data.get("poster"):
            embed.set_thumbnail(url=data["poster"])
        try:
            msg = await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            log.warning("Couldn't post an approval card: %s", e)
            return "I couldn't reach the mods' channel. Ask a mod."
        req["card"] = [channel.id, msg.id]
        # Load the library only now, after the card is posted, so a review or request saved meanwhile is kept.
        lib = movies.load()
        lib["pending"][rid] = req
        movies.save(lib)
        log.info("Movie change from %s sent for approval: %s", req["by_name"], describe_request(req))
        return "Sent to the mods for approval. 👍"

    async def _night_candidates(self, s, day: str) -> list[dict[str, Any]]:
        """Likely movies for a night: the franchise queue's next part, the polls, then the watchlist."""
        out: list[dict[str, Any]] = []
        state = store.load(STATE)
        data = self._data(state, s)
        nxt = franchise.next_part(data.get("queue")) if is_franchise(s) else None
        if nxt:
            part, total = franchise.part_number(data["queue"], nxt)
            out.append({"title": nxt["title"], "year": nxt.get("year"), "why": f"part {part} of {total} in the queue"})
        show = datetime.fromisoformat(day).replace(tzinfo=self.bot.features.tz)
        polls = await self._read_polls(s, show - timedelta(days=15), show + timedelta(days=1))
        for c in voicewatch.poll_picks(polls):
            title, year = voicewatch.split_year(c["title"])
            out.append({"title": title, "year": year, "why": c["why"]})
        waiting = [n for n in data.get("nominations", []) if not n.get("watched")]
        for n in sorted(waiting, key=lambda n: n.get("added") or "", reverse=True)[:6]:
            out.append({"title": n["title"], "year": n.get("year"), "why": "on the watchlist"})
        lib = movies.load()
        recent = {
            movies.norm(lib["movies"].get(e["movie_id"], {}).get("title", ""))
            for e in lib["history"]
            if e.get("series") == s.name and e.get("date") and e["date"] < day
        }
        seen: set[str] = set()
        picks = []
        for c in out:
            k = movies.norm(c["title"])
            if k and k not in seen and k not in recent:
                seen.add(k)
                picks.append(c)
        return picks[:24]

    async def _decide_detected(
        self,
        lib: dict[str, Any],
        req: dict[str, Any],
        approve: bool,
        mod: discord.abc.User,
        choice: int | None,
        custom: dict[str, Any] | None,
        choice_label: str | None = None,
        rate: bool | None = None,
    ) -> str:
        d = req["data"]
        who = getattr(mod, "display_name", mod.name)
        if not approve:
            req["status"], req["decided_by"] = "denied", who
            movies.save(lib)
            msg = f"⏭️ {who} says {req['series']} on {d['date']} was skipped. Nothing was added."
            if d.get("expected") and self.series(req["series"]) and is_franchise(self.series(req["series"])):
                msg += f" {d['expected']['title']} stays next in the queue."
        else:
            pick = custom
            if pick is None and choice is not None and 0 <= choice < len(d.get("candidates", [])):
                pick = d["candidates"][choice]
                if choice_label is not None and choice_label != candidate_label(pick):
                    s = self.series(req["series"])
                    if s is not None:
                        await self._refresh_night_card(s, req)
                    return Decision("The list of movies changed. Pick again on the updated card.", ok=False)
            if pick is None and choice is None:
                pick = d.get("expected")  # "Yes, we watched it"
            if not pick or not str(pick.get("title", "")).strip():
                return Decision("Pick a movie (or Something else…) first.", ok=False)
            s = self.series(req["series"])
            if s is None:
                return Decision(f"There's no {req['series']} series anymore.", ok=False)
            req["status"], req["decided_by"] = "approved", who
            movies.save(lib)
            year = pick.get("year")
            post = bool(self.cfg.rate) and rate is not False  # "Log it, no Rate it" on the movie pages
            e, _created = await self.add_night(
                s,
                str(pick["title"]).strip(),
                int(year) if str(year or "").isdigit() else None,
                pick.get("imdb_id"),
                d["date"],
                rate=post,
                rate_after=d.get("show_end"),
            )
            lib = movies.load()
            entry = next((x for x in lib["history"] if x["id"] == e["id"]), None)
            if entry is not None:
                entry["voice"] = d.get("voice")
                if d.get("starter") and not entry.get("ratings") and not entry.get("starter"):
                    entry["starter"] = {**d["starter"], "by": who}  # pinged when the Rate it button goes up
                movies.save(lib)
            m = lib["movies"].get(e["movie_id"], {"title": pick["title"]})
            label = f"{m['title']} ({m['year']})" if m.get("year") else m["title"]
            msg = f"✅ {who} logged **{label}** for {req['series']} on {d['date']}."
            if post:
                msg += " The Rate it button is on its way."
            elif self.cfg.rate:
                msg += " No Rate it button for this one."
        log.info(msg.replace("**", ""))
        card = req.get("card")
        channel = self.bot.get_channel(card[0]) if card else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.get_partial_message(card[1]).edit(content=msg, embed=None, view=None)
            except discord.HTTPException:
                pass
        return msg

    async def decide(
        self,
        rid: str,
        approve: bool,
        mod: discord.abc.User,
        choice: int | None = None,
        custom: dict[str, Any] | None = None,
        starter: str | None = None,
        choice_label: str | None = None,
        rate: bool | None = None,
    ) -> str:
        """A movie mod's answer to a waiting card. For a "did it happen?" card, `rate` False logs the night
        without its Rate it button (None: the usual, a button if Rate it is on)."""
        lib = movies.load()
        req = lib["pending"].get(rid)
        if req is not None and req.get("kind") == "detected" and starter is not None:  # from the movie pages
            guild = getattr(self.bot, "home_guild", None)
            member = guild.get_member(int(starter)) if guild is not None and starter.isdigit() else None
            if member is not None and not member.bot:
                req["data"]["starter"] = {"uid": str(member.id), "name": member.display_name}
            elif not starter:
                req["data"].pop("starter", None)
        if req is None or req.get("status") != "pending":
            return Decision("This one was already handled.", ok=False)
        if req["kind"] == "detected":
            return await self._decide_detected(lib, req, approve, mod, choice, custom, choice_label, rate)
        req["status"] = "approved" if approve else "denied"
        req["decided_by"] = getattr(mod, "display_name", mod.name)
        movies.save(lib)
        line = describe_request(req)
        if not approve:
            log.info("%s denied: %s", req["decided_by"], line)
            msg = f"❌ Denied by {req['decided_by']}: {line} (asked by {req['by_name']})"
        else:
            result = await self.apply(req["kind"], req["series"], req["data"], req["by"])
            log.info("%s approved: %s", req["decided_by"], line)
            msg = f"✅ Approved by {req['decided_by']}: {line} (asked by {req['by_name']}). {result}"
        card = req.get("card")
        channel = self.bot.get_channel(card[0]) if card else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.get_partial_message(card[1]).edit(content=msg, embed=None, view=None)
            except discord.HTTPException:
                pass
        return msg

    def import_list(self, series: str, rows: list[dict[str, Any]], by: int, label: str = "") -> dict[str, int]:
        """Add a list's waiting movies to a watchlist at once (no channel posts). Skips watched ones."""
        s = self.series(series)
        if s is None:
            raise ValueError(f"There's no {series} series.")
        state = store.load(STATE)
        noms = self._data(state, s)["nominations"]
        lib = movies.load()
        # Titles with their years, so a remake (The Thing 2011) isn't taken for the original (1982).
        seen: dict[str, list[Any]] = {}
        for n in noms:
            if not n.get("watched"):
                seen.setdefault(movies.norm(n["title"]), []).append(n.get("year"))
        watched: dict[str, list[Any]] = {}
        for e in lib["history"]:
            m = lib["movies"].get(e["movie_id"])
            if m:
                watched.setdefault(movies.norm(m["title"]), []).append(m.get("year"))
        stats = {"added": 0, "watched": 0, "already": 0}
        for r in rows:
            if r.get("done") or _known(watched, r["title"], r.get("year")):
                stats["watched"] += 1
                continue
            if _known(seen, r["title"], r.get("year")):
                stats["already"] += 1
                continue
            seen.setdefault(movies.norm(r["title"]), []).append(r.get("year"))
            found = _same_year(movies.find_movie(lib, r["title"], r.get("year")), r.get("year"))
            noms.append(
                {
                    "id": secrets.token_hex(4),
                    "title": r["title"],
                    "year": r.get("year"),
                    "movie_id": found["id"] if found else None,
                    "by": by,
                    "added": discord.utils.utcnow().isoformat(),
                    "watched": None,
                    "list": label[:40],
                    "note": r.get("note", ""),
                }
            )
            stats["added"] += 1
        store.save(STATE, state)
        return stats

    def watchlist_change(self, series: str, item_id: str, op: str, to: str = "") -> str:
        """A movie mod's change to a watchlist from the movie pages: remove, move to another list, or put a
        watched movie back. Raises ValueError with a message for the mod."""
        s = self.series(series)
        if s is None:
            raise ValueError(f"There's no {series} watchlist anymore.")
        state = store.load(STATE)
        noms = self._data(state, s)["nominations"]
        n = next((x for x in noms if x.get("id") == item_id), None)
        if n is None:
            raise ValueError("That movie isn't on the list anymore.")
        if op == "remove":
            noms.remove(n)
            msg = f"Removed **{n['title']}** from the {series_label(s)} watchlist."
        elif op == "unwatch":
            n["watched"] = None
            msg = f"**{n['title']}** is back on the {series_label(s)} watchlist."
        elif op == "move":
            t = self.series(to)
            if t is None or slug(t.name) == slug(s.name):
                raise ValueError("Pick another watchlist to move it to.")
            dest = self._data(state, t)["nominations"]
            if any(_same_title(x, n["title"], n.get("year")) and not x.get("watched") for x in dest):
                raise ValueError(f"{n['title']} is already on the {series_label(t)} watchlist.")
            noms.remove(n)
            dest.append({**n, "list": ""})
            msg = f"Moved **{n['title']}** to the {series_label(t)} watchlist."
        else:
            raise ValueError("Unknown change.")
        store.save(STATE, state)
        return msg

    async def apply(self, kind: str, series: str, data: dict[str, Any], by: int) -> str:
        s = self.series(series)
        if kind == "edit":
            return self._apply_edit(data)
        if s is None:
            return f"There's no {series} series anymore."
        if kind == "night":
            e, created = await self.add_night(
                s, data["title"], data.get("year"), data.get("imdb_id"), data["date"], rate=bool(data.get("rate"))
            )
            m = movies.load()["movies"].get(e["movie_id"], {"title": data["title"]})
            label = f"{m['title']} ({m['year']})" if m.get("year") else m["title"]
            if not created:
                return f"{label} on {data['date']} was already in the history."
            return f"Added **{label}** to the {s.name} history ({data['date']})."
        state = store.load(STATE)
        noms = self._data(state, s)["nominations"]
        if kind == "nominate":
            if any(_same_title(n, data["title"], data.get("year")) and not n.get("watched") for n in noms):
                return f"**{data['title']}** is already on the watchlist."
            noms.append(_new_nomination(data, by))
            store.save(STATE, state)
            channel = self.announce_channel(s)
            if channel:
                try:
                    await channel.send(
                        self.bot.persona.say(
                            "movie_nominated", title=data["title"], series=series_label(s), by=f"<@{by}>"
                        ),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass
            return f"Added **{data['title']}** to the {s.name} watchlist."
        n = next((x for x in noms if x["id"] == data.get("id")), None)
        if kind == "remove":
            if n is None:
                return "That movie isn't on the list anymore."
            noms.remove(n)
            store.save(STATE, state)
            return f"Removed **{n['title']}** from the {s.name} watchlist."
        if kind == "watched":
            if n is None:
                return "That movie isn't on the list anymore."
            if data.get("watched", True) and n.get("watched"):  # say, set as tonight's movie meanwhile
                return f"**{n['title']}** is already marked as watched."
            if not data.get("watched", True):
                n["watched"] = None
                store.save(STATE, state)
                return f"**{n['title']}** marked as not watched."
            day = data.get("date") or datetime.now(self.bot.features.tz).date().isoformat()
            n["watched"] = datetime.fromisoformat(day).replace(hour=20, tzinfo=UTC).isoformat()
            store.save(STATE, state)
            e, created = await self.add_night(s, n["title"], n.get("year"), None, day, rate=bool(data.get("rate")))
            rated = " The Rate it button is on its way." if data.get("rate") and self.cfg.rate else ""
            added = f" Added to the history for {day}." if created else f" It was already in the history for {day}."
            return f"**{n['title']}** marked as watched.{added}{rated}"
        if kind == "tonight":
            # The showing it was asked for (worked out when it was asked), not the one after the approval.
            when = datetime.fromisoformat(data["when"]) if data.get("when") else self.tonight_showing(s)
            m = await self._card_movie(data["title"], data.get("year"))
            runtime = (m or {}).get("runtime") or DEFAULT_RUNTIME
            expected = {"title": data["title"], **({"year": data["year"]} if data.get("year") else {})}
            if m and m.get("imdb_id"):
                expected["imdb_id"] = m["imdb_id"]
            await self.ask_night(
                s, when.date().isoformat(), expected, "Set as tonight's movie", when.timestamp() + (runtime + 10) * 60
            )
            return (
                f"Tonight's {s.name} movie is **{data['title']}**. A movie mod confirms it after the movie, "
                "and then it joins the history" + (" and everyone gets a Rate it button." if self.cfg.rate else ".")
            )
        return "Unknown change."

    def _apply_edit(self, data: dict[str, Any]) -> str:
        lib = movies.load()
        e = next((x for x in lib["history"] if x["id"] == data.get("hid")), None)
        if e is None:
            return "That movie night isn't in the history anymore."
        ch = data.get("changes", {})
        if "date" in ch:
            e["date"] = ch["date"] or None
            lib["history"].sort(key=lambda x: (x.get("date") or "9999", x.get("created", 0)))  # date order
        if "series" in ch:
            e["series"] = ch["series"]
        if "title" in ch or "year" in ch:
            old = lib["movies"].get(e["movie_id"])
            title = (ch.get("title") or (old or {}).get("title") or "").strip()
            year = ch["year"] if "year" in ch else (old or {}).get("year")
            # Another movie with exactly this title and year: point at it. Otherwise it's a correction to this
            # movie, so fix it in place and keep its poster, IMDb link and details.
            same = next(
                (
                    x
                    for x in lib["movies"].values()
                    if x is not old
                    and movies.norm(x["title"]) == movies.norm(title)
                    and str(x.get("year") or "") == str(year or "")
                ),
                None,
            )
            if same is not None:
                e["movie_id"] = same["id"]
            elif old is not None:
                old["title"], old["year"] = title, year
            else:
                e["movie_id"] = movies.upsert_movie(lib, {"title": title, "year": year})["id"]
        if "poster" in ch and e["movie_id"] in lib["movies"]:
            lib["movies"][e["movie_id"]]["poster"] = ch["poster"] or None
        movies.save(lib)
        return "History updated."

    async def add_night(
        self,
        s,
        title: str,
        year: int | None,
        imdb_id: str | None,
        day: str,
        rate: bool = False,
        rate_after: float | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Add a movie night that already happened (from the movie pages). Returns (entry, newly added).

        It joins the history on its date, is marked watched on the series' watchlist, and, if asked,
        gets a Rate it button in Discord right away."""
        found = await self.details(title, year, imdb_id)
        lib = movies.load()
        m = lib["movies"].get(found["id"]) if found else None
        if m is None:
            m = movies.upsert_movie(lib, {"title": title.strip(), "year": year})
        e = next((x for x in lib["history"] if x.get("date") == day and x["movie_id"] == m["id"]), None)
        created = e is None
        if created:
            e = movies.new_entry(m, day, s.name, source="pages")
            lib["history"].append(e)
            lib["history"].sort(key=lambda x: (x.get("date") or "9999", x.get("created", 0)))
        if rate and self.cfg.rate and not e.get("prompt") and getattr(s, "channel", None):
            e["rate_at"] = max(time.time(), float(rate_after or 0))  # not before the movie is over
            e["rate_channel"] = s.channel
        movies.save(lib)
        self._night_logged(s, m, day, title)
        await self._close_night_card(s, day, e["id"], movies.label(m))
        await self._franchise_wrap_up(s)
        return e, created

    async def _close_night_card(self, s, day: str, entry_id: str, label: str) -> None:
        """A night logged some other way (the movie pages, /movie watched) while its "did it happen?" card was
        still waiting: the card is done, and who was in voice goes on the history entry."""
        lib = movies.load()
        req = self._pending_night(lib, s, day)
        if req is None:
            return
        req["status"], req["decided_by"] = "approved", "logged elsewhere"
        voice = req["data"].get("voice")
        entry = next((e for e in lib["history"] if e.get("id") == entry_id), None)
        if voice and entry is not None and not entry.get("voice"):
            entry["voice"] = voice
        movies.save(lib)
        msg = f"✅ **{label}** was added to the {req['series']} history for {day}, so this night is done."
        card = req.get("card")
        channel = self.bot.get_channel(card[0]) if card else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.get_partial_message(card[1]).edit(content=msg, embed=None, view=None)
            except discord.HTTPException:
                pass
