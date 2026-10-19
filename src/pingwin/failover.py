"""Keeps exactly one Pingwin in charge when several PCs run it.

Every PC connects to Discord, but only the *active* one answers buttons and
commands. The others wait on *standby* and stay quiet.

The active PC checks in by editing a status message in a mod-only channel
(the "lease"). If check-ins stop for `takeover_after_seconds` (PC off,
internet down, crashed), the next PC in `[failover] pcs` takes over. A PC
earlier in that list takes the job back as soon as it returns.

Discord's own timestamps on the message are used, so it doesn't matter if the
PCs' clocks disagree a little.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from .config import FailoverConfig, Node

if TYPE_CHECKING:
    from .bot import Pingwin

log = logging.getLogger("pingwin.failover")

HEADER = "🐧 **Pingwin status**"
_LEASE_LINE = re.compile(r"^-# lease:(\{.*\})\s*$", re.MULTILINE)
HISTORY_SCAN = 25


@dataclass(frozen=True)
class Lease:
    node: str
    renewed_at: datetime  # Discord's timestamp of the last check-in

    def age(self, now: datetime) -> float:
        return (now - self.renewed_at).total_seconds()


def render_lease(node: Node, role: str, active_since: datetime, now: datetime) -> str:
    payload = json.dumps({"node": node.name}, separators=(",", ":"))
    return (
        f"{HEADER}\n"
        f"Answering from **{node.name}** ({role}) since <t:{int(active_since.timestamp())}:f>\n"
        f"Last check-in <t:{int(now.timestamp())}:R>. If this stops updating, a backup PC takes over.\n"
        f"-# lease:{payload}"
    )


def parse_lease(content: str, renewed_at: datetime) -> Lease | None:
    match = _LEASE_LINE.search(content)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        return Lease(node=str(data["node"]), renewed_at=renewed_at)
    except (ValueError, KeyError, TypeError):
        return None


def should_lead(lease: Lease | None, me: Node, now: datetime, cfg: FailoverConfig) -> bool:
    """Decide whether this PC should be the active one."""
    if lease is None:
        return True  # nobody has ever claimed it
    if lease.node.casefold() == me.name.casefold():
        return True  # it's us: keep renewing
    if lease.age(now) > cfg.takeover_after_seconds:
        return True  # the active PC went quiet
    return cfg.rank(me.name) < cfg.rank(lease.node)  # we're earlier in the list: take it back


class Coordinator:
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.active = not bot.config.failover.active  # single-PC mode is always active
        self.active_since: datetime | None = None
        self.leader: str | None = bot.node.name if self.active else None
        self._last_renewed: float | None = None  # time.monotonic()
        self._warned_channel = False

    async def tick(self) -> None:
        cfg = self.bot.config.failover
        if not cfg.active:
            self._set_active(True, "failover is off (single PC)")
            self.leader = self.bot.node.name
            return
        if not self.bot.gateway_connected:
            self._check_stale()
            return

        channel = await self._channel(cfg.status_channel_id)
        if channel is None:
            # Misconfigured: answering (possibly twice) beats going silent.
            self._set_active(True, "status channel unavailable")
            return

        messages = await self._status_messages(channel)
        current = messages[0] if messages else None
        lease = parse_lease(current.content, current.edited_at or current.created_at) if current else None
        now = discord.utils.utcnow()

        if should_lead(lease, self.bot.node, now, cfg):
            if not self.active or self.active_since is None:
                self.active_since = now
            content = render_lease(self.bot.node, cfg.describe(self.bot.node.name), self.active_since, now)
            if current is None:
                await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
            else:
                await current.edit(content=content)
                for extra in messages[1:]:  # tidy duplicates left by a race
                    await extra.delete()
            self._last_renewed = time.monotonic()
            self.leader = self.bot.node.name
            if lease is None or lease.node.casefold() == self.bot.node.name.casefold():
                reason = "no one else is active"
            elif lease.age(now) > cfg.takeover_after_seconds:
                reason = f"took over from {lease.node} (it went quiet)"
            else:
                reason = f"took over from {lease.node} (this PC is earlier in the list)"
            self._set_active(True, reason)
        else:
            assert lease is not None
            self.leader = lease.node
            self._set_active(False, f"{lease.node} is active")

    def _check_stale(self) -> None:
        """Step down if we haven't been able to check in for too long."""
        limit = self.bot.config.failover.takeover_after_seconds
        if self.active and self._last_renewed is not None and time.monotonic() - self._last_renewed > limit:
            self._set_active(False, "couldn't check in with Discord; a backup has likely taken over")

    async def after_error(self) -> None:
        self._check_stale()

    async def _channel(self, channel_id: int | None) -> discord.abc.Messageable | None:
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                channel = None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            if not self._warned_channel:
                log.error(
                    "Failover status channel %s not found or not a text channel; check config.toml",
                    channel_id,
                )
                self._warned_channel = True
            return None
        self._warned_channel = False
        return channel

    async def _status_messages(self, channel: discord.abc.Messageable) -> list[discord.Message]:
        """All of Pingwin's status messages in the channel, oldest first (the oldest is the real one)."""
        found = [
            m
            async for m in channel.history(limit=HISTORY_SCAN)
            if self.bot.user and m.author.id == self.bot.user.id and m.content.startswith(HEADER)
        ]
        return sorted(found, key=lambda m: m.id)

    def _set_active(self, value: bool, reason: str) -> None:
        if value == self.active:
            return
        self.active = value
        if value:
            log.info("ACTIVE on %s: %s", self.bot.node.name, reason)
            self.bot.dispatch("pingwin_active")
        else:
            self.active_since = None
            log.info("STANDBY on %s: %s", self.bot.node.name, reason)
            self.bot.dispatch("pingwin_standby")
