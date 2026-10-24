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
HISTORY_SCAN = 50

# Standby PCs check in every 30 minutes with their own message, so admins can see the backups are alive.
CHECKIN_HEADER = "🛟 **Pingwin backup check-in**"
_CHECKIN_LINE = re.compile(r"^-# backup:(\{.*\})\s*$", re.MULTILINE)
ALERT_HEADER = "⚠️ **Pingwin backup alert**"
_ALERT_LINE = re.compile(r"^-# alert:(\{.*\})\s*$", re.MULTILINE)
CHECKIN_EVERY = 30 * 60
QUIET_AFTER = 65 * 60  # two missed check-ins (plus a little slack) before an alert


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


def render_checkin(node: Node, version: str, now: datetime) -> str:
    payload = json.dumps({"node": node.name}, separators=(",", ":"))
    return (
        f"{CHECKIN_HEADER}\n"
        f"**{node.name}** is on standby and ready to take over (Pingwin {version}).\n"
        f"Last check-in <t:{int(now.timestamp())}:R>. It checks in every 30 minutes.\n"
        f"-# backup:{payload}"
    )


def parse_tagged(content: str, line: re.Pattern) -> str | None:
    """The PC name in a check-in or alert message's hidden tag line."""
    match = line.search(content or "")
    if not match:
        return None
    try:
        return str(json.loads(match.group(1))["node"])
    except (ValueError, KeyError, TypeError):
        return None


def render_alert(pc: str, last: datetime | None, back: datetime | None = None) -> str:
    payload = json.dumps({"node": pc}, separators=(",", ":"))
    if back is not None:
        body = f"✅ **{pc}** checked in again <t:{int(back.timestamp())}:R>. The backup is ready."
    elif last is None:
        body = f"**{pc}** hasn't checked in. If that PC is off or Pingwin isn't running on it, there's no backup."
    else:
        body = (
            f"**{pc}** hasn't checked in since <t:{int(last.timestamp())}:R>. If that PC is off or asleep, "
            "or Pingwin isn't running on it, there's no backup right now."
        )
    return f"{ALERT_HEADER}\n{body}\n-# alert:{payload}"


def quiet_pcs(pcs: list[str], me: str, checkins: dict[str, datetime], now: datetime) -> list[str]:
    """Listed PCs (other than this one) whose last check-in is missing or too old."""
    out = []
    for pc in pcs:
        if pc.casefold() == me.casefold():
            continue
        last = next((t for name, t in checkins.items() if name.casefold() == pc.casefold()), None)
        if last is None or (now - last).total_seconds() > QUIET_AFTER:
            out.append(pc)
    return out


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
        self.last_lease: Lease | None = None  # the most recent check-in we saw (ours or another PC's)
        self._warned_channel = False
        self._announced = False  # log the first decision even if it matches the starting state
        self._checked_in: float | None = None  # time.monotonic() of our last standby check-in
        self._active_from: float | None = None  # time.monotonic() when we became active (alerts wait a bit)
        self._last_watch: float = 0.0
        self.checkins: dict[str, datetime] = {}  # other PCs' latest check-ins (Discord's timestamps)
        self.quiet: list[str] = []  # PCs that haven't checked in lately

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

        messages, checkins, alerts = await self._scan(channel)
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
            self.last_lease = Lease(self.bot.node.name, now)
            if lease is None or lease.node.casefold() == self.bot.node.name.casefold():
                reason = "no one else is active"
            elif lease.age(now) > cfg.takeover_after_seconds:
                reason = f"took over from {lease.node} (it went quiet)"
            else:
                reason = f"took over from {lease.node} (this PC is earlier in the list)"
            self._set_active(True, reason)
            await self._watch_backups(channel, checkins, alerts, now)
        else:
            assert lease is not None
            self.leader = lease.node
            self.last_lease = lease
            self._set_active(False, f"{lease.node} is active")
            await self._check_in(channel, checkins, now)

    async def _check_in(self, channel, checkins: dict[str, discord.Message], now: datetime) -> None:
        """On standby: refresh this PC's check-in message every 30 minutes."""
        if self._checked_in is not None and time.monotonic() - self._checked_in < CHECKIN_EVERY:
            return
        from . import __version__

        content = render_checkin(self.bot.node, __version__, now)
        mine = next((m for name, m in checkins.items() if name.casefold() == self.bot.node.name.casefold()), None)
        try:
            if mine is None:
                await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
            else:
                await mine.edit(content=content)
            self._checked_in = time.monotonic()
            log.info("Backup check-in from %s", self.bot.node.name)
        except discord.HTTPException as e:
            log.warning("Couldn't post this PC's backup check-in: %s", e)

    async def _watch_backups(self, channel, checkins: dict[str, discord.Message], alerts, now: datetime) -> None:
        """On the answering PC: alert (once) when a listed PC stops checking in, and say when it's back."""
        cfg = self.bot.config.failover
        self.checkins = {name: m.edited_at or m.created_at for name, m in checkins.items()}
        if self._active_from is None:
            self._active_from = time.monotonic()
        if time.monotonic() - self._active_from < CHECKIN_EVERY + 300:
            return  # give the backups a full round to check in after we take over
        if time.monotonic() - self._last_watch < 300:
            return
        self._last_watch = time.monotonic()
        self.quiet = quiet_pcs(list(cfg.pcs), self.bot.node.name, self.checkins, now)
        try:
            for pc in self.quiet:
                if pc.casefold() not in alerts:
                    last = next((t for n, t in self.checkins.items() if n.casefold() == pc.casefold()), None)
                    await channel.send(render_alert(pc, last), allowed_mentions=discord.AllowedMentions.none())
                    log.warning("Backup %s hasn't checked in", pc)
            for pc, msg in alerts.items():
                if pc not in {q.casefold() for q in self.quiet} and "✅" not in msg.content:
                    back = next((t for n, t in self.checkins.items() if n.casefold() == pc), now)
                    name = parse_tagged(msg.content, _ALERT_LINE) or pc
                    await msg.edit(content=render_alert(name, None, back))
                    log.info("Backup %s checked in again", name)
        except discord.HTTPException as e:
            log.warning("Couldn't post a backup alert: %s", e)

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

    async def _scan(self, channel: discord.abc.Messageable):
        """Pingwin's messages in the status channel: lease messages (oldest first; the oldest is the
        real one), each PC's check-in message, and open alerts (newest per PC, by lowercase name)."""
        status: list[discord.Message] = []
        checkins: dict[str, discord.Message] = {}
        alerts: dict[str, discord.Message] = {}
        async for m in channel.history(limit=HISTORY_SCAN):  # newest first
            if not (self.bot.user and m.author.id == self.bot.user.id):
                continue
            if m.content.startswith(HEADER):
                status.append(m)
            elif m.content.startswith(CHECKIN_HEADER):
                name = parse_tagged(m.content, _CHECKIN_LINE)
                if name and name not in checkins:
                    checkins[name] = m
            elif m.content.startswith(ALERT_HEADER):
                name = parse_tagged(m.content, _ALERT_LINE)
                if name and name.casefold() not in alerts:
                    alerts[name.casefold()] = m
        return sorted(status, key=lambda m: m.id), checkins, alerts

    async def _status_messages(self, channel: discord.abc.Messageable) -> list[discord.Message]:
        return (await self._scan(channel))[0]

    def _set_active(self, value: bool, reason: str) -> None:
        if value == self.active and self._announced:
            return
        self._announced = True
        changed = value != self.active
        self.active = value
        if value:
            log.info("ACTIVE on %s: %s", self.bot.node.name, reason)
            if changed:
                self.bot.dispatch("pingwin_active")
        else:
            self.active_since = None
            self._active_from = None
            log.info("STANDBY on %s: %s", self.bot.node.name, reason)
            if changed:
                self.bot.dispatch("pingwin_standby")
