"""Repeating events: reminders start over for each date, and Pingwin keeps one message per event."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import discord
import pytest

from pingwin import store
from pingwin.cogs import events
from pingwin.cogs.events import is_new_date


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def test_new_date():
    start = int(datetime(2026, 11, 9, 2, 30, tzinfo=UTC).timestamp())
    assert is_new_date({"start": start - 7 * 86400}, start, 300)
    assert not is_new_date({"start": start}, start, 300)
    # Older records only have when things were posted.
    assert is_new_date({"reminded": "2026-10-26T19:30:20+00:00"}, start, 300)
    assert not is_new_date({"reminded": "2026-11-08T21:30:00+00:00"}, start, 300)
    assert not is_new_date({}, start, 300)


class Channel:
    id = 9

    def __init__(self, old=()):
        self.sent, self.deleted = [], []
        self.old = list(old)

    async def send(self, text, **kw):
        self.sent.append(text)
        return NS(id=500 + len(self.sent))

    def get_partial_message(self, mid):
        ch = self

        class Msg:
            async def delete(self):
                ch.deleted.append(mid)

        return Msg()

    async def history(self, limit):
        for m in self.old:
            yield m


def make(channel, event):
    async def never():
        await asyncio.Event().wait()

    cfg = NS(
        enabled=True,
        channel=9,
        announce_new=True,
        remind_minutes=60,
        reminders=[],
        remind_at_start=True,
        ping_interested=False,
        rules=[NS(role="Movie fans", keywords=["MMM"])],
        replace_old=True,
        post_next=True,
    )
    role = NS(mention="<@&1>", name="Movie fans")
    persona = NS(say=lambda key, **v: f"{key}: {v.get('event')} {v.get('when', '')}".strip())
    bot = NS(
        features=NS(events=cfg),
        is_active=True,
        home_guild=NS(scheduled_events=[event]),
        text_channel=lambda c: channel,
        get_channel=lambda i: channel,
        role_named=lambda g, n: role,
        persona=persona,
        get_cog=lambda n: None,
        user=NS(id=42),
        wait_until_ready=never,
    )
    cog = events.EventReminders.__new__(events.EventReminders)
    cog.bot = bot
    return cog


def event_at(start):
    return NS(
        id=777,
        name="MMM",
        description="",
        start_time=start,
        status=discord.EventStatus.scheduled,
        url="https://discord.com/events/1/777",
        guild=None,
    )


def test_each_week_gets_its_reminder_and_one_message_stays():
    async def go():
        now = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
        ev = event_at(now + timedelta(minutes=30))
        ch = Channel()
        cog = make(ch, ev)
        reminders = events.reminder_list(cog.cfg)
        pick, mark = events.due_reminder(reminders, "MMM", 30, set())
        await cog.post(ev, "reminded", pick, mark)
        assert len(ch.sent) == 1 and "<@&1>" in ch.sent[0]
        await cog.post(ev, "started")  # replaces the reminder
        assert ch.deleted == [501] and len(ch.sent) == 2
        # The occurrence is over; Discord moves the event to next week.
        ev.start_time = now + timedelta(days=7, minutes=30)
        await cog._check_date(ev, 60, now + timedelta(hours=3))
        assert ch.deleted == [501, 502] and "event_next: MMM" in ch.sent[-1] and "<@&1>" not in ch.sent[-1]
        rec = store.load(events.STATE)["777"]
        assert rec["start"] == int(ev.start_time.timestamp()) and "reminded:60" not in rec
        # Next week's reminder goes out again (it used to be skipped forever).
        await cog.post(ev, "reminded", pick, mark)
        assert len(ch.sent) == 4 and ch.deleted[-1] == 503

    asyncio.run(go())


def test_old_posts_are_found_in_the_channel():
    async def go():
        mine = NS(author=NS(id=42), content="⏰ MMM starts…\nhttps://discord.com/events/1/777", deleted=False)
        theirs = NS(author=NS(id=7), content="https://discord.com/events/1/777", deleted=False)
        other_event = NS(author=NS(id=42), content="https://discord.com/events/1/888", deleted=False)
        for m in (mine, theirs, other_event):

            async def delete(m=m):
                m.deleted = True

            m.delete = delete
        start = datetime(2026, 11, 9, 2, 30, tzinfo=UTC)
        ev = event_at(start)
        ch = Channel([mine, theirs, other_event])
        cog = make(ch, ev)
        store.save(events.STATE, {"777": {"reminded": "2026-10-26T19:30:20+00:00"}})
        await cog._check_date(ev, 300, datetime(2026, 11, 3, 4, 0, tzinfo=UTC))
        assert mine.deleted and not theirs.deleted and not other_event.deleted
        assert len(ch.sent) == 1 and "event_next" in ch.sent[0]

    asyncio.run(go())
