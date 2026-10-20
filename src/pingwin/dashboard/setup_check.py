"""The dashboard's setup checklist: what's done, what's left, and where to fix it."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import invite_url, menus, updater
from ..config import PROJECT_ROOT

if TYPE_CHECKING:
    import discord

    from ..bot import Pingwin

DONE, TODO, WARN, OPTIONAL = "done", "todo", "warn", "optional"


@dataclass
class Facts:
    connected: bool
    guild_name: str | None
    invite: str | None
    guild_id_set: bool
    can_manage_roles: bool
    menu_count: int
    missing_roles: list[str]
    problem_roles: list[str]
    gate_role: str | None
    gate_exists: bool
    menus_posted: int
    failover_enabled: bool
    failover_ready: bool
    auto_updates: bool
    autostart: bool | None  # None = can't tell (not Windows)


@dataclass
class Step:
    id: str
    title: str
    status: str
    detail: str
    required: bool = True
    tab: str | None = None
    link: str | None = None
    link_label: str | None = None


def checklist(f: Facts) -> list[Step]:
    steps = [
        Step(
            "connect",
            "Connect to Discord",
            DONE if f.connected else TODO,
            "Pingwin is online." if f.connected else "Pingwin is starting up, or this PC's internet is down.",
        ),
        Step(
            "invite",
            "Invite Pingwin to your server",
            DONE if f.guild_name else TODO,
            f"Pingwin is in {f.guild_name}." if f.guild_name else "Open the invite link and pick your server.",
            link=f.invite,
            link_label="Invite link",
        ),
        Step(
            "guild_id",
            "Set your server ID",
            DONE if f.guild_id_set else OPTIONAL,
            "Slash commands update instantly."
            if f.guild_id_set
            else "Makes slash commands show up right away instead of within an hour. Put guild_id in config.toml.",
            required=False,
        ),
        Step(
            "manage_roles",
            "Let Pingwin manage roles",
            DONE if f.can_manage_roles else TODO,
            "Pingwin can hand out roles."
            if f.can_manage_roles
            else "Re-invite with the link above, or give Pingwin's role Manage Roles in Server Settings → Roles.",
        ),
        Step(
            "menu",
            "Choose the ping roles",
            DONE if f.menu_count else TODO,
            f"{f.menu_count} role(s) in the menu." if f.menu_count else "Add the roles people can opt into.",
            tab="roles",
        ),
    ]

    if not f.guild_name:
        roles_status, roles_detail = TODO, "Comes after Pingwin is in your server."
    elif f.missing_roles:
        roles_status, roles_detail = (
            TODO,
            "Missing in Discord: " + ", ".join(f.missing_roles) + ". Click Create missing roles.",
        )
    elif f.problem_roles:
        roles_status = WARN
        roles_detail = "Needs attention: " + ", ".join(f.problem_roles) + ". Hover the labels on the Role menu tab."
    else:
        roles_status, roles_detail = (
            (DONE, "Every menu role exists and is set up right.") if f.menu_count else (TODO, "")
        )
    steps.append(Step("roles_ready", "Create the roles in Discord", roles_status, roles_detail, tab="roles"))

    if f.gate_role:
        steps.append(
            Step(
                "gate",
                f"Make sure the {f.gate_role} role exists",
                DONE if f.gate_exists else TODO,
                f"Only members with {f.gate_role} can use the menu."
                if f.gate_exists
                else "Comes after Pingwin is in your server."
                if not f.guild_name
                else f"Nobody can use the menu until a role named {f.gate_role} exists, or you change Who can use it.",
                tab="roles",
            )
        )
    else:
        steps.append(
            Step(
                "gate",
                "Decide who can use the menu",
                OPTIONAL,
                "Anyone in the server can use it. Pick a role under Who can use it to limit it.",
                required=False,
                tab="roles",
            )
        )

    steps.append(
        Step(
            "posted",
            "Post the menu",
            DONE if f.menus_posted else TODO,
            f"Posted in {f.menus_posted} place(s)." if f.menus_posted else "Post it in a channel like #roles.",
            tab="roles",
        )
    )

    if f.autostart is not None:
        steps.append(
            Step(
                "autostart",
                "Start Pingwin with Windows",
                DONE if f.autostart else OPTIONAL,
                "Pingwin starts when you sign in to this PC."
                if f.autostart
                else "Double-click scripts\\install_autostart.cmd so Pingwin comes back after a reboot.",
                required=False,
            )
        )
    steps.append(
        Step(
            "updates",
            "Automatic updates",
            DONE if f.auto_updates else OPTIONAL,
            "Pingwin updates itself from GitHub."
            if f.auto_updates
            else "Downloaded as a ZIP? Install with git clone instead so Pingwin can update itself.",
            required=False,
        )
    )
    if not f.failover_enabled:
        fo_status, fo_detail = (
            OPTIONAL,
            "Add a backup PC on another network so Pingwin stays up if this one goes offline.",
        )
    elif f.failover_ready:
        fo_status, fo_detail = DONE, "Backup PCs take over if the answering PC goes offline."
    else:
        fo_status, fo_detail = WARN, "Failover is on, but pick a status channel and make sure this PC is in the list."
    steps.append(Step("failover", "Backup PCs", fo_status, fo_detail, required=False, tab="pcs"))
    return steps


# ------------------------------------------------------------------ gathering
_cache: dict[str, tuple[float, Any]] = {}


async def _cached(name: str, seconds: float, fn) -> Any:
    hit = _cache.get(name)
    if hit and time.monotonic() - hit[0] < seconds:
        return hit[1]
    value = await fn()
    _cache[name] = (time.monotonic(), value)
    return value


def startup_launcher() -> Path | None:
    """Where scripts\\install_autostart.cmd puts its launcher (Windows only)."""
    appdata = os.environ.get("APPDATA")
    if sys.platform != "win32" or not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Pingwin.cmd"


async def _autostart_installed() -> bool | None:
    launcher = startup_launcher()
    return None if launcher is None else launcher.exists()


async def _auto_updates() -> bool:
    if not (PROJECT_ROOT / ".git").exists():
        return False
    code, _ = await updater._git(PROJECT_ROOT, "rev-parse", "--abbrev-ref", "@{u}")
    return code == 0


async def gather_facts(bot: Pingwin, guild: discord.Guild | None) -> Facts:
    from ..cogs.roles import resolve_menu, role_problems

    cfg = bot.config
    missing: list[str] = []
    problems: list[str] = []
    gate_exists = False
    if guild is not None:
        for menu in cfg.roles.menus:
            for m, role in resolve_menu(guild, menu):
                if role is None:
                    missing.append(m.name)
                elif role_problems(guild, role, pingable=not menu.is_timezone):
                    problems.append(role.name)
        if cfg.roles.required_role:
            key = cfg.roles.required_role.casefold()
            gate_exists = any(r.name.casefold() == key for r in guild.roles)
    listed = cfg.failover.rank(bot.node.name) <= len(cfg.failover.pcs)
    return Facts(
        connected=bot.gateway_connected,
        guild_name=guild.name if guild else None,
        invite=invite_url(bot.user.id) if bot.user else None,
        guild_id_set=cfg.guild_id is not None,
        can_manage_roles=bool(guild and guild.me.guild_permissions.manage_roles),
        menu_count=len(cfg.roles.menu),
        missing_roles=missing,
        problem_roles=problems,
        gate_role=cfg.roles.required_role,
        gate_exists=gate_exists,
        menus_posted=len(menus.load_menus()),
        failover_enabled=cfg.failover.enabled,
        failover_ready=cfg.failover.active and listed,
        auto_updates=await _cached("updates", 600, _auto_updates),
        autostart=await _cached("autostart", 60, _autostart_installed),
    )


def summarize(steps: list[Step]) -> dict[str, Any]:
    required = [s for s in steps if s.required]
    return {
        "steps": [asdict(s) for s in steps],
        "required_done": sum(s.status == DONE for s in required),
        "required_total": len(required),
    }
