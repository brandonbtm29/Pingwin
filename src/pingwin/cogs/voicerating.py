"""Rate a movie by voice message: send Pingwin a voice message (in a DM, or as a reply to the Rate it post or
a question ping), and Pingwin writes down what you said and the score you gave.

Nothing is saved until the person who spoke taps Save on the card Pingwin replies with (or Edit, to fix
the score or the words, answer a question and ask one). The speech-to-text runs on this PC (see
pingwin.voicerate); the recording itself isn't kept.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands, tasks

from .. import movies, store, voicerate
from .movienight import RateModal

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.voicerating")
STATE = "voicerate"  # the cards waiting for Save: {"drafts": {id: draft}}
KEEP_DRAFTS = 2 * 86400
MAX_BYTES = 25 * 1024 * 1024
GOLD = discord.Color.gold()


def voice_attachment(message: discord.Message) -> discord.Attachment | None:
    """The recording in a Discord voice message (or, in a DM, any audio file)."""
    for a in message.attachments:
        audio = (a.content_type or "").startswith("audio/")
        if (message.flags.voice or (message.guild is None and audio)) and (audio or a.is_voice_message()):
            return a
    return None


def open_entries(lib: dict[str, Any]) -> list[dict[str, Any]]:
    """Movie nights whose Rate it post is up and still open, oldest first."""
    return [e for e in lib["history"] if (e.get("prompt") or {}).get("message") and not e["prompt"].get("closed")]


def replied_entry(lib: dict[str, Any], message_id: int) -> dict[str, Any] | None:
    """The movie night a message belongs to: its Rate it post, or a question ping under it."""
    for e in lib["history"]:
        if (e.get("prompt") or {}).get("message") == message_id:
            return e
        if any((r.get("ask_msg") or [None, None])[1] == message_id for r in e.get("ratings", {}).values()):
            return e
    return None


def pick_entry(entries: list[dict[str, Any]], uid: str) -> dict[str, Any] | None:
    """Without a reply to say which, the movie night someone is rating by voice: one where a question is
    waiting for them by name, else the newest open one."""
    for e in reversed(entries):
        q, asker = movies.question_for(e, uid)
        if q and asker and (e["ratings"].get(asker) or {}).get("ask_for") == uid:
            return e
    return entries[-1] if entries else None


class DraftButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:vr:(?P<did>[0-9a-f]+):(?P<op>save|edit|other|cancel)"
):
    LOOK = {
        "save": ("Save", "✅", discord.ButtonStyle.success),
        "edit": ("Edit", "✏️", discord.ButtonStyle.secondary),
        "other": ("Other movie", "🔁", discord.ButtonStyle.secondary),
        "cancel": ("Cancel", None, discord.ButtonStyle.secondary),
    }

    def __init__(self, did: str, op: str, disabled: bool = False) -> None:
        label, emoji, style = self.LOOK[op]
        super().__init__(
            discord.ui.Button(
                label=label, emoji=emoji, style=style, custom_id=f"pingwin:vr:{did}:{op}", disabled=disabled
            )
        )
        self.did, self.op = did, op

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["did"], match["op"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: VoiceRatings | None = interaction.client.get_cog("VoiceRatings")  # type: ignore[union-attr]
        if cog is None:
            await interaction.response.send_message("Voice ratings are off right now.", ephemeral=True)
            return
        await cog.press(interaction, self.did, self.op)


class VoiceRatings(commands.Cog):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.transcriber = voicerate.Transcriber()
        self.tidy.start()

    def cog_unload(self) -> None:
        self.tidy.cancel()

    @property
    def enabled(self) -> bool:
        cfg = self.bot.features.movienight
        return bool(cfg.enabled and cfg.rate and getattr(cfg, "voice_ratings", True))

    # ------------------------------------------------------------ drafts
    def _drafts(self) -> dict[str, Any]:
        return store.load(STATE).get("drafts", {})

    def _put(self, draft: dict[str, Any]) -> None:
        state = store.load(STATE)
        state.setdefault("drafts", {})[draft["id"]] = draft
        store.save(STATE, state)

    def forget(self, did: str) -> None:
        state = store.load(STATE)
        if state.get("drafts", {}).pop(did, None) is not None:
            store.save(STATE, state)

    @tasks.loop(minutes=10)
    async def tidy(self) -> None:
        self.transcriber.unload_if_idle()
        state = store.load(STATE)
        drafts = state.get("drafts", {})
        old = [k for k, d in drafts.items() if time.time() - d.get("at", 0) > KEEP_DRAFTS]
        if old:
            for k in old:
                drafts.pop(k)
            store.save(STATE, state)

    # ------------------------------------------------------------ voice messages
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not self.bot.is_active or not self.enabled:
            return
        att = voice_attachment(message)
        ref = message.reference.message_id if message.reference else None
        if att is None and not (message.flags.voice and ref):
            return
        lib = movies.load()
        uid = str(message.author.id)
        if message.guild is None:  # a DM: only from people in the server
            guild = self.bot.home_guild
            if att is None or guild is None or guild.get_member(message.author.id) is None:
                return
            entry = replied_entry(lib, ref) if ref else None
            entry = entry or pick_entry(open_entries(lib), uid)
            if entry is None:
                await self._reply(message, "There's nothing to rate right now. 🍿")
                return
        else:  # in the server: only replies to the Rate it post or a question ping
            if message.guild != self.bot.home_guild or not ref or (entry := replied_entry(lib, ref)) is None:
                return
            if att is None:  # Discord hides a voice message's file unless the reply pings Pingwin
                await self._reply(message, "🎙️ I can't hear that one. Keep the @ on when you reply, or DM me.")
                return
        if (entry.get("prompt") or {}).get("closed") or not (entry.get("prompt") or {}).get("message"):
            await self._reply(message, "Ratings for that one have closed.")
            return
        if not voicerate.available():
            await self._reply(message, "🎙️ I can't listen to voice messages on this PC yet. Tap **Rate it** to type it.")
            return
        if (att.duration or 0) > voicerate.MAX_SECONDS or att.size > MAX_BYTES:
            await self._reply(message, "That's a long one! Keep it under 3 minutes, or tap **Rate it** to type it.")
            return
        try:
            async with message.channel.typing():
                text = await self.transcriber.transcribe(await att.read())
        except Exception:
            log.exception("Couldn't turn a voice message into text")
            await self._reply(message, "Sorry, I couldn't make that recording out. Try again, or tap **Rate it**.")
            return
        if not text:
            await self._reply(message, "I didn't hear anything in that one. Try again a little closer to the mic?")
            return
        draft = {
            "id": secrets.token_hex(4),
            "hid": entry["id"],
            "uid": uid,
            "name": getattr(message.author, "display_name", message.author.name),
            "score": voicerate.spoken_score(text),
            "comment": text[:1000],
            "at": time.time(),
        }
        self._put(draft)
        embed, view = self.card(draft, movies.load())
        try:
            await message.reply(embed=embed, view=view, mention_author=False)
        except discord.HTTPException as e:
            log.warning("Couldn't reply to a voice rating: %s", e)
        log.info("Heard a voice rating from %s (%s)", draft["name"], "score found" if draft["score"] else "no score")

    async def _reply(self, message: discord.Message, text: str) -> None:
        try:
            await message.reply(text, mention_author=False)
        except discord.HTTPException:
            pass

    def card(self, draft: dict[str, Any], lib: dict[str, Any]) -> tuple[discord.Embed, discord.ui.View]:
        entry = next((e for e in lib["history"] if e["id"] == draft["hid"]), {"movie_id": "", "ratings": {}})
        movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
        score = draft.get("score")
        desc = (
            f"**Score:** {score:g}/10"
            if score is not None
            else "**Score:** I didn't catch one. Tap **Edit** to add it."
        )
        said = draft["comment"] if len(draft["comment"]) <= 1500 else draft["comment"][:1499] + "…"
        desc += f"\n**What you said:** {said}"
        embed = discord.Embed(title=f"🎙️ {draft['name']}'s rating for {movies.label(movie)}"[:256], description=desc)
        embed.color = GOLD
        if movie.get("poster"):
            embed.set_thumbnail(url=movie["poster"])
        q, asker = movies.question_for(entry, draft["uid"])
        if q and not (entry["ratings"].get(draft["uid"]) or {}).get("answered"):
            who = (entry["ratings"].get(asker or "") or {}).get("name") or "Someone"
            embed.add_field(name=f"{who} asked you", value=f"*{q[:900]}*\nTap **Edit** to answer it.", inline=False)
        embed.set_footer(text=f"Nothing's saved until {draft['name']} taps Save.")
        view = discord.ui.View(timeout=None)
        view.add_item(DraftButton(draft["id"], "save", disabled=score is None))
        view.add_item(DraftButton(draft["id"], "edit"))
        if len(open_entries(lib)) > 1:
            view.add_item(DraftButton(draft["id"], "other"))
        view.add_item(DraftButton(draft["id"], "cancel"))
        return embed, view

    async def press(self, interaction: discord.Interaction, did: str, op: str) -> None:
        draft = self._drafts().get(did)
        if draft is None:
            await interaction.response.edit_message(content="This one's done (or it expired).", embed=None, view=None)
            return
        if str(interaction.user.id) != draft["uid"]:
            await interaction.response.send_message(
                f"That's {draft['name']}'s rating. Send me your own voice message to rate by voice.", ephemeral=True
            )
            return
        if op == "cancel":
            self.forget(did)
            await interaction.response.edit_message(content="Cancelled. Nothing was saved.", embed=None, view=None)
            return
        lib = movies.load()
        entry = next((e for e in lib["history"] if e["id"] == draft["hid"]), None)
        if entry is None or (entry.get("prompt") or {}).get("closed"):
            self.forget(did)
            await interaction.response.edit_message(content="Ratings for that one have closed.", embed=None, view=None)
            return
        movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
        if op == "other":
            entries = open_entries(lib)
            ids = [e["id"] for e in entries]
            draft["hid"] = ids[(ids.index(draft["hid"]) + 1) % len(ids)] if draft["hid"] in ids else ids[-1]
            self._put(draft)
            embed, view = self.card(draft, lib)
            await interaction.response.edit_message(embed=embed, view=view)
            return
        if op == "edit":
            await interaction.response.send_modal(
                RateModal(
                    entry["id"],
                    movie,
                    entry,
                    draft["uid"],
                    draft={"score": draft.get("score"), "comment": draft["comment"]},
                    draft_id=did,
                    pick_person=interaction.guild is not None,
                )
            )
            return
        if draft.get("score") is None:
            await interaction.response.send_message("I didn't catch a score: tap **Edit** to add it.", ephemeral=True)
            return
        # Save: the score and words; anything else in their rating (an answer, their question) stays.
        old = dict(entry.get("ratings", {}).get(draft["uid"]) or {})
        movies.add_rating(
            entry,
            draft["uid"],
            name=draft["name"],
            user_id=draft["uid"],
            score=draft["score"],
            comment=draft["comment"],
            answered=old.get("answered", ""),
            answered_from=old.get("answered_from"),
            answer=old.get("answer", ""),
            ask=old.get("ask", ""),
            ask_for=old.get("ask_for"),
            ask_for_name=old.get("ask_for_name", ""),
        )
        if old:
            entry["ratings"][draft["uid"]]["at"] = old.get("at", time.time())
            if old.get("ask_msg"):
                entry["ratings"][draft["uid"]]["ask_msg"] = old["ask_msg"]
        movies.save(lib)
        self.forget(did)
        await interaction.response.edit_message(
            content=f"✅ {draft['name']} gave **{movies.label(movie)}** **{draft['score']:g}/10**. Thanks! ⭐",
            embed=None,
            view=None,
        )
        log.info("Saved a voice rating from %s", draft["name"])
        night = self.bot.get_cog("MovieNight")
        if night is not None:
            await night.after_rating(entry["id"], draft["uid"], old)


async def setup(bot: Pingwin) -> None:
    bot.add_dynamic_items(DraftButton)
    await bot.add_cog(VoiceRatings(bot))
