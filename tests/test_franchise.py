"""Franchise queues: parts in order, announced before showtime, logged when the movie starts."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from pingwin import franchise, movies, store
from pingwin.cogs import movienight

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def test_parse_and_parts():
    items = franchise.parse_items("1. Alien (1979)\n2) ✅ Aliens 1986\nAlien 3\nPart 4 - Alien Resurrection (1997)")
    assert [(i["title"], i["year"]) for i in items] == [
        ("Alien", 1979),
        ("Aliens", 1986),
        ("Alien 3", None),
        ("Alien Resurrection", 1997),
    ]
    q = franchise.new_queue("Alien", items)
    assert items[1]["done"] and not items[0]["done"]
    assert franchise.next_part(q) is items[0]
    items[0]["done"] = "2026-10-02"
    items[2]["skipped"] = True
    nxt = franchise.next_part(q)
    assert nxt["title"] == "Alien Resurrection" and franchise.part_number(q, nxt) == (3, 3)
    assert franchise.label(nxt) == "Alien Resurrection (1997)"


def test_due_announces_then_logs():
    q = franchise.new_queue("Alien", franchise.parse_items("Alien (1979)\nAliens (1986)"))
    fri = datetime(2026, 10, 30, 21, 30, tzinfo=ET)
    assert franchise.due(q, fri, fri - timedelta(hours=8), 6) is None  # too early
    assert franchise.due(q, fri, fri - timedelta(hours=5), 6) == "announce"
    q["announced_for"] = "2026-10-30"
    assert franchise.due(q, fri, fri - timedelta(hours=1), 6) is None  # already announced
    nxt = fri + timedelta(days=7)  # after it starts, the next showing is a week later
    assert franchise.due(q, nxt, fri + timedelta(minutes=1), 6) == "log"
    q["logged_for"] = "2026-10-30"
    assert franchise.due(q, nxt, fri + timedelta(minutes=2), 6) is None
    q["paused"] = True
    assert franchise.due(q, nxt, nxt - timedelta(hours=1), 6) is None
    q["paused"], q["postponed_for"] = False, nxt.date().isoformat()
    assert franchise.due(q, nxt, nxt - timedelta(hours=1), 6) is None  # not this week


def make_cog():
    async def never():
        await asyncio.Event().wait()

    series = NS(name="FF", full_name="Franchise Fridays", role="", channel=5, showtime="Fri 9:30pm", franchises=True)
    cfg = NS(enabled=True, series=[series], queue_announce_hours=6, rate=False, auto_details=False, omdb_daily=800)
    sent = []

    class Channel:
        guild = None

        async def send(self, text, **kw):
            sent.append(text)

    persona = NS(say=lambda key, **v: f"{key}: {v.get('title', '')} {v.get('part', '')}/{v.get('total', '')}")
    bot = NS(
        features=NS(movienight=cfg, secret=lambda k: None, tz=ET),
        is_active=True,
        wait_until_ready=never,
        persona=persona,
        text_channel=lambda cid: Channel(),
        role_named=lambda g, n: None,
    )
    cog = movienight.MovieNight(bot)
    cog.check.cancel()
    cog.fill_loop.cancel()
    return cog, series, sent


def test_two_fridays_end_to_end(monkeypatch):
    clock = {"now": datetime(2026, 10, 30, 16, 0, tzinfo=ET)}

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"]

    monkeypatch.setattr(movienight, "datetime", FakeDT)

    async def go():
        cog, s, sent = make_cog()
        state = {
            "ff": {
                "nominations": [],
                "polls": [],
                "queue": franchise.new_queue(
                    "Alien", franchise.parse_items("✅ Alien (1979)\nAliens (1986)\nAlien 3 (1992)")
                ),
            }
        }
        store.save(movienight.STATE, state)
        await cog.run_queues()  # 4pm: announce part 2 of 3
        assert sent == ["franchise_next: Aliens (1986) 2/3"]
        await cog.run_queues()
        assert len(sent) == 1  # once
        clock["now"] = datetime(2026, 10, 30, 21, 31, tzinfo=ET)
        await cog.run_queues()  # it started: logged
        lib = movies.load()
        assert [(movies.label(lib["movies"][e["movie_id"]]), e["date"]) for e in lib["history"]] == [
            ("Aliens (1986)", "2026-10-30")
        ]
        clock["now"] = datetime(2026, 11, 6, 16, 0, tzinfo=ET)
        await cog.run_queues()
        assert sent[-1] == "franchise_next: Alien 3 (1992) 3/3"
        clock["now"] = datetime(2026, 11, 6, 22, 0, tzinfo=ET)
        await cog.run_queues()  # last part logged: franchise done
        assert sent[-1].startswith("franchise_done")
        assert franchise.finished(store.load(movienight.STATE)["ff"]["queue"])
        clock["now"] = datetime(2026, 11, 13, 16, 0, tzinfo=ET)
        await cog.run_queues()
        assert sent[-1].startswith("franchise_done")  # nothing more to announce

    asyncio.run(go())
