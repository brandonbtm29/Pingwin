"""Attendance: who counts, what they earn, and the UnbelievaBoat call."""

import asyncio
from datetime import UTC, datetime

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from pingwin.cogs import attendance as att
from pingwin.config import parse_config


def session(start=1000.0):
    return {"started_at": start, "seconds": {}, "open": {}, "checked_in": [], "added": [], "removed": []}


def test_threshold():
    assert att.threshold(30, 3 * 3600) == 1800  # long event: the setting
    assert att.threshold(30, 40 * 60) == 1200  # 40-minute event: half of it
    assert att.threshold(30, 4 * 60) == 300  # never less than 5 minutes


def test_who_counts():
    s = session()
    att.voice_join(s, "a", 1000)
    att.voice_leave(s, "a", 1000 + 20 * 60)  # 20 min
    att.voice_join(s, "a", 1000 + 40 * 60)
    att.voice_leave(s, "a", 1000 + 55 * 60)  # +15 = 35 min
    att.voice_join(s, "b", 1000)
    att.voice_leave(s, "b", 1000 + 10 * 60)  # 10 min: not enough
    att.voice_join(s, "c", 1000 + 60 * 60)  # still there at the end: 2h
    s["checked_in"].append("d")
    s["added"].append("e")
    s["removed"].append("c")
    s["ended_at"] = 1000 + 3 * 3600
    assert att.attendees(s, 30, 0) == ["a", "d", "e"]
    s["removed"].clear()
    assert att.attendees(s, 30, 0) == ["a", "c", "d", "e"]


def test_awards_and_keys():
    assert att.awards_for(["a", "b"], "b", 10, 5) == {"a": 10, "b": 15}
    start = datetime(2026, 10, 22, 0, 30, tzinfo=UTC)
    assert att.session_key(123, start) == "123-202610220030"
    assert len(att.new_code()) == 4 and set(att.new_code()) <= set(att.CODE_LETTERS)


def test_feature_settings_parse():
    cfg = parse_config({"attendance": {"enabled": True, "amount": 25, "checkin": "all"}}, require_token=False)
    a = cfg.features.attendance
    assert (a.enabled, a.amount, a.checkin, a.payout, a.balance, a.currency) == (
        True,
        25,
        "all",
        "unbelievaboat",
        "cash",
        "Frumples",
    )
    assert not cfg.features.needs_members  # no privileged intents needed


def test_unbelievaboat_call(monkeypatch):
    seen = {}

    async def patch(request):
        seen["path"] = request.path
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = await request.json()
        if request.headers.get("Authorization") != "good":
            return web.Response(status=401)
        return web.json_response({"cash": 50})

    async def go():
        app = web.Application()
        app.router.add_patch("/api/v1/guilds/{g}/users/{u}", patch)
        async with TestServer(app) as server:
            monkeypatch.setattr(att, "UNB_API", str(server.make_url("/api/v1")))
            async with aiohttp.ClientSession() as http:
                await att.add_to_unbelievaboat(http, "good", 1, "42", 10, "bank", "Pingwin: attended MMM")
                assert seen == {
                    "path": "/api/v1/guilds/1/users/42",
                    "auth": "good",
                    "body": {"bank": 10, "reason": "Pingwin: attended MMM"},
                }
                with pytest.raises(att.PayoutError, match="token"):
                    await att.add_to_unbelievaboat(http, "bad", 1, "42", 10, "cash", "x")

    asyncio.run(go())


def test_a_voice_event_from_start_to_finish(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import discord

    from pingwin import store

    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    clock = {"t": 1_800_000_000.0}
    monkeypatch.setattr(att.time, "time", lambda: clock["t"])
    start = datetime.fromtimestamp(clock["t"] - 60, tz=UTC)
    monkeypatch.setattr(att.discord.utils, "utcnow", lambda: datetime.fromtimestamp(clock["t"], tz=UTC))
    vc = SimpleNamespace(voice_states={7: None, 8: None})
    event = SimpleNamespace(
        id=1,
        name="MMM",
        start_time=start,
        end_time=None,
        status=discord.EventStatus.active,
        entity_type=discord.EntityType.voice,
        channel_id=10,
        creator_id=7,
    )
    guild = SimpleNamespace(
        id=99, scheduled_events=[event], get_channel=lambda cid: vc if cid == 10 else None, get_member=lambda uid: None
    )
    cfg = parse_config({"attendance": {"enabled": True, "amount": 10, "host_bonus": 5}}, require_token=False)
    bot = SimpleNamespace(
        features=SimpleNamespace(attendance=cfg.features.attendance, secret=lambda name: ""),
        is_active=True,
        home_guild=guild,
        text_channel=lambda cid: None,
        loop=None,
    )

    async def go():
        cog = att.Attendance.__new__(att.Attendance)
        cog.bot, cog.http = bot, None
        await att.Attendance.check.coro(cog)  # event is live: attendance starts
        sessions = store.load(att.STATE)["sessions"]
        ((key, s),) = sessions.items()
        assert sorted(s["open"]) == ["7", "8"] and not s["finalized"]

        clock["t"] += 10 * 60
        vc.voice_states = {7: None}  # 8 leaves after 10 minutes
        await att.Attendance.check.coro(cog)
        clock["t"] += 50 * 60
        vc.voice_states = {}  # everyone's gone...
        await att.Attendance.check.coro(cog)
        assert not store.load(att.STATE)["sessions"][key]["finalized"]
        clock["t"] += 16 * 60  # ...for over 15 minutes, so it's over
        await att.Attendance.check.coro(cog)
        s = store.load(att.STATE)["sessions"][key]
        assert s["finalized"] and s["attendees"] == ["7"]
        assert s["awards"]["7"] == {"amount": 15, "status": "failed", "error": "no UnbelievaBoat token set"}

    asyncio.run(go())
