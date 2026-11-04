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


MMM_RULE = {"start": "2026-03-17T00:30:00+00:00", "end": None, "frequency": 2, "interval": 1, "by_weekday": [1]}
GAME_RULE = {"start": "2026-03-21T19:00:00+00:00", "end": None, "frequency": 2, "interval": 2, "by_weekday": [5]}


def test_next_date_from_the_repeat_rule():
    nov3 = datetime(2026, 11, 3, 0, 30, tzinfo=UTC)
    assert events.next_occurrence(MMM_RULE, nov3) == nov3 + timedelta(days=7)
    assert events.next_occurrence(MMM_RULE, nov3 - timedelta(minutes=1)) == nov3
    # Every other Saturday, from Mar 21: Nov 14 is one of them, Nov 7 isn't.
    assert events.next_occurrence(GAME_RULE, datetime(2026, 11, 3, tzinfo=UTC)) == datetime(
        2026, 11, 14, 19, 0, tzinfo=UTC
    )
    assert events.next_occurrence({"start": "2026-01-01T00:00:00+00:00", "frequency": 1}, nov3) is None  # monthly
    assert events.next_occurrence(None, nov3) is None
    ended = {**MMM_RULE, "end": "2026-11-01T00:00:00+00:00"}
    assert events.next_occurrence(ended, nov3) is None


def test_a_date_nobody_started_moves_on():
    nov3 = datetime(2026, 11, 3, 0, 30, tzinfo=UTC)
    ev = event_at(nov3)
    assert events.effective_start(ev, MMM_RULE, nov3 + timedelta(hours=2)) == nov3  # still on, maybe late
    assert events.effective_start(ev, MMM_RULE, nov3 + timedelta(hours=5)) == nov3 + timedelta(days=7)
    assert events.effective_start(ev, None, nov3 + timedelta(hours=5)) == nov3  # one-off: left alone

    async def go():
        ch = Channel()
        cog = make(ch, ev)
        store.save(events.STATE, {"777": {"start": int(nov3.timestamp()), "msg": [9, 400]}})
        now = nov3 + timedelta(hours=5)
        await cog._check_date(ev, 60, now, events.effective_start(ev, MMM_RULE, now))
        assert ch.deleted == [400] and "event_next: MMM" in ch.sent[-1]
        assert store.load(events.STATE)["777"]["start"] == int((nov3 + timedelta(days=7)).timestamp())
        # Its reminder next week goes out even though Discord still shows the old date.
        reminders = events.reminder_list(cog.cfg)
        pick, mark = events.due_reminder(reminders, "MMM", 30, set())
        await cog.post(ev, "reminded", pick, mark, when=nov3 + timedelta(days=7))
        assert len(ch.sent) == 2 and str(int((nov3 + timedelta(days=7)).timestamp())) in ch.sent[-1]

    asyncio.run(go())


def test_movie_card():
    from pingwin.cogs.movienight import movie_card

    m = {
        "title": "Alien 3",
        "year": 1992,
        "imdb_id": "tt0103644",
        "poster": "https://m.media-amazon.com/images/alien3.jpg",
        "imdb_rating": 6.4,
        "runtime": 114,
        "genre": "Action, Horror, Sci-Fi",
        "plot": "x " * 200,
        "director": "David Fincher",
    }
    e = movie_card(m, "Part 3 of 6 of Alien")
    assert e.title == "Alien 3 (1992)" and e.url == "https://www.imdb.com/title/tt0103644/"
    assert e.image.url.endswith("alien3.jpg") and e.footer.text == "Part 3 of 6 of Alien"
    assert (
        e.description.startswith("⭐ IMDb 6.4 · 114 min · Action, Horror, Sci-Fi") and "David Fincher" in e.description
    )
    assert len(e.description) < 400
    bare = movie_card({"title": "Mystery Movie"})
    assert bare.title == "Mystery Movie" and bare.url is None and bare.image.url is None


def test_movie_nights_get_a_movie_card():
    async def go():
        ev = event_at(datetime(2026, 11, 7, 1, 30, tzinfo=UTC))
        ch = Channel()
        sent_embeds = []
        orig = ch.send

        async def send(text, **kw):
            sent_embeds.append(kw.get("embeds"))
            return await orig(text, **kw)

        ch.send = send
        cog = make(ch, ev)

        async def info(name, start):
            return {
                "line": "🍿 Tonight: **Alien 3 (1992)**, part 3 of 6.",
                "nudge": "",
                "votes": None,
                "movie": {"title": "Alien 3", "year": 1992, "imdb_id": "tt0103644"},
                "why": "Part 3 of 6 of Alien",
            }

        cog.bot.get_cog = lambda n: NS(reminder_info=info)
        reminders = events.reminder_list(cog.cfg)
        pick, mark = events.due_reminder(reminders, "MMM", 30, set())
        await cog.post(ev, "reminded", pick, mark)
        assert "Alien 3" in ch.sent[-1] and sent_embeds[-1][0].title == "Alien 3 (1992)"
        cog.cfg.movie_cards = False
        await cog.post(ev, "started")
        assert sent_embeds[-1] == []

    asyncio.run(go())
