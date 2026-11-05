"""Simulates two PCs sharing one status channel to check the takeover rules end to end."""

import asyncio
import itertools
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import discord
import pytest

from pingwin import failover
from pingwin.config import FailoverConfig, Node
from pingwin.failover import WARMUP_SECONDS, Coordinator

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


def status(channel):
    from pingwin.failover import HEADER

    return [m for m in channel.messages if m.content.startswith(HEADER)]


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
    start = clock.now
    monkeypatch.setattr(discord.utils, "utcnow", lambda: clock.now)
    # Each PC's own clocks (monotonic and wall) follow the simulated time too.
    monkeypatch.setattr(
        failover,
        "time",
        SimpleNamespace(
            monotonic=lambda: (clock.now - start).total_seconds() + 1000,
            time=lambda: clock.now.timestamp(),
        ),
    )
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


def wait_for_takeback(clock, main, backup):
    """Tick both PCs every 30 s until main takes the job back; returns how long that took."""
    waited = 0
    while not main.active:
        clock.advance(30)
        waited += 30
        run(main.tick(), backup.tick())
        assert main.active != backup.active  # exactly one answers at every step
        assert waited <= WARMUP_SECONDS + 60
    return waited


def ticks_until_active(clock, pc, limit=300):
    """Tick one PC every 30 s until it's active; returns how many seconds that took."""
    for waited in range(0, limit, 30):
        run(pc.tick())
        if pc.active:
            return waited
        clock.advance(30)
    raise AssertionError(f"{pc.bot.node.name} never became active")


def test_main_preferred_backup_covers_outage(world):
    clock, channel, main, backup = world

    run(backup.tick())  # backup starts first and claims the empty job
    assert backup.active

    run(main.tick(), backup.tick())  # main comes online: waits for SyncThing before taking it back
    assert backup.active and not main.active
    assert wait_for_takeback(clock, main, backup) == WARMUP_SECONDS  # then takes it back, backup steps aside
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

    run(main.tick(), backup.tick())  # main is back, but its state/ files may be behind: wait again
    assert backup.active and not main.active
    assert wait_for_takeback(clock, main, backup) == WARMUP_SECONDS
    assert main.active and not backup.active
    assert len(status(channel)) == 1


def test_simultaneous_start_converges_to_one(world):
    clock, channel, main, backup = world

    # Both read the empty channel before either posts, so both post a status message.
    async def both():
        await asyncio.gather(main.tick(), backup.tick())

    asyncio.run(both())
    assert len(status(channel)) == 2
    clock.advance(30)
    run(main.tick(), backup.tick())
    clock.advance(30)
    run(main.tick(), backup.tick())
    assert main.active and not backup.active
    assert len(status(channel)) == 1


def test_backups_check_in_and_quiet_ones_raise_one_alert(world, monkeypatch):
    from pingwin import failover

    clock, channel, main, backup = world
    start = clock.now
    monkeypatch.setattr(failover.time, "monotonic", lambda: (clock.now - start).total_seconds() + 1000)

    async def run(minutes, pcs):
        for _ in range(int(minutes * 2)):
            for pc in pcs:
                await pc.tick()
            clock.advance(30)

    async def go():
        await run(1, [main, backup])
        checkins = [m for m in channel.messages if m.content.startswith(failover.CHECKIN_HEADER)]
        assert len(checkins) == 1 and "FRIEND-PC" in checkins[0].content
        await run(40, [main, backup])  # still checking in: no alert
        assert main.checkins and not any(m.content.startswith(failover.ALERT_HEADER) for m in channel.messages)
        await run(80, [main])  # the backup PC goes quiet
        alerts = [m for m in channel.messages if m.content.startswith(failover.ALERT_HEADER)]
        assert len(alerts) == 1 and "FRIEND-PC" in alerts[0].content and main.quiet == ["FRIEND-PC"]
        backup._checked_in = None  # it comes back (restarted)
        await run(10, [main, backup])
        alerts = [m for m in channel.messages if m.content.startswith(failover.ALERT_HEADER)]
        assert len(alerts) == 1 and "✅" in alerts[0].content and main.quiet == []

    asyncio.run(go())


def test_clock_skew_does_not_cause_takeover(world, monkeypatch):
    """A backup whose clock runs fast must not think the main PC's fresh check-ins are old."""
    clock, channel, main, backup = world
    skew = {"v": 0}
    monkeypatch.setattr(discord.utils, "utcnow", lambda: clock.now + timedelta(seconds=skew["v"]))
    for _ in range(10):
        skew["v"] = 0
        run(main.tick())
        clock.advance(15)
        skew["v"] = 150  # the backup PC's clock is 2.5 minutes fast
        run(backup.tick())
        clock.advance(15)
        assert main.active and not backup.active
    assert len(status(channel)) == 1


def test_slow_clock_backup_still_takes_over(world, monkeypatch):
    """...and a backup whose clock runs slow still notices when the main PC stops checking in."""
    clock, channel, main, backup = world
    skew = {"v": 0}
    monkeypatch.setattr(discord.utils, "utcnow", lambda: clock.now + timedelta(seconds=skew["v"]))
    run(main.tick())
    skew["v"] = -600  # ten minutes slow
    clock.advance(30)  # main goes quiet
    assert ticks_until_active(clock, backup) == 150  # first tick after 120 s without check-ins, by its own clock


def test_returning_main_waits_while_backup_checks_in(world):
    clock, channel, main, backup = world
    run(backup.tick())
    assert backup.active
    clock.advance(30)
    run(main.tick())  # main returns: doesn't grab the job (and re-send what backup already sent)
    assert backup.active and not main.active
    for _ in range(4):
        clock.advance(30)
        run(main.tick(), backup.tick())
    main.bot.gateway_connected = False  # main's connection drops for a moment: the warm-up starts over
    run(main.tick())
    main.bot.gateway_connected = True
    assert wait_for_takeback(clock, main, backup) == WARMUP_SECONDS + 30  # the first tick back starts the clock


def test_main_waking_from_sleep_warms_up_again(world):
    clock, channel, main, backup = world
    run(main.tick(), backup.tick())
    assert main.active
    clock.advance(600)  # main PC asleep for 10 minutes; backup covers
    for _ in range(5):
        run(backup.tick())
        clock.advance(30)
    assert backup.active
    run(main.tick(), backup.tick())  # wakes up: standby first, even though it's been running a long time
    assert backup.active and not main.active
    assert wait_for_takeback(clock, main, backup) == WARMUP_SECONDS


def test_main_takes_over_at_once_from_a_quiet_backup(world):
    """Warm-up only applies to a backup that's still answering; a dead one is replaced as before."""
    clock, channel, main, backup = world
    run(backup.tick())  # ...and then the backup stops too
    clock.advance(30)
    assert ticks_until_active(clock, main) == 150 < WARMUP_SECONDS  # 120 s of silence, not the 3-minute warm-up


def test_second_outage_raises_a_new_alert(world):
    clock, channel, main, backup = world

    async def go(minutes, pcs):
        for _ in range(int(minutes * 2)):
            for pc in pcs:
                await pc.tick()
            clock.advance(30)

    def alerts():
        return [m for m in channel.messages if m.content.startswith(failover.ALERT_HEADER)]

    async def scenario():
        await go(41, [main, backup])
        await go(80, [main])  # first outage: alert
        assert len(alerts()) == 1 and "✅" not in alerts()[0].content
        backup._checked_in = None
        await go(10, [main, backup])  # back: alert edited to OK
        assert len(alerts()) == 1 and "✅" in alerts()[0].content
        await go(120, [main])  # second outage, 2 hours: a new alert
        assert main.quiet == ["FRIEND-PC"]
        assert len(alerts()) == 2 and "✅" in alerts()[0].content and "✅" not in alerts()[1].content
        await go(60, [main])  # still out: no repeat
        assert len(alerts()) == 2
        backup._checked_in = None
        await go(10, [main, backup])  # back again
        assert len(alerts()) == 2 and all("✅" in a.content for a in alerts())

    asyncio.run(scenario())
