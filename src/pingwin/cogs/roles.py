"""Opt-in ping roles: a button menu members tap to add or remove roles.

Buttons carry the role ID in their custom_id ("pingwin:role:<id>"), and are
registered as dynamic items, so menus keep working after the bot restarts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .. import menus
from ..config import MenuRole, RolesConfig

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.roles")

MENU_COLOR = discord.Color.from_rgb(88, 166, 255)  # icy blue


class RoleToggleButton(discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:role:(?P<role_id>[0-9]+)"):
    def __init__(self, role_id: int, *, label: str | None = None, emoji: str | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
                custom_id=f"pingwin:role:{role_id}",
            )
        )
        self.role_id = role_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match) -> RoleToggleButton:
        return cls(int(match["role_id"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Standby PCs stay silent; the active PC answers.
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: Pingwin = interaction.client  # type: ignore[assignment]
        persona = bot.persona
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return

        # Acknowledge first. If two PCs ever overlap, only one can acknowledge,
        # so the role is never toggled twice.
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except discord.HTTPException:
            return
        cfg = bot.config.roles
        role = guild.get_role(self.role_id)

        if role is None or not cfg.is_menu_role(role.name):
            # Deleted, renamed, or no longer in config.toml: never hand out roles outside the menu.
            msg = persona.say("role_missing")
        elif cfg.required_role and not has_role_named(member, cfg.required_role):
            msg = persona.say("not_allowed", required=cfg.required_role, user=member.display_name)
        else:
            try:
                if role in member.roles:
                    await member.remove_roles(role, reason="Pingwin role menu: opted out")
                    msg = persona.say("role_removed", role=role.name, user=member.display_name)
                    log.info("%s opted out of %s", member, role.name)
                else:
                    await member.add_roles(role, reason="Pingwin role menu: opted in")
                    msg = persona.say("role_added", role=role.name, user=member.display_name)
                    log.info("%s opted into %s", member, role.name)
            except discord.Forbidden:
                log.warning("Forbidden toggling %s for %s: check role order and Manage Roles", role.name, member)
                msg = persona.say("no_permission", role=role.name)

        await interaction.followup.send(msg, ephemeral=True)


def has_role_named(member: discord.Member, name: str) -> bool:
    key = name.casefold()
    return any(r.name.casefold() == key for r in member.roles)


def resolve_menu(guild: discord.Guild, cfg: RolesConfig) -> list[tuple[MenuRole, discord.Role | None]]:
    by_name = {r.name.casefold(): r for r in guild.roles}
    return [(m, by_name.get(m.name.casefold())) for m in cfg.menu]


def role_problems(guild: discord.Guild, role: discord.Role) -> list[str]:
    issues = []
    if role.managed:
        issues.append("is managed by an integration and can't be handed out")
    if role >= guild.me.top_role:
        issues.append("is above (or equal to) my role; drag my role higher in Server Settings → Roles")
    if not role.mentionable:
        issues.append('isn\'t pingable; turn on "Allow anyone to @mention this role"')
    if role.permissions.value:
        issues.append("has permissions; ping roles should usually have none")
    return issues


def build_menu_embed(cfg: RolesConfig, footer: str) -> discord.Embed:
    lines = [cfg.menu_description] if cfg.menu_description else []
    if lines:
        lines.append("")
    for m in cfg.menu:
        prefix = f"{m.emoji} " if m.emoji else ""
        suffix = f": {m.description}" if m.description else ""
        lines.append(f"{prefix}**{m.name}**{suffix}")
    embed = discord.Embed(title=cfg.menu_title, description="\n".join(lines), color=MENU_COLOR)
    embed.set_footer(text=footer)
    return embed


class MenuError(Exception):
    """A problem posting or refreshing a menu, worded for the person who asked."""


def menu_view(resolved: list[tuple[MenuRole, discord.Role | None]]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for m, role in resolved:
        assert role is not None
        view.add_item(RoleToggleButton(role.id, label=m.name, emoji=m.emoji))
    return view


def _ready_menu(bot: Pingwin, guild: discord.Guild) -> list[tuple[MenuRole, discord.Role | None]]:
    resolved = resolve_menu(guild, bot.config.roles)
    if not resolved:
        raise MenuError("The menu is empty. Add some roles first.")
    missing = [m.name for m, r in resolved if r is None]
    if missing:
        raise MenuError(
            "These roles don't exist yet: " + ", ".join(f"**{n}**" for n in missing) + ". Create them first."
        )
    return resolved


async def role_report(guild: discord.Guild, cfg: RolesConfig, *, create_missing: bool, actor: str) -> list[str]:
    """Check (and optionally create) the menu roles. Returns one line per finding."""
    lines: list[str] = []
    if not guild.me.guild_permissions.manage_roles:
        lines.append("❌ I don't have **Manage Roles**, so I can't hand out any roles yet.")

    for m, role in resolve_menu(guild, cfg):
        if role is None:
            if not create_missing:
                lines.append(f"❓ **{m.name}** doesn't exist yet")
                continue
            try:
                await guild.create_role(
                    name=m.name,
                    permissions=discord.Permissions.none(),
                    mentionable=True,
                    reason=f"Pingwin role setup by {actor}",
                )
                lines.append(f"✅ Created **{m.name}** (pingable, no permissions)")
            except discord.HTTPException as e:
                lines.append(f"❌ Couldn't create **{m.name}**: {e.text or e}")
            continue
        issues = role_problems(guild, role)
        lines.append(f"⚠️ **{role.name}** " + "; ".join(issues) if issues else f"👍 **{role.name}** looks good")

    if cfg.required_role:
        gate = next((r for r in guild.roles if r.name.casefold() == cfg.required_role.casefold()), None)
        if gate is None:
            lines.append(f"⚠️ The gate role **{cfg.required_role}** doesn't exist, so nobody can use the menu.")
        else:
            lines.append(f"🔒 Only members with **{gate.name}** can use the menu.")
    else:
        lines.append("🔓 No gate role set: anyone in the server can use the menu.")
    if not cfg.menu:
        lines.append("The menu is empty.")
    return lines


async def post_menu(bot: Pingwin, channel: discord.TextChannel | discord.Thread, *, actor: str) -> discord.Message:
    resolved = _ready_menu(bot, channel.guild)
    embed = build_menu_embed(bot.config.roles, bot.persona.say("menu_footer"))
    try:
        message = await channel.send(embed=embed, view=menu_view(resolved))
    except discord.Forbidden as e:
        raise MenuError(
            f"I can't post in #{channel.name}. I need View Channel, Send Messages and Embed Links there."
        ) from e
    except discord.HTTPException as e:
        raise MenuError(f"Discord rejected the menu (often a bad custom emoji): {e.text or e}") from e
    menus.remember(menus.PostedMenu(channel.guild.id, channel.id, message.id))
    log.info("Posted role menu in #%s (%s) by %s", channel.name, channel.guild.name, actor)
    return message


async def refresh_menus(bot: Pingwin) -> list[str]:
    """Update every posted menu in place with the current roles and wording."""
    posted = menus.load_menus()
    if not posted:
        return ["No posted menus on record yet. Post one first."]
    lines: list[str] = []
    gone: set[int] = set()
    for pm in posted:
        guild = bot.get_guild(pm.guild_id)
        channel = guild.get_channel_or_thread(pm.channel_id) if guild else None
        if guild is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            lines.append(f"❓ Can't see the channel for {pm.url} anymore")
            continue
        try:
            resolved = _ready_menu(bot, guild)
            message = channel.get_partial_message(pm.message_id)
            await message.edit(
                embed=build_menu_embed(bot.config.roles, bot.persona.say("menu_footer")), view=menu_view(resolved)
            )
            lines.append(f"✅ Updated the menu in #{channel.name}")
        except discord.NotFound:
            gone.add(pm.message_id)
            lines.append(f"🗑️ The menu in #{channel.name} was deleted; forgot it")
        except MenuError as e:
            lines.append(f"❌ #{channel.name}: {e}")
            break
        except discord.HTTPException as e:
            lines.append(f"❌ #{channel.name}: {e.text or e}")
    menus.forget(gone)
    return lines


@app_commands.guild_only()
@app_commands.default_permissions(manage_roles=True)
class RoleMenu(commands.GroupCog, group_name="rolemenu", group_description="Set up the opt-in ping role menu"):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot

    async def _report(self, interaction: discord.Interaction, *, create_missing: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        assert interaction.guild is not None
        lines = await role_report(
            interaction.guild, self.bot.config.roles, create_missing=create_missing, actor=str(interaction.user)
        )
        await interaction.followup.send("\n".join(lines)[:2000], ephemeral=True)

    @app_commands.command(name="check", description="Check the menu roles without changing anything")
    async def check(self, interaction: discord.Interaction) -> None:
        await self._report(interaction, create_missing=False)

    @app_commands.command(name="setup", description="Create any missing menu roles (pingable, no permissions)")
    async def setup_roles(self, interaction: discord.Interaction) -> None:
        await self._report(interaction, create_missing=True)

    @app_commands.command(name="post", description="Post the role menu")
    @app_commands.describe(channel="Where to post it (defaults to this channel)")
    async def post(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        target = channel or interaction.channel
        if not isinstance(target, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send("Pick a text channel to post in.", ephemeral=True)
            return
        try:
            await post_menu(self.bot, target, actor=str(interaction.user))
        except MenuError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        await interaction.followup.send(
            f"Menu posted in {target.mention}. Use `/rolemenu refresh` after changing the roles.", ephemeral=True
        )

    @app_commands.command(name="refresh", description="Update posted menus with the current roles and wording")
    async def refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        lines = await refresh_menus(self.bot)
        await interaction.followup.send("\n".join(lines)[:2000], ephemeral=True)


async def setup(bot: Pingwin) -> None:
    bot.add_dynamic_items(RoleToggleButton)
    await bot.add_cog(RoleMenu(bot))
