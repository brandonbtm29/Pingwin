"""Event attendance and Frumples.

While a Discord Event runs, Pingwin notes who's in its voice channel (and who taps "I'm here" for
in-person events). When it ends, everyone who stayed long enough counts as attending. Pingwin then
adds the reward to their UnbelievaBoat balance and posts a recap.

A session starts when the event goes live (or its start time passes) and ends when the event ends,
its end time passes, or its voice channel has been empty for a while. Everything lives in the shared
settings folder (state/attendance.json), so a backup PC can pick up a running event.
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import store

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.attendance")
STATE = "attendance"
UNB_API = "https://unbelievaboat.com/api/v1"
EMPTY_ENDS_AFTER = 15 * 60  # a voice event with nobody left for this long is over
LONGEST = 8 * 3600  # no event runs longer than this
KEEP_DAYS = 120
CODE_LETTERS = "ACDEFHJKMNPRTVWXY"  # no look-alikes


# --------------------------------------------------------------------- pure helpers (tested)
def threshold(min_minutes: int, duration_s: float) -> float:
    """Seconds someone must be there. Short events only need half their length (but at least 5 minutes)."""
    return min(min_minutes * 60, max(300.0, duration_s * 0.5))


def seconds_present(session: dict[str, Any], uid: str, now: float) -> float:
    total = float(session["seconds"].get(uid, 0))
    if uid in session["open"]:
        total += max(0.0, now - session["open"][uid])
    return total


def attendees(session: dict[str, Any], min_minutes: int, now: float) -> list[str]:
    end = session.get("ended_at") or now
    need = threshold(min_minutes, end - session["started_at"])
    people = {u for u in set(session["seconds"]) | set(session["open"]) if seconds_present(session, u, end) >= need}
    people |= set(session["checked_in"]) | set(session["added"])
    people -= set(session["removed"])
    return sorted(people)


def awards_for(people: list[str], creator: str | None, amount: int, host_bonus: int) -> dict[str, int]:
    return {u: amount + (host_bonus if creator and u == creator else 0) for u in people}


def new_code() -> str:
    return "".join(secrets.choice(CODE_LETTERS) for _ in range(4))


def session_key(event_id: int, start: datetime) -> str:
    """Recurring events reuse their ID, so the date is part of the key."""
    return f"{event_id}-{start.astimezone(UTC):%Y%m%d%H%M}"


def voice_join(session: dict[str, Any], uid: str, now: float) -> None:
    session["open"].setdefault(uid, now)


def voice_leave(session: dict[str, Any], uid: str, now: float) -> None:
    start = session["open"].pop(uid, None)
    if start is not None:
        session["seconds"][uid] = session["seconds"].get(uid, 0) + max(0.0, now - start)
        session["last_voice"] = now  # someone was there until now


# --------------------------------------------------------------------- UnbelievaBoat
class PayoutError(Exception):
    pass


async def add_to_unbelievaboat(
    http: aiohttp.ClientSession, token: str, guild_id: int, user_id: str, amount: int, balance: str, reason: str
) -> None:
    async with http.patch(
        f"{UNB_API}/guilds/{guild_id}/users/{user_id}",
        json={balance: amount, "reason": reason[:200]},
        headers={"Authorization": token},
    ) as r:
        if r.status == 200:
            return
        if r.status == 401:
            raise PayoutError("UnbelievaBoat didn't accept the API token")
        if r.status == 403:
            raise PayoutError("the UnbelievaBoat application isn't authorized for this server (Economy permission)")
        if r.status == 429:
            raise PayoutError("UnbelievaBoat asked Pingwin to slow down; try again in a minute")
        text = (await r.text())[:150]
        raise PayoutError(f"UnbelievaBoat said {r.status}: {text}")


# --------------------------------------------------------------------- check-in button
class CheckInButton(discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:att:(?P<key>[0-9]+-[0-9]+)"):
    def __init__(self, key: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="I'm here", emoji="📍", style=discord.ButtonStyle.success, custom_id=f"pingwin:att:{key}"
            )
        )
        self.key = key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["key"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: Pingwin = interaction.client  # type: ignore[assignment]
        session = store.load(STATE).get("sessions", {}).get(self.key)
        if session is None or session.get("finalized"):
            await interaction.response.send_message("Check-in for this event has closed.", ephemeral=True)
            return
        if session.get("code"):
            await interaction.response.send_modal(CodeModal(self.key))
            return
        await interaction.response.send_message(check_in(bot, self.key, str(interaction.user.id)), ephemeral=True)


class CodeModal(discord.ui.Modal, title="Check in"):
    code = discord.ui.TextInput(label="Code the host announced", min_length=4, max_length=4)

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key

    async def on_submit(self, interaction: discord.Interaction) -> None:
        session = store.load(STATE).get("sessions", {}).get(self.key)
        if session is None or session.get("finalized"):
            await interaction.response.send_message("Check-in for this event has closed.", ephemeral=True)
            return
        if str(self.code.value).strip().upper() != session.get("code"):
            await interaction.response.send_message("That's not the code. Ask the host for it.", ephemeral=True)
            return
        await interaction.response.send_message(
            check_in(interaction.client, self.key, str(interaction.user.id)),
            ephemeral=True,  # type: ignore[arg-type]
        )


def check_in(bot: Pingwin, key: str, uid: str) -> str:
    state = store.load(STATE)
    session = state.get("sessions", {}).get(key)
    if session is None or session.get("finalized"):
        return "Check-in for this event has closed."
    if uid in session["checked_in"]:
        return "You're already checked in. 👍"
    session["checked_in"].append(uid)
    store.save(STATE, state)
    return f"Checked in to **{session['name']}**. Have fun! 🐧"


# --------------------------------------------------------------------- the cog
class Attendance(commands.GroupCog, group_name="attendance", group_description="Event attendance"):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.http: aiohttp.ClientSession | None = None
        self.check.start()

    def cog_unload(self) -> None:
        self.check.cancel()
        if self.http and not self.http.closed:
            self.bot.loop.create_task(self.http.close())

    @property
    def cfg(self):
        return self.bot.features.attendance

    def _on(self) -> bool:
        return bool(self.cfg.enabled and self.bot.is_active)

    # ----- voice tracking --------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot or not self._on():
            return
        b = before.channel.id if before.channel else None
        a = after.channel.id if after.channel else None
        if a == b:
            return
        state = store.load(STATE)
        now = time.time()
        changed = False
        for s in state.get("sessions", {}).values():
            if s.get("finalized") or not s.get("channel_id"):
                continue
            if s["channel_id"] == b:
                voice_leave(s, str(member.id), now)
                changed = True
            if s["channel_id"] == a:
                voice_join(s, str(member.id), now)
                s["last_voice"] = now
                changed = True
        if changed:
            store.save(STATE, state)

    def _http(self) -> aiohttp.ClientSession:
        if self.http is None or self.http.closed:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self.http

    def _in_channel(self, guild: discord.Guild, channel_id: int | None) -> set[str]:
        ch = guild.get_channel(channel_id) if channel_id else None
        if ch is None or not hasattr(ch, "voice_states"):  # voice and stage channels
            return set()
        out = set()
        for uid in ch.voice_states:
            m = guild.get_member(uid)
            if m is None or not m.bot:
                out.add(str(uid))
        return out

    # ----- the clock -------------------------------------------------------
    @tasks.loop(minutes=1)
    async def check(self) -> None:
        if not self._on():
            return
        guild = self.bot.home_guild
        if guild is None:
            return
        state = store.load(STATE)
        sessions: dict[str, Any] = state.setdefault("sessions", {})
        now = time.time()
        nowdt = discord.utils.utcnow()
        changed = False
        live_keys = set()

        for event in guild.scheduled_events:
            start = event.start_time
            if event.status == discord.EventStatus.active or (
                event.status == discord.EventStatus.scheduled and start <= nowdt <= start + timedelta(hours=2)
            ):
                key = session_key(event.id, start)
                live_keys.add(key)
                if key not in sessions:
                    sessions[key] = self._new_session(event, now)
                    changed = True
                    log.info("Taking attendance for %s", event.name)
                    await self._post_checkin(event, key, sessions[key])
                s = sessions[key]
                if not s.get("finalized"):
                    s["name"] = event.name
                    if event.end_time:
                        s["ends_at"] = event.end_time.timestamp()

        by_event = {e.id: e for e in guild.scheduled_events}
        for key, s in sessions.items():
            if s.get("finalized"):
                continue
            # Catch up on anything missed (e.g. while another PC was answering).
            here = self._in_channel(guild, s.get("channel_id"))
            for uid in list(s["open"]):
                if uid not in here:
                    voice_leave(s, uid, now)
                    changed = True
            for uid in here - set(s["open"]):
                voice_join(s, uid, now)
                changed = True
            if here:
                s["last_voice"] = now
            event = by_event.get(s["event_id"])
            over = (
                event is None
                or event.status in (discord.EventStatus.completed, discord.EventStatus.canceled)
                or (key not in live_keys and event.status != discord.EventStatus.active)
                or (s.get("ends_at") and now >= s["ends_at"] and event.status != discord.EventStatus.active)
                or now - s["started_at"] > LONGEST
                # Everyone who came has left (people who are late don't end it before anyone arrives).
                or (
                    s.get("channel_id")
                    and (s["seconds"] or s["open"])
                    and not here
                    and now - s.get("last_voice", s["started_at"]) > EMPTY_ENDS_AFTER
                )
            )
            if over:
                await self.finalize(guild, key, s, now)
                changed = True

        cutoff = now - KEEP_DAYS * 86400
        for key in [k for k, s in sessions.items() if s.get("finalized") and s["started_at"] < cutoff]:
            sessions.pop(key)
            changed = True
        if changed:
            store.save(STATE, state)

    @check.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    def _new_session(self, event: discord.ScheduledEvent, now: float) -> dict[str, Any]:
        kind = {discord.EntityType.voice: "voice", discord.EntityType.stage_instance: "stage"}.get(
            event.entity_type, "external"
        )
        s: dict[str, Any] = {
            "event_id": event.id,
            "name": event.name,
            "kind": kind,
            "channel_id": event.channel_id if kind != "external" else None,
            "creator_id": str(event.creator_id) if event.creator_id else None,
            "started_at": now,
            "ends_at": event.end_time.timestamp() if event.end_time else None,
            "last_voice": now,
            "seconds": {},
            "open": {},
            "checked_in": [],
            "added": [],
            "removed": [],
            "code": new_code() if self.cfg.require_code else None,
            "finalized": False,
            "awards": {},
        }
        return s

    async def _post_checkin(self, event: discord.ScheduledEvent, key: str, s: dict[str, Any]) -> None:
        mode = self.cfg.checkin
        if mode == "off" or (mode == "external" and s["kind"] != "external"):
            return
        channel = self.bot.text_channel(self.cfg.channel)
        if channel is None:
            log.warning("Attendance: no channel set for check-in buttons, or Pingwin can't post there")
            return
        view = discord.ui.View(timeout=None)
        view.add_item(CheckInButton(key))
        extra = " You'll need the code the host announces." if s.get("code") else ""
        try:
            msg = await channel.send(self.bot.persona.say("checkin_open", event=event.name) + extra, view=view)
            s["checkin_message"] = [channel.id, msg.id]
        except discord.HTTPException as e:
            log.warning("Attendance: couldn't post the check-in button: %s", e)

    # ----- the end of an event ---------------------------------------------
    async def finalize(self, guild: discord.Guild, key: str, s: dict[str, Any], now: float) -> None:
        for uid in list(s["open"]):
            voice_leave(s, uid, now)
        s["ended_at"] = now
        s["finalized"] = True
        people = attendees(s, self.cfg.min_minutes, now)
        s["attendees"] = people
        log.info("%s ended: %d attended", s["name"], len(people))
        if s.get("checkin_message"):
            try:
                ch = guild.get_channel(s["checkin_message"][0])
                if isinstance(ch, discord.TextChannel):
                    await ch.get_partial_message(s["checkin_message"][1]).edit(
                        content=f"📍 Check-in for **{s['name']}** has closed.", view=None
                    )
            except discord.HTTPException:
                pass
        if not people:
            return
        amounts = awards_for(people, s.get("creator_id"), self.cfg.amount, self.cfg.host_bonus)
        await self.pay(guild, s, amounts)
        await self.post_recap(s)

    async def pay(self, guild: discord.Guild, s: dict[str, Any], amounts: dict[str, int]) -> None:
        cfg = self.cfg
        token = self.bot.features.secret("UNBELIEVABOAT_TOKEN")
        for uid, amount in amounts.items():
            rec = s["awards"].setdefault(uid, {"amount": amount, "status": "pending"})
            rec["amount"] = amount
            if rec.get("status") == "paid":
                continue
            if cfg.payout == "off" or amount == 0:
                rec["status"] = "skipped"
                continue
            if not token:
                rec.update(status="failed", error="no UnbelievaBoat token set")
                continue
            try:
                await add_to_unbelievaboat(
                    self._http(), token, guild.id, uid, amount, cfg.balance, f"Pingwin: attended {s['name']}"
                )
                rec.update(status="paid", error=None, at=time.time())
            except (PayoutError, aiohttp.ClientError, TimeoutError) as e:
                rec.update(status="failed", error=str(e) or type(e).__name__)
                log.warning("Couldn't add %s to %s: %s", cfg.currency, uid, rec["error"])

    async def post_recap(self, s: dict[str, Any]) -> None:
        channel = self.bot.text_channel(self.cfg.channel)
        if channel is None:
            return
        cur = self.cfg.currency or "Frumples"
        lines = []
        for uid in s["attendees"]:
            a = s["awards"].get(uid, {})
            note = f" +{a['amount']}" if a.get("status") == "paid" else ""
            lines.append(f"<@{uid}>{note}")
        failed = [u for u, a in s["awards"].items() if a.get("status") == "failed"]
        embed = discord.Embed(
            title=f"📋 {s['name']}: {len(s['attendees'])} came",
            description=" · ".join(lines)[:3900],
            color=discord.Color.from_rgb(88, 166, 255),
        )
        if self.cfg.payout != "off":
            if failed:
                err = s["awards"][failed[0]].get("error", "")
                embed.set_footer(
                    text=f"Couldn't add {cur} for {len(failed)} ({err}). A mod can retry on the dashboard."
                )
            else:
                embed.set_footer(text=f"{cur} added in UnbelievaBoat. Thanks for coming!")
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            log.warning("Attendance: couldn't post the recap: %s", e)

    # ----- changes by mods (commands and dashboard) -------------------------
    async def change(self, key: str, uid: str, action: str) -> str:
        """add/remove someone, or pay whoever's unpaid. Works during and after the event."""
        guild = self.bot.home_guild
        state = store.load(STATE)
        s = state.get("sessions", {}).get(key)
        if s is None or guild is None:
            return "I can't find that event."
        if action == "add":
            if uid in s["removed"]:
                s["removed"].remove(uid)
            if uid not in s["added"]:
                s["added"].append(uid)
        elif action == "remove":
            if uid in s["added"]:
                s["added"].remove(uid)
            if uid not in s["removed"]:
                s["removed"].append(uid)
        msg = "Done."
        if s.get("finalized"):
            before = set(s.get("attendees", []))
            s["attendees"] = attendees(s, self.cfg.min_minutes, s["ended_at"])
            now_in = set(s["attendees"])
            cur = self.cfg.currency or "Frumples"
            if action == "add" and uid in now_in - before or action == "pay":
                targets = (
                    awards_for([uid], s.get("creator_id"), self.cfg.amount, self.cfg.host_bonus)
                    if action == "add"
                    else {u: a["amount"] for u, a in s["awards"].items() if a.get("status") == "failed"}
                )
                await self.pay(guild, s, targets)
                bad = [u for u in targets if s["awards"][u]["status"] == "failed"]
                msg = f"Couldn't add {cur}: {s['awards'][bad[0]]['error']}" if bad else f"{cur} added."
            elif action == "remove" and uid in before - now_in:
                a = s["awards"].get(uid)
                if a and a.get("status") == "paid" and self.cfg.payout != "off":
                    try:
                        await add_to_unbelievaboat(
                            self._http(),
                            self.bot.features.secret("UNBELIEVABOAT_TOKEN"),
                            guild.id,
                            uid,
                            -int(a["amount"]),
                            self.cfg.balance,
                            f"Pingwin: removed from {s['name']}",
                        )
                        a["status"] = "taken back"
                        msg = f"Removed and took back {a['amount']} {cur}."
                    except (PayoutError, aiohttp.ClientError, TimeoutError) as e:
                        msg = f"Removed, but couldn't take the {cur} back: {e}"
                else:
                    s["awards"].pop(uid, None)
        store.save(STATE, state)
        return msg

    def recent(self, days: int = 30) -> list[tuple[str, dict[str, Any]]]:
        cutoff = time.time() - days * 86400
        sessions = store.load(STATE).get("sessions", {})
        return sorted(
            ((k, s) for k, s in sessions.items() if s["started_at"] >= cutoff), key=lambda kv: -kv[1]["started_at"]
        )

    async def event_autocomplete(self, interaction: discord.Interaction, current: str):
        cur = current.casefold()
        out = []
        for k, s in self.recent(30):
            when = datetime.fromtimestamp(s["started_at"], tz=UTC).astimezone(self.bot.features.tz)
            name = f"{s['name']} · {when:%b %d}" + ("" if s.get("finalized") else " (now)")
            if cur in name.casefold():
                out.append(app_commands.Choice(name=name[:100], value=k))
        return out[:25]

    async def _mod(self, interaction: discord.Interaction) -> bool:
        if self.bot.is_mod(interaction.user):
            return True
        await interaction.response.send_message("Only mods can change attendance.", ephemeral=True)
        return False

    def _pick(self, event: str | None) -> str | None:
        recent = self.recent(30)
        if event:
            return event if any(k == event for k, _ in recent) else None
        return recent[0][0] if recent else None

    @app_commands.command(name="add", description="Count someone as attending an event (mods)")
    @app_commands.autocomplete(event=event_autocomplete)
    async def add(self, interaction: discord.Interaction, member: discord.Member, event: str | None = None) -> None:
        if not await self._mod(interaction):
            return
        key = self._pick(event)
        if key is None:
            await interaction.response.send_message("No recent event found.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await self.change(key, str(member.id), "add")
        log.info("%s added %s to attendance for %s", interaction.user, member, key)
        await interaction.followup.send(f"{member.mention}: {msg}", ephemeral=True)

    @app_commands.command(name="remove", description="Take someone off an event's attendance (mods)")
    @app_commands.autocomplete(event=event_autocomplete)
    async def remove(self, interaction: discord.Interaction, member: discord.Member, event: str | None = None) -> None:
        if not await self._mod(interaction):
            return
        key = self._pick(event)
        if key is None:
            await interaction.response.send_message("No recent event found.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await self.change(key, str(member.id), "remove")
        log.info("%s removed %s from attendance for %s", interaction.user, member, key)
        await interaction.followup.send(f"{member.mention}: {msg}", ephemeral=True)

    @app_commands.command(name="code", description="Get the check-in code for an event happening now (host or mods)")
    async def code(self, interaction: discord.Interaction) -> None:
        running = [(k, s) for k, s in self.recent(1) if not s.get("finalized")]
        uid = str(interaction.user.id)
        mine = [(k, s) for k, s in running if self.bot.is_mod(interaction.user) or s.get("creator_id") == uid]
        if not mine:
            await interaction.response.send_message("No event of yours is running right now.", ephemeral=True)
            return
        lines = [
            f"**{s['name']}**: code **{s['code']}**" if s.get("code") else f"**{s['name']}**: no code needed"
            for _, s in mine
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="history", description="Events someone came to recently")
    async def history(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        who = member or interaction.user
        uid = str(who.id)
        went = [s for _, s in self.recent(KEEP_DAYS) if s.get("finalized") and uid in s.get("attendees", [])]
        if not went:
            await interaction.response.send_message(
                f"No events on record for {who.mention} yet.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        lines = [f"<t:{int(s['started_at'])}:d> **{s['name']}**" for s in went[:10]]
        await interaction.response.send_message(
            f"{who.mention} came to **{len(went)}** event(s) in the last {KEEP_DAYS} days:\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: Pingwin) -> None:
    bot.add_dynamic_items(CheckInButton)
    await bot.add_cog(Attendance(bot))
