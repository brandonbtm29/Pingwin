"""Rate it posts after a movie, and the question game."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import discord

from ... import movies, voicerate

if TYPE_CHECKING:
    pass
from .common import GOLD, log
from .views import RateButton, StarterButton


class RatingsMixin:
    """Part of the movie night cog (MovieNight, in cog.py)."""

    # ------------------------------------------------------------ ratings
    def _rater_names(self, lib: dict[str, Any]) -> dict[str, str]:
        """Who each rater is, as the movie pages show them: the name or member set under People, else
        their current server name."""
        guild = getattr(self.bot, "home_guild", None)
        members = {}
        if guild is not None:
            for uid in set(movies.rater_links(lib).values()):
                member = guild.get_member(int(uid))
                if member is not None:
                    members[uid] = member.display_name
        return movies.rater_names(lib, members)

    def _prompt_embed(
        self, entry: dict[str, Any], movie: dict[str, Any], closes: float, closed: bool = False
    ) -> discord.Embed:
        names = self._rater_names(movies.load())
        rs = sorted(entry.get("ratings", {}).items(), key=lambda kv: kv[1].get("at", 0))
        avg = movies.ratings_avg(entry)
        lines = [
            f"**{names.get(k) or r['name']}**: {r['score']:g}/10"
            for k, r in rs
            if isinstance(r.get("score"), (int, float))
        ]
        desc = (
            "Ratings are closed."
            if closed
            else self.bot.persona.say("movie_rate_prompt", closes=f"<t:{int(closes)}:R>")
        )
        if not closed and getattr(self.cfg, "voice_ratings", False) and voicerate.available():
            desc += "\n🎙️ *Rather say it? Reply here with a voice message, or DM me one.*"
        if lines:
            desc += "\n\n" + "\n".join(lines)
        if avg is not None:
            desc += f"\n\n**Group average: {avg:g}/10**" + (
                f" · IMDb {movie['imdb_rating']:g}" if movie.get("imdb_rating") else ""
            )
        embed = discord.Embed(title=f"⭐ {movies.label(movie)}", description=desc[:4000], color=GOLD)
        if movie.get("poster"):
            embed.set_thumbnail(url=movie["poster"])
        if not closed:
            waiting = [
                names.get(r["ask_for"]) or r.get("ask_for_name") or "someone"
                for _k, r in movies.open_questions(entry)
                if r.get("ask_for")
            ]
            foot = []
            if entry.get("starter") and not entry.get("ratings"):
                foot.append(f"{entry['starter']['name']} goes first")
            if q := movies.open_question(entry):
                foot.append(f"Next person's question: {q}")
            if waiting:
                foot.append("Questions waiting for " + ", ".join(waiting))
            if foot:
                embed.set_footer(text="\n".join(foot)[:2000])
        return embed

    async def post_prompt(self, entry: dict[str, Any], lib: dict[str, Any]) -> None:
        channel = self.bot.text_channel(entry.get("rate_channel"))
        if channel is None:
            entry["prompt"] = {"skipped": True}
            return
        movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
        closes = time.time() + self.cfg.rate_hours * 3600
        try:
            msg = await channel.send(embed=self._prompt_embed(entry, movie, closes), view=self._prompt_view(entry))
        except discord.HTTPException as e:
            log.warning("Couldn't post the Rate it button: %s", e)
            entry["prompt"] = {"skipped": True}
            return
        entry["prompt"] = {"channel": channel.id, "message": msg.id, "closes": closes, "closed": False}
        log.info("Asked everyone to rate %s", movie.get("title"))
        if entry.get("starter") and not entry["starter"].get("ping"):  # picked when the night was confirmed
            entry["starter"]["ping"] = await self._ping_starter(entry, lib)

    def _prompt_view(self, entry: dict[str, Any]) -> discord.ui.View:
        """Rate it, plus "Pick who starts" until someone's picked or has rated."""
        view = discord.ui.View(timeout=None)
        view.add_item(RateButton(entry["id"]))
        if not entry.get("starter") and not entry.get("ratings"):
            view.add_item(StarterButton(entry["id"]))
        return view

    def starter_problem(self, hid: str) -> str:
        """Why nobody can pick who rates first anymore ("" while they can)."""
        entry = next((e for e in movies.load()["history"] if e["id"] == hid), None)
        p = (entry or {}).get("prompt") or {}
        if entry is None or not p.get("message") or p.get("closed"):
            return "Ratings for this one have closed."
        if entry.get("starter"):
            return f"{entry['starter']['name']} was already picked to go first."
        if entry.get("ratings"):
            return "Someone already started the ratings."
        return ""

    async def pick_starter(self, hid: str, member, by) -> str:
        """Someone picked who rates first from the Rate it post: ping them."""
        if problem := self.starter_problem(hid):
            return problem
        if member is None or getattr(member, "bot", False):
            return "Pick someone in the server (bots don't rate movies)."
        lib = movies.load()
        entry = next(e for e in lib["history"] if e["id"] == hid)
        who = getattr(by, "display_name", getattr(by, "name", ""))
        entry["starter"] = {"uid": str(member.id), "name": member.display_name, "by": who}
        movies.save(lib)
        ping = await self._ping_starter(entry, lib)
        fresh = movies.load()
        cur = next((e for e in fresh["history"] if e["id"] == hid), None)
        if cur is not None and cur.get("starter"):
            cur["starter"]["ping"] = ping
            movies.save(fresh)
            await self.refresh_prompt(cur, fresh)
        return f"Pinged {member.display_name} to go first." if ping else f"{member.display_name} goes first."

    async def _ping_starter(self, entry: dict[str, Any], lib: dict[str, Any]) -> list[int] | None:
        """Ping whoever was picked to rate first, under the Rate it post. Returns [channel id, message id]."""
        st, p = entry["starter"], entry.get("prompt") or {}
        channel = self.bot.get_channel(p.get("channel")) if p.get("channel") else None
        if not isinstance(channel, discord.TextChannel):
            return None
        movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
        text = self.bot.persona.say("movie_rate_starter", target=f"<@{st['uid']}>", title=movies.label(movie))
        view = discord.ui.View(timeout=None)
        view.add_item(RateButton(entry["id"]))
        try:
            msg = await channel.send(
                text[:2000],
                view=view,
                reference=channel.get_partial_message(p["message"]).to_reference(fail_if_not_exists=False),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=[discord.Object(int(st["uid"]))], replied_user=False
                ),
            )
        except discord.HTTPException as e:
            log.warning("Couldn't ping who rates first: %s", e)
            return None
        return [channel.id, msg.id]

    async def refresh_prompt(self, entry: dict[str, Any], lib: dict[str, Any]) -> None:
        p = entry.get("prompt") or {}
        channel = self.bot.get_channel(p.get("channel")) if p.get("channel") else None
        if not isinstance(channel, discord.TextChannel):
            return
        movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
        try:
            await channel.get_partial_message(p["message"]).edit(
                embed=self._prompt_embed(entry, movie, p["closes"], p.get("closed", False)),
                view=None if p.get("closed") else self._prompt_view(entry),
            )
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------- the question game
    async def after_rating(self, hid: str, key: str, old: dict[str, Any] | None) -> None:
        """After a rating is saved (the Rate it form, the movie pages, a voice message): ping the member its
        question is for, show an answer on the ping of the question it answers, and update the Rate it post."""
        old = old or {}
        lib = movies.load()
        entry = next((e for e in lib["history"] if e["id"] == hid), None)
        r = (entry or {}).get("ratings", {}).get(key)
        if entry is None or r is None:
            return
        names = self._rater_names(lib)
        same = (old.get("ask"), old.get("ask_for")) == (r.get("ask"), r.get("ask_for"))
        ping = old.get("ask_msg") if same else None
        if old.get("ask_msg") and not same:
            await self._delete_message(old["ask_msg"])  # out of date: the question or who it's for changed
        if ping is None and r.get("ask") and r.get("ask_for"):
            ping = await self._ping_question(entry, key, names, lib)
        if ping != r.get("ask_msg"):
            fresh = movies.load()
            cur = next((e for e in fresh["history"] if e["id"] == hid), None)
            if cur is not None and key in cur.get("ratings", {}):
                cur["ratings"][key]["ask_msg"] = ping
                movies.save(fresh)
        src = r.get("answered_from")
        if r.get("answer") and src and (src, r.get("answer")) != (old.get("answered_from"), old.get("answer")):
            asked = entry["ratings"].get(src) or {}
            if asked.get("ask_msg"):
                movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
                text = (
                    f"✅ **{names.get(src) or asked.get('name') or 'Someone'}** asked "
                    f"**{names.get(key) or r.get('name')}** about **{movies.label(movie)}**: *{r['answered']}*\n"
                    f"→ {r['answer']}"
                )
                await self._edit_message(asked["ask_msg"], text[:2000])
        st = entry.get("starter") or {}
        if st.get("uid") == key and st.get("ping") and not st.get("done"):
            movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
            name = names.get(key) or r.get("name") or st.get("name")
            await self._edit_message(st["ping"], f"✅ **{name}** kicked off the ratings for **{movies.label(movie)}**.")
            fresh = movies.load()
            cur = next((e for e in fresh["history"] if e["id"] == hid), None)
            if cur is not None and cur.get("starter"):
                cur["starter"]["done"] = True
                movies.save(fresh)
        if (entry.get("prompt") or {}).get("message"):
            await self.refresh_prompt(entry, movies.load())

    async def _ping_question(
        self, entry: dict[str, Any], key: str, names: dict[str, str], lib: dict[str, Any]
    ) -> list[int] | None:
        """Ping the member a rater's question is for, under the Rate it post (with its button) while ratings
        are open. Returns [channel id, message id]."""
        r = entry["ratings"][key]
        p = entry.get("prompt") or {}
        is_open = bool(p.get("message")) and not p.get("closed")
        channel = self.bot.get_channel(p["channel"]) if p.get("channel") else None
        if not isinstance(channel, discord.TextChannel):
            s = self.series(entry.get("series") or "")
            channel = self.bot.text_channel(entry.get("rate_channel") or getattr(s, "channel", None))
        if channel is None:
            return None
        movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
        text = self.bot.persona.say(
            "movie_question_for",
            target=f"<@{r['ask_for']}>",
            asker=names.get(key) or r.get("name") or "Someone",
            title=movies.label(movie),
            question=r["ask"],
        )
        kw: dict[str, Any] = {
            "allowed_mentions": discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(int(r["ask_for"]))], replied_user=False
            )
        }
        if is_open:
            view = discord.ui.View(timeout=None)
            view.add_item(RateButton(entry["id"]))
            kw["view"] = view
            if p.get("channel") == channel.id:
                kw["reference"] = channel.get_partial_message(p["message"]).to_reference(fail_if_not_exists=False)
        else:
            text += "\n-# Ratings for this one have closed, so answer it on the movie pages (`/pingwin movie page`)."
        try:
            msg = await channel.send(text[:2000], **kw)
        except discord.HTTPException as e:
            log.warning("Couldn't ping someone about a rating question: %s", e)
            return None
        return [channel.id, msg.id]

    async def _edit_message(self, ref: list[int], text: str) -> None:
        channel = self.bot.get_channel(ref[0])
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.get_partial_message(ref[1]).edit(content=text, view=None)
            except discord.HTTPException:
                pass

    async def _delete_message(self, ref: list[int]) -> None:
        channel = self.bot.get_channel(ref[0])
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.get_partial_message(ref[1]).delete()
            except discord.HTTPException:
                pass

    async def close_prompt(self, entry: dict[str, Any], lib: dict[str, Any]) -> None:
        p = entry["prompt"]
        p["closed"] = True
        await self.refresh_prompt(entry, lib)
        channel = self.bot.get_channel(p.get("channel"))
        movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
        avg = movies.ratings_avg(entry)
        n = sum(1 for r in entry.get("ratings", {}).values() if isinstance(r.get("score"), (int, float)))
        if not isinstance(channel, discord.TextChannel) or not n:
            return
        text = self.bot.persona.say(
            "movie_summary", title=movies.label(movie), count=f"{n} rating{'s' if n != 1 else ''}", avg=f"{avg:g}"
        )
        if movie.get("imdb_rating"):
            diff = avg - movie["imdb_rating"]
            verdict = "kinder" if diff > 0 else "harsher" if diff < 0 else "spot on"
            text += f" (IMDb {movie['imdb_rating']:g}, we were {verdict})"
        url = self.view_url()
        if url:
            text += f"\nAll the comments: <{url}>"
        try:
            await channel.get_partial_message(p["message"]).reply(
                text, mention_author=False, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            pass
