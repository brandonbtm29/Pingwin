"""Mod log: joins, leaves, bans, role changes and deleted (optionally edited) messages, in a private channel.

It stays quiet on purpose: Pingwin's own posts, other bots, mod-only channels and quick typo fixes aren't
logged, and edits are off unless a mod turns them on."""

from __future__ import annotations

import difflib
import logging
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.modlog")
MOD_PERMS = ("administrator", "manage_messages", "manage_guild", "kick_members", "ban_members")


def clip(text: str, n: int = 1000) -> str:
    text = text or "*(no text)*"
    return text if len(text) <= n else text[: n - 1] + "…"


def bare_name(name: str) -> str:
    """A channel name without emoji or punctuation around it: '🎵-music' and '#music' are both 'music'."""
    return re.sub(r"^[\W_]+|[\W_]+$", "", (name or "").casefold())


def skipped(name: str, skip_channels: str) -> bool:
    """Whether a channel is one mods asked to skip (whole names: 'bots' doesn't skip #robots)."""
    skip = {bare_name(s) for s in str(skip_channels or "").split(",")} - {""}
    return bare_name(name) in skip


def is_private(channel, guild) -> bool:
    """Whether regular members can't see the channel (a mod channel): neither @everyone nor any role without
    mod powers can. Bot and other managed roles don't count."""
    everyone = guild.default_role
    regular = [everyone] + [
        r
        for r in getattr(guild, "roles", ())
        if r != everyone and not r.managed and not any(getattr(r.permissions, p, False) for p in MOD_PERMS)
    ]
    return not any(channel.permissions_for(r).view_channel for r in regular)


class ModLog(commands.Cog):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot

    @property
    def cfg(self):
        return self.bot.features.modlog

    def _channel(self, guild: discord.Guild | None) -> discord.TextChannel | None:
        if not (self.cfg.enabled and self.bot.is_active) or guild is None or guild != self.bot.home_guild:
            return None
        return self.bot.text_channel(self.cfg.channel)

    async def _send(self, channel: discord.TextChannel, embed: discord.Embed) -> None:
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            log.warning("Couldn't write to the mod log: %s", e)

    # ------------------------------------------------------------ members
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if (ch := self._channel(member.guild)) and self.cfg.joins:
            e = discord.Embed(color=discord.Color.green(), description=f"📥 {member.mention} joined · {member}")
            e.add_field(name="Account created", value=f"<t:{int(member.created_at.timestamp())}:R>")
            e.set_thumbnail(url=member.display_avatar.url)
            await self._send(ch, e)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if (ch := self._channel(member.guild)) and self.cfg.leaves:
            roles = ", ".join(r.name for r in member.roles if not r.is_default()) or "none"
            e = discord.Embed(color=discord.Color.dark_grey(), description=f"📤 {member.mention} left · {member}")
            e.add_field(name="Roles they had", value=clip(roles, 1000))
            await self._send(ch, e)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        if (ch := self._channel(guild)) and self.cfg.leaves:
            await self._send(
                ch, discord.Embed(color=discord.Color.red(), description=f"🔨 {user.mention} was banned · {user}")
            )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        if (ch := self._channel(guild)) and self.cfg.leaves:
            await self._send(
                ch, discord.Embed(color=discord.Color.blue(), description=f"🕊️ {user.mention} was unbanned · {user}")
            )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not ((ch := self._channel(after.guild)) and self.cfg.roles) or before.roles == after.roles:
            return
        menu = self.bot.config.roles
        skip = (lambda r: menu.is_menu_role(r.name)) if self.cfg.ignore_menu_roles else (lambda r: False)
        added = [r for r in after.roles if r not in before.roles and not skip(r)]
        removed = [r for r in before.roles if r not in after.roles and not skip(r)]
        if not (added or removed):
            return
        lines = [f"➕ {r.mention}" for r in added] + [f"➖ {r.mention}" for r in removed]
        e = discord.Embed(
            color=discord.Color.blurple(), description=f"🏷️ Roles changed for {after.mention}\n" + "\n".join(lines)
        )
        await self._send(ch, e)

    # ------------------------------------------------------------ messages
    def _skip_message(self, message: discord.Message, ch: discord.TextChannel) -> bool:
        """Pingwin's own and other bots' posts, the log channel, channels mods asked to skip, and channels
        regular members can't see (mod channels: mods talking among themselves isn't what the log is for)."""
        # Without the Message Content intent every message looks empty, so there's nothing useful to log.
        if message.author.bot or message.channel.id == ch.id or not self.bot.intents.message_content:
            return True
        if skipped(getattr(message.channel, "name", "") or "", getattr(self.cfg, "skip_channels", "")):
            return True
        try:
            if message.guild is not None and is_private(message.channel, message.guild):
                return True  # a mod channel
        except (AttributeError, TypeError):
            pass
        return False

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not ((ch := self._channel(message.guild)) and self.cfg.messages) or self._skip_message(message, ch):
            return
        if not (message.content or message.attachments):
            return  # a sticker or an embed only: nothing to show
        e = discord.Embed(
            color=discord.Color.orange(),
            description=f"🗑️ Message by {message.author.mention} deleted in {message.channel.mention}",
        )
        e.add_field(name="Message", value=clip(message.content), inline=False)
        if message.attachments:
            e.add_field(name="Attachments", value=clip(", ".join(a.filename for a in message.attachments), 500))
        e.add_field(name="Posted", value=f"<t:{int(message.created_at.timestamp())}:R>")
        await self._send(ch, e)

    # Deletes of messages Pingwin never saw (older ones, or from before a restart) aren't logged: there's no
    # text or author to show, and most of them are Pingwin tidying up its own posts.

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not getattr(self.cfg, "edits", False):
            return
        if not ((ch := self._channel(after.guild)) and self.cfg.enabled) or self._skip_message(after, ch):
            return
        if minor_edit(before.content, after.content, before.created_at, discord.utils.utcnow()):
            return
        e = discord.Embed(
            color=discord.Color.gold(),
            description=(
                f"✏️ {after.author.mention} edited a message in {after.channel.mention} · [jump]({after.jump_url})"
            ),
        )
        e.add_field(name="Before", value=clip(before.content), inline=False)
        e.add_field(name="After", value=clip(after.content), inline=False)
        await self._send(ch, e)


def minor_edit(before: str, after: str, posted, now) -> bool:
    """Edits not worth logging: nothing changed (link previews and pins count as edits), a fix within 10
    minutes of posting, or only a few characters changed."""
    before, after = before or "", after or ""
    if before == after:
        return True
    if posted is not None and (now - posted).total_seconds() < 600:
        return True
    changed = sum(
        max(i2 - i1, j2 - j1)
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after).get_opcodes()
        if op != "equal"
    )
    return changed <= max(8, len(before) // 10)


async def setup(bot: Pingwin) -> None:
    await bot.add_cog(ModLog(bot))
