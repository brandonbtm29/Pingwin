"""Game day sign-ups: a "Who's in?" card with buttons, then reminders to everyone who's in."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import store, zones
from ..timeparse import parse_when

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.gameday")
STATE = "gameday"
CHOICES = {
    "in": ("I'm in", discord.ButtonStyle.success, "✅"),
    "maybe": ("Maybe", discord.ButtonStyle.secondary, "🤔"),
    "out": ("Can't", discord.ButtonStyle.secondary, "❌"),
}


def toggle(game: dict, user_id: int, choice: str) -> str:
    """Move a user to a choice (or off it, if they click the same one again). Returns their new choice."""
    was = next((c for c in CHOICES if user_id in game[c]), None)
    for c in CHOICES:
        if user_id in game[c]:
            game[c].remove(user_id)
    if was == choice:
        return ""
    game[choice].append(user_id)
    return choice


def card(game: dict) -> discord.Embed:
    when = int(datetime.fromisoformat(game["when"]).timestamp())
    embed = discord.Embed(
        title=f"🎮 {game['title']}",
        color=discord.Color.blurple(),
        description=(game.get("note") or "") + f"\n\n**When:** <t:{when}:F> (<t:{when}:R>)",
    )
    for c, (label, _, emoji) in CHOICES.items():
        people = game[c]
        embed.add_field(
            name=f"{emoji} {label} ({len(people)})",
            value=" ".join(f"<@{u}>" for u in people)[:1024] or "–",
            inline=True,
        )
    embed.set_footer(text=f"Hosted by {game['host_name']}" + (" · CANCELLED" if game.get("cancelled") else ""))
    return embed


class GameButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:gd:(?P<gid>[0-9a-f]+):(?P<choice>in|maybe|out)"
):
    def __init__(self, gid: str, choice: str) -> None:
        label, style, emoji = CHOICES[choice]
        super().__init__(
            discord.ui.Button(label=label, style=style, emoji=emoji, custom_id=f"pingwin:gd:{gid}:{choice}")
        )
        self.gid, self.choice = gid, choice

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["gid"], match["choice"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            return
        state = store.load(STATE)
        game = state.get(self.gid)
        if game is None or game.get("cancelled"):
            await interaction.followup.send("This game day is over or was cancelled.", ephemeral=True)
            return
        toggle(game, interaction.user.id, self.choice)
        store.save(STATE, state)
        await interaction.message.edit(embed=card(game), allowed_mentions=discord.AllowedMentions.none())


def buttons(gid: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for c in CHOICES:
        view.add_item(GameButton(gid, c))
    return view


class GameDay(commands.GroupCog, group_name="gameday", group_description="Game day sign-ups"):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.check.start()

    def cog_unload(self) -> None:
        self.check.cancel()

    @property
    def cfg(self):
        return self.bot.features.gameday

    @app_commands.command(name="create", description="Post a 'Who's in?' sign-up")
    @app_commands.describe(
        title="What you're playing",
        when="In your time zone, e.g. 'fri 8pm', 'tomorrow 7:30pm', 'in 2 hours', or add one: 'sat 7pm CT'",
        note="Anything else people should know",
    )
    async def create(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 1, 100],
        when: str,
        note: app_commands.Range[str, 1, 500] | None = None,
    ) -> None:
        if not self.cfg.enabled:
            await interaction.response.send_message("Game day sign-ups aren't turned on.", ephemeral=True)
            return
        text, typed_zone = zones.split_zone(when)
        tz = typed_zone or zones.for_member(interaction.user.id, self.bot.features.tz)
        now = datetime.now(tz)
        start = parse_when(text, now)
        if start is None or start <= now:
            await interaction.response.send_message(
                f"I couldn't understand **{when}** as a future time. Try `fri 8pm`, `tomorrow 7:30pm`, "
                "`10/24 8pm`, `in 2 hours`, or add a zone like `sat 7pm CT`.",
                ephemeral=True,
            )
            return
        channel = (
            interaction.channel
            if isinstance(interaction.channel, discord.TextChannel)
            else self.bot.text_channel(self.cfg.channel)
        )
        if channel is None:
            await interaction.response.send_message("Run this in a text channel.", ephemeral=True)
            return
        gid = secrets.token_hex(4)
        game = {
            "title": title,
            "note": note or "",
            "when": start.isoformat(),
            "host": interaction.user.id,
            "host_name": interaction.user.display_name,
            "channel": channel.id,
            "message": None,
            "in": [interaction.user.id],
            "maybe": [],
            "out": [],
            "reminded": False,
            "started": False,
        }
        role = self.bot.role_named(channel.guild, self.cfg.role)
        await interaction.response.defer(ephemeral=True)
        msg = await channel.send(
            content=role.mention if role else None,
            embed=card(game),
            view=buttons(gid),
            allowed_mentions=discord.AllowedMentions(roles=[role] if role else False),
        )
        game["message"] = msg.id
        state = store.load(STATE)
        state[gid] = game
        store.save(STATE, state)
        note_zone = ""
        if typed_zone is None and zones.get(interaction.user.id) is None:
            note_zone = " Set yours with `/timezone set` if that's not your time zone."
        await interaction.followup.send(
            f"Posted: {msg.jump_url}\nI read **{when}** as {start.strftime('%a %b %d, %I:%M %p').replace(' 0', ' ')} "
            f"{zones.label(tz)}. Everyone sees it in their own time.{note_zone}",
            ephemeral=True,
        )

    async def game_autocomplete(self, interaction: discord.Interaction, current: str):
        state = store.load(STATE)
        return [
            app_commands.Choice(name=g["title"][:100], value=gid)
            for gid, g in state.items()
            if not g.get("cancelled") and not g.get("started") and current.casefold() in g["title"].casefold()
        ][:25]

    @app_commands.command(name="cancel", description="Cancel a game day (host or mods)")
    @app_commands.autocomplete(game=game_autocomplete)
    async def cancel(self, interaction: discord.Interaction, game: str) -> None:
        state = store.load(STATE)
        g = state.get(game)
        if g is None:
            await interaction.response.send_message("I can't find that game day.", ephemeral=True)
            return
        if g["host"] != interaction.user.id and not self.bot.is_mod(interaction.user):
            await interaction.response.send_message("Only the host or a mod can cancel it.", ephemeral=True)
            return
        g["cancelled"] = True
        store.save(STATE, state)
        await interaction.response.send_message("Cancelled.", ephemeral=True)
        channel = self.bot.get_channel(g["channel"])
        try:
            msg = await channel.fetch_message(g["message"])
            await msg.edit(embed=card(g), view=None)
            if g["in"]:
                await msg.reply(
                    f"❌ **{g['title']}** is cancelled. " + " ".join(f"<@{u}>" for u in g["in"]),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
        except (discord.HTTPException, AttributeError):
            pass

    @tasks.loop(minutes=1)
    async def check(self) -> None:
        if not (self.cfg.enabled and self.bot.is_active):
            return
        state = store.load(STATE)
        now = discord.utils.utcnow()
        changed = False
        for gid, g in list(state.items()):
            start = datetime.fromisoformat(g["when"])
            if now - start > timedelta(days=2):
                state.pop(gid)
                changed = True
                continue
            if g.get("cancelled"):
                continue
            kind = None
            if (
                not g["reminded"]
                and self.cfg.remind_minutes
                and timedelta(0) < start - now <= timedelta(minutes=self.cfg.remind_minutes)
            ):
                kind, text = "reminded", f"⏰ **{g['title']}** starts <t:{int(start.timestamp())}:R>!"
            elif not g["started"] and timedelta(0) <= now - start < timedelta(minutes=30):
                kind, text = "started", f"🎮 **{g['title']}** is starting now!"
            if kind is None:
                continue
            g[kind] = True
            g["reminded"] = True
            changed = True
            if not g["in"]:
                continue
            channel = self.bot.get_channel(g["channel"])
            try:
                msg = await channel.fetch_message(g["message"])
                await msg.reply(
                    text + " " + " ".join(f"<@{u}>" for u in g["in"]),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
            except (discord.HTTPException, AttributeError) as e:
                log.warning("Couldn't post the game day reminder: %s", e)
        if changed:
            store.save(STATE, state)

    @check.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: Pingwin) -> None:
    bot.add_dynamic_items(GameButton)
    await bot.add_cog(GameDay(bot))
