"""Simulates two PCs sharing one status channel to check the takeover rules end to end."""

import asyncio
import itertools
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import discord
import pytest

from pingwin.config import FailoverConfig, Node
from pingwin.failover import Coordinator

BOT_USER_ID = 1


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeMessage:
    _ids = itertools.count(100)

    def __init__(self, channel, content, clock):
        self.id = next(self._ids)
        self.channel, self.content, self.clock = channel, content, clock
        self.author = SimpleNamespace(id=BOT_USER_ID)
        self.created_at, self.edited_at = clock.now, None

    async def edit(self, content):
        self.content, self.edited_at = content, self.clock.now

    async def delete(self):
        self.channel.messages.remove(self)


class FakeChannel:
    def __init__(self, clock):
        self.clock, self.messages = clock, []

    async def send(self, content, **_):
        msg = FakeMessage(self, content, self.clock)
        self.messages.append(msg)
        return msg

    async def history(self, limit):
        snapshot = list(reversed(self.messages[-limit:]))
        await asyncio.sleep(0)  # let the other PC run in between, like real network calls
        for m in snapshot:
            yield m


def make_pc(name, channel):
    bot = SimpleNamespace(
        config=SimpleNamespace(failover=FailoverConfig(True, 99, 30, 120, pcs=("BTM-NOVA", "FRIEND-PC"))),
        node=Node(name),
        user=SimpleNamespace(id=BOT_USER_ID),
        gateway_connected=True,
        dispatch=lambda *a: None,
    )
    coord = Coordinator(bot)

    async def channel_lookup(_):
        return channel

    coord._channel = channel_lookup
    return coord


@pytest.fixture
def world(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(discord.utils, "utcnow", lambda: clock.now)
    channel = FakeChannel(clock)
    return clock, channel, make_pc("BTM-NOVA", channel), make_pc("FRIEND-PC", channel)


def run(*coros):
    async def go():
        for c in coros:
            await c

    asyncio.run(go())


def test_starts_quiet_until_first_check(world):
    _, _, main, backup = world
    assert not main.active and not backup.active


def test_main_preferred_backup_covers_outage(world):
    clock, channel, main, backup = world

    run(backup.tick())  # backup starts first and claims the empty job
    assert backup.active

    run(main.tick(), backup.tick())  # main comes online: takes it back, backup steps aside
    assert main.active and not backup.active

    for _ in range(3):  # main keeps checking in; backup keeps waiting
        clock.advance(30)
        run(main.tick(), backup.tick())
    assert main.active and not backup.active

    clock.advance(30)  # main's internet drops: no more check-ins
    run(backup.tick())
    assert not backup.active
    clock.advance(100)  # >120s since main's last check-in
    run(backup.tick())
    assert backup.active and backup.leader == "FRIEND-PC"

    run(main.tick(), backup.tick())  # main is back
    assert main.active and not backup.active
    assert len(channel.messages) == 1


def test_simultaneous_start_converges_to_one(world):
    clock, channel, main, backup = world

    # Both read the empty channel before either posts, so both post a status message.
    async def both():
        await asyncio.gather(main.tick(), backup.tick())

    asyncio.run(both())
    assert len(channel.messages) == 2
    clock.advance(30)
    run(main.tick(), backup.tick())
    clock.advance(30)
    run(main.tick(), backup.tick())
    assert main.active and not backup.active
    assert len(channel.messages) == 1
