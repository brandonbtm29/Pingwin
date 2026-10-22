"""Mod log: joins, leaves, bans, role changes and edited or deleted messages, in a private channel."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.modlog")


def clip(text: str, n: int = 1000) -> str:
    text = text or "*(no text)*"
    return text if len(text) <= n else text[: n - 1] + "…"


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
        # Without the Message Content intent every message looks empty, so there's nothing useful to log.
        return message.author.bot or message.channel.id == ch.id or not self.bot.intents.message_content

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not ((ch := self._channel(message.guild)) and self.cfg.messages) or self._skip_message(message, ch):
            return
        e = discord.Embed(
            color=discord.Color.orange(),
            description=f"🗑️ Message by {message.author.mention} deleted in {message.channel.mention}",
        )
        e.add_field(name="Message", value=clip(message.content), inline=False)
        if message.attachments:
            e.add_field(name="Attachments", value=clip(", ".join(a.filename for a in message.attachments), 500))
        await self._send(ch, e)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.cached_message is not None:
            return  # handled above
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not ((ch := self._channel(guild)) and self.cfg.messages) or payload.channel_id == ch.id:
            return
        if not self.bot.intents.message_content:
            return
        await self._send(
            ch,
            discord.Embed(
                color=discord.Color.orange(),
                description=(
                    f"🗑️ An older message was deleted in <#{payload.channel_id}> (Pingwin hadn't seen it, so the text "
                    "isn't known)"
                ),
            ),
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not ((ch := self._channel(after.guild)) and self.cfg.messages) or self._skip_message(after, ch):
            return
        if before.content == after.content:
            return  # link previews and pins also count as edits
        e = discord.Embed(
            color=discord.Color.gold(),
            description=(
                f"✏️ {after.author.mention} edited a message in {after.channel.mention} · [jump]({after.jump_url})"
            ),
        )
        e.add_field(name="Before", value=clip(before.content), inline=False)
        e.add_field(name="After", value=clip(after.content), inline=False)
        await self._send(ch, e)


async def setup(bot: Pingwin) -> None:
    await bot.add_cog(ModLog(bot))
