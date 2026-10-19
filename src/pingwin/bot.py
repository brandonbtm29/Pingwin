"""The Pingwin bot: loads features, syncs slash commands, and runs housekeeping
(failover check-ins, settings auto-reload, update checks)."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from . import updater
from .config import PROJECT_ROOT, Config, config_path, load_config, load_node
from .dashboard.server import Dashboard
from .failover import Coordinator
from .persona import Persona, load_persona, persona_path

log = logging.getLogger("pingwin")

EXTENSIONS = (
    "pingwin.cogs.core",
    "pingwin.cogs.roles",
)

EXIT_RESTART = 3  # scripts/run_pingwin.cmd pulls updates and restarts right away

# Discord errors meaning "another Pingwin already answered" or "too late to answer".
ALREADY_HANDLED = {40060, 10062}


class PingwinTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Standby PCs stay silent; the active PC answers.
        return getattr(interaction.client, "is_active", True)


class Pingwin(commands.Bot):
    def __init__(self, config: Config) -> None:
        # Default intents are enough: no message content or member list needed.
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            help_command=None,
            tree_cls=PingwinTree,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True),
        )
        self.config: Config = config
        self.persona: Persona = load_persona(config.persona)
        self.node = load_node()
        self.coordinator = Coordinator(self)
        self.gateway_connected = False
        self.started_at: datetime = discord.utils.utcnow()
        self.exit_code = 0
        self.update_status: dict | None = None  # last update check, shown on the dashboard
        self.dashboard = Dashboard(self)
        self._watched: dict[Path, float] = {}
        self._housekeeping_task: asyncio.Task | None = None
        self._remember_settings_files()

    # ----- state ---------------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self.coordinator.active

    # ----- startup -------------------------------------------------------
    async def setup_hook(self) -> None:
        self.tree.on_error = self.on_app_command_error
        for ext in EXTENSIONS:
            await self.load_extension(ext)

        if self.config.guild_id:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d slash command(s) to server %s", len(synced), self.config.guild_id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global slash command(s) (can take up to an hour to appear)", len(synced))
        failover = self.config.failover
        log.info("This PC is %r; settings from %s", self.node.name, config_path())
        if failover.active and failover.rank(self.node.name) > len(failover.pcs):
            log.warning(
                "%r isn't in [failover] pcs %s, so it only steps in when every listed PC is offline",
                self.node.name,
                list(failover.pcs),
            )

        await self.dashboard.start()

    async def close(self) -> None:
        await self.dashboard.stop()
        await super().close()

    async def on_ready(self) -> None:
        self.gateway_connected = True
        log.info("Logged in as %s in %d server(s)", self.user, len(self.guilds))
        if self._housekeeping_task is None or self._housekeeping_task.done():
            self._housekeeping_task = asyncio.create_task(self._housekeeping(), name="pingwin-housekeeping")
        if self.is_active:
            await self.apply_nickname()

    async def on_resumed(self) -> None:
        self.gateway_connected = True

    async def on_disconnect(self) -> None:
        self.gateway_connected = False

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self.is_active:
            await self.apply_nickname(guild)

    async def on_pingwin_active(self) -> None:
        await self.apply_nickname()

    # ----- housekeeping loop ---------------------------------------------
    async def _housekeeping(self) -> None:
        next_update_check = time.monotonic() + 120
        while not self.is_closed():
            self._auto_reload()
            try:
                await self.coordinator.tick()
            except Exception:
                log.exception("Failover check-in failed")
                await self.coordinator.after_error()

            minutes = self.config.update_check_minutes
            if minutes and time.monotonic() >= next_update_check:
                next_update_check = time.monotonic() + minutes * 60
                if await updater.update_available(PROJECT_ROOT):
                    log.info("Restarting to install the update")
                    await self.restart()
                    return

            await asyncio.sleep(self.config.failover.heartbeat_seconds + random.uniform(0, 3))

    async def restart(self) -> None:
        self.exit_code = EXIT_RESTART
        await self.close()

    # ----- settings ------------------------------------------------------
    def _settings_files(self) -> list[Path]:
        files = [config_path()]
        try:
            files.append(persona_path(self.config.persona))
        except Exception:
            pass
        return files

    def _remember_settings_files(self) -> None:
        self._watched = {p: p.stat().st_mtime for p in self._settings_files() if p.exists()}

    def _auto_reload(self) -> None:
        """Pick up edits to config.toml / the persona (e.g. synced by SyncThing) without a restart."""
        current = {p: p.stat().st_mtime for p in self._settings_files() if p.exists()}
        if current == self._watched:
            return
        try:
            self.reload_settings()
            log.info("Settings changed on disk; reloaded")
        except Exception as e:
            log.error("Settings changed but couldn't be loaded, keeping the old ones: %s", e)
            self._watched = current  # don't retry until the file changes again

    def reload_settings(self) -> None:
        """Reload config.toml and the persona. Raises ConfigError on bad files."""
        config = load_config(require_token=False)
        persona = load_persona(config.persona)
        self.config = replace(config, token=self.config.token)
        self.persona = persona
        self._remember_settings_files()
        if self.is_active:
            asyncio.get_running_loop().create_task(self.apply_nickname())

    # ----- helpers -------------------------------------------------------
    async def apply_nickname(self, guild: discord.Guild | None = None) -> None:
        nick = self.persona.nickname
        if not nick:
            return
        for g in [guild] if guild else self.guilds:
            if g.me.nick == nick:
                continue
            try:
                await g.me.edit(nick=nick)
                log.info("Set nickname to %r in %s", nick, g.name)
            except discord.Forbidden:
                log.warning("No permission to change my nickname in %s (needs Change Nickname)", g.name)
            except discord.HTTPException as e:
                log.warning("Couldn't set nickname in %s: %s", g.name, e)

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, discord.HTTPException) and original.code in ALREADY_HANDLED:
            return
        if isinstance(error, app_commands.MissingPermissions):
            msg = (
                "You need the "
                + ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
                + " permission for that."
            )
        elif isinstance(error, app_commands.CheckFailure):
            msg = "You can't use that command here."
        else:
            log.exception("Slash command failed", exc_info=error)
            msg = "Something went wrong on my end. A mod can check logs/pingwin.log."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass
