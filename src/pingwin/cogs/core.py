"""/pingwin about, status, reload and restart."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import __version__
from ..config import ConfigError

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.core")


class Core(commands.GroupCog, group_name="pingwin", group_description="About Pingwin"):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot

    @app_commands.command(name="about", description="Who is Pingwin?")
    async def about(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(f"{self.bot.persona.say('about')}\n-# v{__version__}", ephemeral=True)

    @app_commands.command(name="status", description="Which PC is Pingwin running on?")
    async def status(self, interaction: discord.Interaction) -> None:
        bot = self.bot
        failover = bot.config.failover
        lines = [
            f"Answering from **{bot.node.name}**"
            + (f" ({failover.describe(bot.node.name)})" if failover.active else ""),
            f"Version {__version__} · running since <t:{int(bot.started_at.timestamp())}:R>",
            "Failover: " + ("on, backup PCs can take over" if failover.active else "off (single PC)"),
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="dashboard", description="Get the link to Pingwin's dashboard (admins)")
    async def dashboard(self, interaction: discord.Interaction) -> None:
        from .. import store

        bot = self.bot
        member = interaction.user
        role = bot.config.dashboard.admin_role
        allowed = isinstance(member, discord.Member) and (
            member.guild_permissions.manage_guild
            or member.guild_permissions.administrator
            or (role and any(r.name.casefold() == role.casefold() for r in member.roles))
        )
        if not allowed:
            need = "Manage Server" + (f" or the {role} role" if role else "")
            await interaction.response.send_message(f"The dashboard is for members with {need}.", ephemeral=True)
            return
        links = store.load("dashboards")
        if not links:
            await interaction.response.send_message(
                "The dashboard isn't online yet. On the PC running Pingwin, open the dashboard and turn it on "
                "under **Status → Remote access**.",
                ephemeral=True,
            )
            return
        order = list(bot.config.failover.pcs)
        rank = {pc.casefold(): i for i, pc in enumerate(order)}
        lines = [
            f"**{pc}**{' (answering now)' if pc.casefold() == bot.node.name.casefold() else ''}: {info['url']}"
            for pc, info in sorted(links.items(), key=lambda kv: rank.get(kv[0].casefold(), 99))
        ]
        await interaction.response.send_message(
            "🐧 **Pingwin dashboard**\n" + "\n".join(lines) + "\n-# Sign in with Discord. If a link doesn't open, "
            "that PC is off: try the next one.",
            ephemeral=True,
            suppress_embeds=True,
        )

    @app_commands.command(name="reload", description="Reload settings and personality now (mods only)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reload(self, interaction: discord.Interaction) -> None:
        try:
            self.bot.reload_settings()
        except ConfigError as e:
            await interaction.response.send_message(f"Couldn't reload: {e}", ephemeral=True)
            return
        log.info("Reloaded settings (requested by %s)", interaction.user)
        await interaction.response.send_message(self.bot.persona.say("reloaded"), ephemeral=True)

    @app_commands.command(name="restart", description="Restart Pingwin and install any update from GitHub (mods only)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def restart(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Restarting… back in about 30 seconds. 🐧", ephemeral=True)
        log.info("Restart requested by %s", interaction.user)
        await self.bot.restart()


async def setup(bot: Pingwin) -> None:
    await bot.add_cog(Core(bot))
