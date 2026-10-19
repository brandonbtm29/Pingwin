"""Welcome and approval: greet newcomers, and give mods Approve / Deny buttons."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from .. import store

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.welcome")
STATE = "approvals"


def fill(template: str, member: discord.abc.User) -> str:
    return template.replace("{mention}", member.mention).replace("{name}", member.display_name)


def approval_card(member: discord.abc.User, status: str = "") -> discord.Embed:
    embed = discord.Embed(
        title="New member waiting",
        color=discord.Color.orange()
        if not status
        else discord.Color.green()
        if status.startswith("Approved")
        else discord.Color.dark_grey(),
        description=f"{member.mention} · {member}",
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Account created", value=f"<t:{int(member.created_at.timestamp())}:R>")
    if isinstance(member, discord.Member) and member.joined_at:
        embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>")
    if status:
        embed.set_footer(text=status)
    return embed


class ApprovalButton(discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:ap:(?P<uid>[0-9]+):(?P<act>ok|no)"):
    def __init__(self, uid: int, act: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Approve" if act == "ok" else "Deny",
                style=discord.ButtonStyle.success if act == "ok" else discord.ButtonStyle.danger,
                custom_id=f"pingwin:ap:{uid}:{act}",
            )
        )
        self.uid, self.act = uid, act

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["uid"]), match["act"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: Pingwin = interaction.client  # type: ignore[assignment]
        cfg = bot.features.welcome
        if not bot.is_mod(interaction.user, cfg.mod_role):
            await interaction.response.send_message("Only mods can approve members.", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except discord.HTTPException:
            return
        guild = interaction.guild
        member = guild.get_member(self.uid)
        if member is None:
            try:
                member = await guild.fetch_member(self.uid)
            except discord.HTTPException:
                member = None
        if member is None:
            await interaction.message.edit(view=None)
            await interaction.followup.send("They've already left the server.", ephemeral=True)
            return
        state = store.load(STATE)
        if self.act == "ok":
            role = bot.role_named(guild, cfg.approve_role)
            if role is None:
                await interaction.followup.send(f"There's no role named {cfg.approve_role}.", ephemeral=True)
                return
            try:
                await member.add_roles(role, reason=f"Pingwin: approved by {interaction.user}")
            except discord.Forbidden:
                await interaction.followup.send(
                    f"I can't give {role.name}: drag my role above it in Server Settings → Roles.", ephemeral=True
                )
                return
            status = f"Approved by {interaction.user.display_name}"
            channel = bot.text_channel(cfg.approved_channel)
            if channel and cfg.approved_message:
                await channel.send(
                    fill(cfg.approved_message, member),
                    allowed_mentions=discord.AllowedMentions(users=[member], roles=False),
                )
        else:
            status = f"Denied by {interaction.user.display_name}"
            if cfg.deny_action == "kick":
                try:
                    await member.kick(reason=f"Pingwin: denied by {interaction.user}")
                    status += " (kicked)"
                except discord.Forbidden:
                    status += " (couldn't kick: needs Kick Members)"
        await interaction.message.edit(embed=approval_card(member, status), view=None)
        state.setdefault(str(self.uid), {})["status"] = status
        store.save(STATE, state)
        log.info("%s: %s", member, status)
        await interaction.followup.send(status + ".", ephemeral=True)


class Welcome(commands.Cog):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot

    @property
    def cfg(self):
        return self.bot.features.welcome

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not (self.cfg.enabled and self.bot.is_active) or member.bot or member.guild != self.bot.home_guild:
            return
        welcome = self.bot.text_channel(self.cfg.welcome_channel)
        if welcome and self.cfg.welcome_message:
            await welcome.send(
                fill(self.cfg.welcome_message, member),
                allowed_mentions=discord.AllowedMentions(users=[member], roles=False),
            )
        cards = self.bot.text_channel(self.cfg.approval_channel)
        if cards is None:
            log.warning("Welcome: no approval channel set, or Pingwin can't see it")
            return
        view = discord.ui.View(timeout=None)
        view.add_item(ApprovalButton(member.id, "ok"))
        view.add_item(ApprovalButton(member.id, "no"))
        msg = await cards.send(embed=approval_card(member), view=view)
        state = store.load(STATE)
        state[str(member.id)] = {"card": msg.id, "channel": cards.id, "status": "pending"}
        store.save(STATE, state)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not (self.cfg.enabled and self.bot.is_active):
            return
        state = store.load(STATE)
        rec = state.get(str(member.id))
        if not rec or rec.get("status") != "pending":
            return
        channel = self.bot.get_channel(rec["channel"])
        try:
            msg = await channel.fetch_message(rec["card"])
            await msg.edit(embed=approval_card(member, "Left before being approved"), view=None)
        except (discord.HTTPException, AttributeError):
            pass
        rec["status"] = "left"
        store.save(STATE, state)


async def setup(bot: Pingwin) -> None:
    bot.add_dynamic_items(ApprovalButton)
    await bot.add_cog(Welcome(bot))
