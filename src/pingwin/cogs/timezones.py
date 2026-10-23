"""/pingwin timezone: members pick their time zone, so the times they type mean their local time."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import zones

if TYPE_CHECKING:
    from ..bot import Pingwin


class TimeZones(commands.GroupCog, group_name="timezone", group_description="Your time zone"):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot

    async def _sync(self, interaction: discord.Interaction, zone_key: str | None) -> None:
        """Keep the time zone menu's role in step (it's quick, but never let it block the reply)."""
        from .roles import sync_zone_roles

        if isinstance(interaction.user, discord.Member):
            self.bot.loop.create_task(sync_zone_roles(self.bot, interaction.user, zone_key))

    async def zone_autocomplete(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=name[:100], value=value) for name, value in zones.search(current)]

    @app_commands.command(name="set", description="Set your time zone (for times you type and your birthday)")
    @app_commands.describe(zone="Pick one, or type a city zone like America/Denver")
    @app_commands.autocomplete(zone=zone_autocomplete)
    async def set_zone(self, interaction: discord.Interaction, zone: str) -> None:
        tz = zones.zone(zone)
        if tz is None:
            await interaction.response.send_message(
                f"I don't know the time zone **{zone}**. Pick one from the list.", ephemeral=True
            )
            return
        zones.set_for(interaction.user.id, tz.key)
        await self._sync(interaction, tz.key)
        now = datetime.now(tz)
        await interaction.response.send_message(
            f"Saved: **{zones.label(tz)}** (it's {_clock(now)} there). Times you type, like "
            "`/pingwin gameday create when:fri 8pm`, now mean your time.",
            ephemeral=True,
        )

    @app_commands.command(name="show", description="Show your time zone")
    async def show(self, interaction: discord.Interaction) -> None:
        mine = zones.get(interaction.user.id)
        server = self.bot.features.tz
        if mine is None:
            text = (
                f"You haven't picked one, so I use the server's: **{zones.label(server)}** "
                f"({_clock(datetime.now(server))}). Set yours with `/pingwin timezone set`."
            )
        else:
            text = f"Yours is **{zones.label(mine)}** ({_clock(datetime.now(mine))})."
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="remove", description="Forget your time zone (use the server's)")
    async def remove(self, interaction: discord.Interaction) -> None:
        zones.set_for(interaction.user.id, None)
        await self._sync(interaction, None)
        await interaction.response.send_message("Done. I'll use the server's time zone for you.", ephemeral=True)


def _clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


async def setup(bot: Pingwin) -> None:
    await bot.add_cog(TimeZones(bot))
