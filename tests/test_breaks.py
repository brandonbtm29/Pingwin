"""Breaks: weeks a movie night series skips. Polls, franchise parts, "did it happen?" cards and the voice check
skip them, and the series' repeating Discord Event moves past them."""

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest
from test_movie_fixes import confirm, fff, make_cog, posts, use_clock
from test_repeating_events import FakeHttp, make

from pingwin import breaks, franchise, movies, store
from pingwin.cogs import events, movienight
from pingwin.cogs.movienight import parse_from_date, poll_close

ET = ZoneInfo("America/New_York")
FRI = "Fri 9:30pm"


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def test_planning_and_merging_breaks():
    now = datetime(2026, 11, 7, 12, 0, tzinfo=ET)  # a Saturday
    assert breaks.plan(FRI, now, 3, []) == (date(2026, 11, 13), date(2026, 11, 27))
    b = breaks.add([], date(2026, 11, 13), date(2026, 11, 27), "between franchises", "Mod")
    assert breaks.covering(b, date(2026, 11, 20))["why"] == "between franchises"
    assert breaks.covering(b, date(2026, 12, 4)) is None
    assert breaks.next_showing(FRI, now, b) == datetime(2026, 12, 4, 21, 30, tzinfo=ET)
    assert breaks.plan(FRI, now, 1, b) == (date(2026, 12, 4), date(2026, 12, 4))  # the next free one
    merged = breaks.add(b, date(2026, 12, 4), date(2026, 12, 4))  # touching: one longer break
    assert merged == [{"from": "2026-11-13", "until": "2026-12-04", "why": "between franchises", "by": "Mod"}]
    assert breaks.end(merged, date(2026, 11, 20)) == []  # the current one ends, and later ones go too
    assert breaks.last_showing(FRI, now) == datetime(2026, 11, 6, 21, 30, tzinfo=ET)
    text = breaks.describe(b[0], datetime(2026, 12, 4, 21, 30, tzinfo=ET))
    assert text == "Off Fri, Nov 13 to Fri, Nov 27 (between franchises); back Fri, Dec 4."


def test_start_dates():
    today = date(2026, 11, 7)  # Saturday
    assert parse_from_date("friday", today) == date(2026, 11, 13)
    assert parse_from_date("saturday", today) == today
    assert parse_from_date("11/20", today) == date(2026, 11, 20)
    assert parse_from_date("Jan 2", today) == date(2027, 1, 2)
    assert parse_from_date("2026-11-01", today) is None  # past
    assert parse_from_date("soon", today) is None


def test_polls_close_after_a_break():
    now = datetime(2026, 11, 7, 12, 0, tzinfo=ET)
    b = breaks.add([], date(2026, 11, 13), date(2026, 11, 27))
    closes, hours = poll_close(FRI, now, None, 24, b)
    assert closes == datetime(2026, 12, 4, 21, 30, tzinfo=ET) and hours == 658


def test_a_break_holds_the_franchise_and_the_mods_pick_it(monkeypatch):
    clock = use_clock(monkeypatch, datetime(2026, 11, 6, 21, 31, tzinfo=ET))

    async def go():
        cog, ch = make_cog([fff()])
        q = franchise.new_queue("Alien", franchise.parse_items("✅ Alien (1979)\n✅ Aliens (1986)\nAlien 3 (1992)"))
        q["announced_for"] = "2026-11-06"
        store.save(movienight.STATE, {"fff": {"nominations": [], "polls": [], "queue": q}})
        await cog.run_queues()  # Alien 3 started: the mods are asked
        await confirm(cog, "FFF", "2026-11-06")  # the last part: Alien is finished
        card = [t for t in ch.sent if isinstance(t, dict) and "a break" in t["embed"].title]
        assert len(card) == 1
        labels = [c.item.label for c in card[0]["view"].children]
        assert labels == ["No break", "2 weeks off", "3 weeks off", "4 weeks off"]
        # A mod picks 3 weeks: Nov 13, 20 and 27 are off.
        mod = NS(display_name="Mod", name="mod")
        cog.is_movie_mod = lambda m: True
        sent = {}

        async def edit_message(**kw):
            sent.update(kw)

        button = movienight.BreakButton("fff", 3)
        inter = NS(client=NS(get_cog=lambda n: cog), user=mod, response=NS(edit_message=edit_message))
        await button.callback(inter)
        assert "Fri, Nov 13 to Fri, Nov 27" in sent["content"] and "back Fri, Dec 4" in sent["content"]
        data = store.load(movienight.STATE)["fff"]
        assert data["event_sync"] and cog.next_showing(cog.series("FFF")).date() == date(2026, 12, 4)
        # The next franchise starts, but nothing is announced or asked until Dec 4.
        data["queue"] = franchise.new_queue("Halloween", franchise.parse_items("Halloween (1978)\nHalloween II (1981)"))
        state = store.load(movienight.STATE)
        state["fff"]["queue"] = data["queue"]
        store.save(movienight.STATE, state)
        before = len(posts(ch))
        for day in (13, 20, 27):
            clock["now"] = datetime(2026, 11, day, 16, 0, tzinfo=ET)
            await cog.run_queues()
            clock["now"] = datetime(2026, 11, day, 21, 31, tzinfo=ET)
            await cog.run_queues()
        assert len(posts(ch)) == before
        clock["now"] = datetime(2026, 12, 4, 16, 0, tzinfo=ET)
        await cog.run_queues()
        assert posts(ch)[-1] == "[franchise_next] Halloween (1978)"

    asyncio.run(go())


def test_no_cards_or_voice_for_skipped_nights(monkeypatch):
    use_clock(monkeypatch, datetime(2026, 11, 13, 22, 0, tzinfo=ET))

    async def go():
        cog, ch = make_cog([fff()], voice_check=True)
        s = cog.series("FFF")
        msg = cog.add_break(s, 1, date(2026, 11, 13), "Thanksgiving week", "Mod")
        assert "Fri, Nov 13" in msg and "Thanksgiving week" in msg
        await cog.ask_night(s, "2026-11-13", {"title": "Alien 3"}, "Part 3 of 3 of Alien was on")
        assert movies.load()["pending"] == {}
        cog.bot.home_guild = NS()
        await cog.watch_voice()
        assert "fff" not in store.load(movienight.STATE).get("voice_watch", {})
        assert "back on" in cog.end_break(s, "Mod") and cog.series_breaks(s) == []
        assert "isn't on a break" in cog.end_break(s, "Mod")

    asyncio.run(go())


FFF_RULE = {"start": "2026-11-07T02:30:00+00:00", "end": None, "frequency": 2, "interval": 1, "by_weekday": [5]}


def raw_fff(start):
    return {"id": "777", "name": "FFF", "status": 1, "scheduled_start_time": start, "recurrence_rule": FFF_RULE}


def test_the_discord_event_moves_past_a_break_and_back(monkeypatch):
    async def go():
        brks = breaks.add([], date(2026, 11, 13), date(2026, 11, 27), "between franchises")
        synced = []
        movie = NS(
            event_breaks=lambda name: (NS(name="FFF"), brks, False) if name == "FFF" else None,
            event_synced=lambda s: synced.append(s.name),
        )
        cog = make(None, NS())
        cog.bot.get_cog = lambda n: movie if n == "MovieNight" else None
        cog.bot.features.tz = ET
        cog.bot.http = FakeHttp()
        guild = NS(id=1, me=NS(guild_permissions=NS(manage_events=True)))
        now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
        cog._raw_latest = [raw_fff("2026-11-14T02:30:00+00:00")]  # Fri Nov 13, 9:30 PM: in the break
        await cog._respect_breaks(guild, now)
        ((path, body),) = cog.bot.http.calls
        assert body["scheduled_start_time"] == "2026-12-04T21:30:00-05:00"
        assert body["recurrence_rule"]["by_weekday"] == [5] and synced == ["FFF"]
        # Not in a break and nothing changed: left alone.
        cog._raw_latest = [raw_fff("2026-12-05T02:30:00+00:00")]
        await cog._respect_breaks(guild, now)
        assert len(cog.bot.http.calls) == 1
        # The break ends early: the event comes back to the next regular Friday.
        brks.clear()
        movie.event_breaks = lambda name: (NS(name="FFF"), [], True)
        cog._raw_latest = [
            {
                **raw_fff("2026-12-05T02:30:00+00:00"),
                "recurrence_rule": {**FFF_RULE, "start": "2026-12-05T02:30:00+00:00"},
            }
        ]
        await cog._respect_breaks(guild, now)
        assert cog.bot.http.calls[-1][1]["scheduled_start_time"] == "2026-11-13T21:30:00-05:00"

    asyncio.run(go())


def test_the_next_date_post_mentions_the_break():
    brks = breaks.add([], date(2026, 11, 13), date(2026, 11, 27), "between franchises")
    cog = make(None, NS())
    cog.bot.get_cog = lambda n: NS(event_breaks=lambda name: (NS(name="FFF"), brks, False))
    cog.bot.features.tz = ET
    note = cog._break_note(NS(name="FFF"), datetime(2026, 12, 4, 21, 30, tzinfo=ET))
    assert note == "\n🏖️ Taking a break until then (between franchises)."
    assert cog._break_note(NS(name="FFF"), datetime(2026, 11, 6, 21, 30, tzinfo=ET)) == ""


def test_next_free_date_from_the_rule():
    skip = lambda d: date(2026, 11, 13) <= d <= date(2026, 11, 27)  # noqa: E731
    after = datetime(2026, 11, 8, tzinfo=UTC)
    assert events.next_free(FFF_RULE, after, ET, skip) == datetime(2026, 12, 4, 21, 30, tzinfo=ET)
    late = {**FFF_RULE, "start": "2026-12-05T02:30:00+00:00"}  # before the rule's start is fine too
    assert events.next_free(late, after, ET, lambda d: False) == datetime(2026, 11, 13, 21, 30, tzinfo=ET)
