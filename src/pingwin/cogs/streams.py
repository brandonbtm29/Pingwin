"""Stream alerts: post once when a streamer goes live on Twitch, YouTube, or in a Discord voice channel."""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

import aiohttp
import discord
from discord.ext import commands, tasks

from .. import store

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.streams")
STATE = "streams"
YT_NS = {"yt": "http://www.youtube.com/xml/schemas/2015", "a": "http://www.w3.org/2005/Atom"}


def format_alert(template: str, name: str, live: list[dict]) -> str:
    title = next((x["title"] for x in live if x.get("title")), "")
    game = next((x["game"] for x in live if x.get("game")), "")
    links = "\n".join(x.get("line") or f"{x['platform']}: {x['url']}" for x in live)
    return (
        template.replace("{name}", name)
        .replace("{title}", title)
        .replace("{game}", game)
        .replace("{links}", links)
        .replace("\\n", "\n")
    )


DISCORD_REPEAT_GAP = 20 * 60  # Go Live again within this long (e.g. a dropped stream) doesn't re-alert


def matches_member(handle: str, member: discord.abc.User) -> bool:
    """Does a 'Discord username' cell refer to this member? Username, display name or ID; @ optional."""
    want = (handle or "").strip().lstrip("@").casefold()
    if not want:
        return False
    names = {member.name, getattr(member, "display_name", ""), getattr(member, "global_name", "") or "", str(member.id)}
    return want in {n.casefold() for n in names if n}


class StreamAlerts(commands.Cog):
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self._twitch_token: tuple[str, float] | None = None
        self._last_error: dict[str, float] = {}
        self.check.start()

    async def cog_unload(self) -> None:
        self.check.cancel()
        if self.session:
            await self.session.close()

    @property
    def cfg(self):
        return self.bot.features.streams

    def _warn(self, key: str, msg: str, *args: Any) -> None:
        if time.monotonic() - self._last_error.get(key, -1e9) > 3600:  # at most once an hour
            log.warning(msg, *args)
            self._last_error[key] = time.monotonic()

    # ------------------------------------------------------------ Twitch
    async def _twitch_headers(self) -> dict[str, str] | None:
        cid, secret = self.bot.features.secret("TWITCH_CLIENT_ID"), self.bot.features.secret("TWITCH_CLIENT_SECRET")
        if not (cid and secret):
            self._warn("twitch-keys", "Stream alerts: Twitch keys aren't set (dashboard → Features → Stream alerts)")
            return None
        if not self._twitch_token or self._twitch_token[1] < time.time() + 60:
            async with self.session.post(
                "https://id.twitch.tv/oauth2/token",
                params={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"},
            ) as r:
                if r.status != 200:
                    self._warn("twitch-auth", "Stream alerts: Twitch rejected the keys (%s)", r.status)
                    return None
                data = await r.json()
            self._twitch_token = (data["access_token"], time.time() + data.get("expires_in", 3600))
        return {"Client-Id": cid, "Authorization": f"Bearer {self._twitch_token[0]}"}

    async def twitch_live(self, logins: list[str]) -> dict[str, dict]:
        if not logins:
            return {}
        headers = await self._twitch_headers()
        if not headers:
            return {}
        params = [("user_login", login.lower()) for login in logins]
        async with self.session.get("https://api.twitch.tv/helix/streams", params=params, headers=headers) as r:
            if r.status == 401:
                self._twitch_token = None
            if r.status != 200:
                self._warn("twitch", "Stream alerts: Twitch check failed (%s)", r.status)
                return {}
            data = await r.json()
        return {
            s["user_login"].lower(): {
                "platform": "Twitch",
                "key": f"tw:{s['id']}",
                "title": s.get("title", ""),
                "game": s.get("game_name", ""),
                "url": f"https://twitch.tv/{s['user_login']}",
            }
            for s in data.get("data", [])
            if s.get("type") == "live"
        }

    # ------------------------------------------------------------ YouTube
    async def _yt_channel_id(self, handle: str, key: str, cache: dict) -> str | None:
        handle = handle.strip()
        if handle.startswith("UC") and len(handle) == 24:
            return handle
        if handle in cache:
            return cache[handle]
        h = handle if handle.startswith("@") else "@" + handle
        async with self.session.get(
            "https://www.googleapis.com/youtube/v3/channels", params={"part": "id", "forHandle": h, "key": key}
        ) as r:
            if r.status != 200:
                self._warn("yt-handle", "Stream alerts: YouTube lookup for %s failed (%s)", h, r.status)
                return None
            items = (await r.json()).get("items", [])
        if not items:
            self._warn(f"yt-{h}", "Stream alerts: YouTube channel %s not found", h)
            return None
        cache[handle] = items[0]["id"]
        return cache[handle]

    async def youtube_live(self, handle: str, cache: dict) -> dict | None:
        key = self.bot.features.secret("YOUTUBE_API_KEY")
        if not key:
            self._warn("yt-key", "Stream alerts: YouTube API key isn't set (dashboard → Features → Stream alerts)")
            return None
        channel_id = await self._yt_channel_id(handle, key, cache)
        if not channel_id:
            return None
        async with self.session.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": channel_id}) as r:
            if r.status != 200:
                return None
            root = ET.fromstring(await r.text())
        ids = [e.text for e in root.iterfind("a:entry/yt:videoId", YT_NS)][:5]
        if not ids:
            return None
        async with self.session.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet,liveStreamingDetails", "id": ",".join(ids), "key": key},
        ) as r:
            if r.status != 200:
                self._warn("yt", "Stream alerts: YouTube check failed (%s)", r.status)
                return None
            items = (await r.json()).get("items", [])
        for v in items:
            if v.get("snippet", {}).get("liveBroadcastContent") == "live":
                return {
                    "platform": "YouTube",
                    "key": f"yt:{v['id']}",
                    "title": v["snippet"].get("title", ""),
                    "game": "",
                    "url": f"https://youtu.be/{v['id']}",
                }
        return None

    # ------------------------------------------------------------ loop
    @tasks.loop(minutes=3)
    async def check(self) -> None:
        if self.check.minutes != self.cfg.check_minutes:
            self.check.change_interval(minutes=self.cfg.check_minutes)
        if not (self.cfg.enabled and self.bot.is_active and self.cfg.streamers):
            return
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        state = store.load(STATE)
        cache = state.setdefault("_youtube_ids", {})
        try:
            twitch = await self.twitch_live([s.twitch for s in self.cfg.streamers if s.twitch])
        except aiohttp.ClientError as e:
            self._warn("twitch-net", "Stream alerts: couldn't reach Twitch (%s)", e)
            twitch = {}
        for s in self.cfg.streamers:
            name = s.name or s.twitch or s.youtube
            live = []
            if s.twitch and s.twitch.lower() in twitch:
                live.append(twitch[s.twitch.lower()])
            if s.youtube:
                try:
                    yt = await self.youtube_live(s.youtube, cache)
                except (aiohttp.ClientError, ET.ParseError) as e:
                    self._warn("yt-net", "Stream alerts: couldn't reach YouTube (%s)", e)
                    yt = None
                if yt:
                    live.append(yt)
            rec = state.setdefault(name, {"live": False, "seen": []})
            keys = [x["key"] for x in live]
            new = [k for k in keys if k not in rec["seen"]]
            if live and new and not rec["live"]:
                await self.announce(name, live)
            rec["live"] = bool(live)
            rec["seen"] = (rec["seen"] + new)[-20:]
        store.save(STATE, state)

    @check.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ Discord Go Live
    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        cfg = self.cfg
        if not (cfg.enabled and cfg.discord_live and self.bot.is_active) or member.bot:
            return
        started = bool(after.self_stream and after.channel) and not (before.self_stream and before.channel)
        stopped = bool(before.self_stream) and not (after.self_stream and after.channel)
        if not (started or stopped):
            return
        streamer = next((s for s in cfg.streamers if matches_member(getattr(s, "discord", ""), member)), None)
        if streamer is None:
            return
        state = store.load(STATE)
        rec = state.setdefault(f"discord:{member.id}", {"live": False, "at": 0})
        now = time.time()
        if stopped:
            rec["live"] = False
        elif not rec["live"]:
            rec["live"] = True
            if now - rec.get("at", 0) > DISCORD_REPEAT_GAP:
                rec["at"] = now
                game = next((a.name for a in getattr(member, "activities", ()) if isinstance(a, discord.Game)), "")
                live = [
                    {
                        "platform": "Discord",
                        "line": f"Watch in {after.channel.mention}",
                        "title": "",
                        "game": game,
                        "url": after.channel.jump_url,
                    }
                ]
                await self.announce(streamer.name or member.display_name, live)
        store.save(STATE, state)

    async def announce(self, name: str, live: list[dict]) -> None:
        channel = self.bot.text_channel(self.cfg.channel)
        if channel is None:
            self._warn("channel", "Stream alerts: no channel set, or Pingwin can't see it")
            return
        role = self.bot.role_named(channel.guild, self.cfg.role)
        text = format_alert(self.cfg.message, name, live)
        if role:
            text = f"{role.mention} {text}"
        await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions(roles=[role] if role else False))
        log.info("%s is live (%s)", name, ", ".join(x["platform"] for x in live))


async def setup(bot: Pingwin) -> None:
    await bot.add_cog(StreamAlerts(bot))
