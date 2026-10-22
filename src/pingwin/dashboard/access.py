"""Can Pingwin actually reach everything it's been set up to use?

Checks every channel picked in the settings (for features that are on), the role order (Pingwin
can only hand out roles below its own), and how many channels Pingwin can see at all.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

import discord

from .. import menus

if TYPE_CHECKING:
    from ..bot import Pingwin

OK, WARN, BAD = "ok", "warn", "bad"


def _item(status: str, title: str, detail: str = "", fix: str = "") -> dict[str, str]:
    return {"status": status, "title": title, "detail": detail, "fix": fix}


def configured_channels(bot: Pingwin) -> list[tuple[str, int, set[str]]]:
    """(what it's for, channel id, extra permissions needed) for everything that's switched on."""
    f = bot.config.features
    out: list[tuple[str, int, set[str]]] = []

    def add(label: str, cid: Any, extra: set[str] | None = None) -> None:
        if cid:
            out.append((label, int(cid), extra or set()))

    if bot.config.failover.enabled:
        add("Failover status", bot.config.failover.status_channel_id)
    if f.events.enabled:
        add("Event reminders", f.events.channel)
    if f.birthdays.enabled:
        add("Birthdays", f.birthdays.channel)
    if f.movienight.enabled:
        for s in f.movienight.series:
            add(f"Movie night ({s.name})", s.channel, {"send_polls"})
    if f.gameday.enabled:
        add("Game day (default channel)", f.gameday.channel)
    if f.streams.enabled:
        add("Stream alerts", f.streams.channel)
    if f.welcome.enabled:
        add("Welcome messages", f.welcome.welcome_channel)
        add("Approval cards", f.welcome.approval_channel)
        add("Approved announcements", f.welcome.approved_channel)
    if f.modlog.enabled:
        add("Mod log", f.modlog.channel)
    for pm in menus.load_menus():
        menu = bot.config.roles.get_menu(pm.menu)
        add(f"Role menu ({menu.title if menu else pm.menu})", pm.channel_id)
    return out


NEEDED = {"view_channel": "View Channel", "send_messages": "Send Messages", "embed_links": "Embed Links"}
EXTRA = {"send_polls": "Create Polls"}


def report(bot: Pingwin) -> dict[str, Any]:
    guild = bot.home_guild
    if guild is None:
        return {"items": [_item(WARN, "Pingwin isn't connected to your server right now")], "visible": None}
    me = guild.me
    items: list[dict[str, str]] = []

    missing = getattr(bot, "missing_intents", [])
    if missing:
        items.append(
            _item(
                BAD,
                "Welcome and the mod log are paused",
                f"Discord hasn't allowed {' and '.join(missing)} for Pingwin.",
                "Discord Developer Portal → your app → Bot → Privileged Gateway Intents: turn on "
                f"{' and '.join(missing)}, click Save Changes, then restart Pingwin.",
            )
        )

    # Roles Pingwin hands out
    if not me.guild_permissions.manage_roles:
        items.append(
            _item(
                BAD,
                "Pingwin can't hand out roles",
                "It needs the Manage Roles permission.",
                "Server Settings → Roles → Pingwin → turn on Manage Roles.",
            )
        )
    f = bot.config.features
    wanted: list[tuple[str, str]] = [(r.name, "role menus") for m in bot.config.roles.menus for r in m.roles]
    if f.welcome.enabled and f.welcome.approve_role:
        wanted.append((f.welcome.approve_role, "the Approve button"))
    if f.birthdays.enabled and f.birthdays.birthday_role:
        wanted.append((f.birthdays.birthday_role, "birthdays"))
    above = []
    for name, why in wanted:
        role = bot.role_named(guild, name)
        if role is not None and role >= me.top_role:
            above.append(f"{role.name} ({why})")
    if above:
        items.append(
            _item(
                BAD,
                f"{len(above)} role(s) sit above Pingwin's role, so it can't give them",
                ", ".join(above),
                f"Server Settings → Roles: drag {me.top_role.name} above them (just under your admin role is fine).",
            )
        )
    elif wanted:
        items.append(_item(OK, "Pingwin's role is high enough for every role it hands out"))
    if f.welcome.enabled and f.welcome.deny_action == "kick" and not me.guild_permissions.kick_members:
        items.append(
            _item(
                BAD,
                "The Deny button is set to kick, but Pingwin can't kick",
                fix="Give Pingwin's role Kick Members, or set the Deny button to just mark the card.",
            )
        )

    # Channels it's been told to use
    seen_ok = 0
    for label, cid, extra in configured_channels(bot):
        channel = guild.get_channel(cid)
        if channel is None:
            items.append(
                _item(
                    BAD,
                    f"{label}: channel not found",
                    f"Channel {cid} was deleted or Pingwin can't see it.",
                    "Pick the channel again on the Features tab, or add Pingwin to it.",
                )
            )
            continue
        perms = channel.permissions_for(me)
        missing = [n for k, n in NEEDED.items() if not getattr(perms, k)]
        missing += [n for k, n in EXTRA.items() if k in extra and not getattr(perms, k, True)]
        if missing:
            items.append(
                _item(
                    BAD,
                    f"{label}: Pingwin can't use #{channel.name}",
                    "Missing: " + ", ".join(missing),
                    f"Edit #{channel.name} → Permissions → add Pingwin (or its role) with {', '.join(missing)}.",
                )
            )
        else:
            seen_ok += 1
    if seen_ok:
        items.append(_item(OK, f"{seen_ok} channel(s) in use are all reachable"))

    # How much of the server it can see
    text = [c for c in guild.text_channels]
    hidden = [c for c in text if not c.permissions_for(me).view_channel]
    visible = len(text) - len(hidden)
    if hidden:
        # Which role would unlock most of them? Usually the members' role (e.g. Friend).
        votes: Counter[discord.Role] = Counter()
        for c in hidden:
            for target, ow in c.overwrites.items():
                if isinstance(target, discord.Role) and ow.view_channel and not target.is_default():
                    votes[target] += 1
        best = next((r for r, _ in votes.most_common() if not r.permissions.administrator), None)
        fix = (
            f"Easiest: give the Pingwin bot the {best.name} role (Server Settings → Members → Pingwin → +). "
            f"It then sees what {best.name} members see, without any extra permissions."
            if best
            else "Add Pingwin to the channels it should use (channel → Edit → Permissions)."
        )
        names = ", ".join(f"#{c.name}" for c in hidden[:8]) + (" …" if len(hidden) > 8 else "")
        items.append(_item(WARN, f"Pingwin can see {visible} of {len(text)} text channels", f"Hidden: {names}", fix))
    else:
        items.append(_item(OK, f"Pingwin can see all {len(text)} text channels"))
    order = {BAD: 0, WARN: 1, OK: 2}
    items.sort(key=lambda i: order[i["status"]])
    return {"items": items, "visible": visible, "total": len(text)}
