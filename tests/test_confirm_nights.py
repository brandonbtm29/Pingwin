"""Movie nights join the history only once a movie mod confirms them, /movie watched takes a date, and event
keywords match whole words."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest
from test_movie_fixes import ET, confirm, fff, history, make_cog, mmm, nom, posts, use_clock

from pingwin import franchise, movies, store
from pingwin.cogs import movienight
from pingwin.cogs.events import keyword_in, match_roles
from pingwin.cogs.movienight import parse_watch_date


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def test_watch_dates():
    today = date(2026, 11, 4)  # a Wednesday
    assert parse_watch_date("", today) == today and parse_watch_date("today", today) == today
    assert parse_watch_date("yesterday", today) == date(2026, 11, 3)
    assert parse_watch_date("monday", today) == date(2026, 11, 2)
    assert parse_watch_date("last fri", today) == date(2026, 10, 30)
    assert parse_watch_date("wednesday", today) == today
    assert parse_watch_date("11/2", today) == date(2026, 11, 2)
    assert parse_watch_date("Nov 2", today) == date(2026, 11, 2)
    assert parse_watch_date("Dec 24", today) == date(2025, 12, 24)  # no year: the latest one so far
    assert parse_watch_date("2026-10-26", today) == date(2026, 10, 26)
    assert parse_watch_date("12/25/26", today) is None  # in the future
    assert parse_watch_date("Smarch 3", today) is None and parse_watch_date("2/30", today) is None


def test_a_skipped_franchise_night_stays_next(monkeypatch):
    clock = use_clock(monkeypatch, datetime(2026, 11, 6, 16, 0, tzinfo=ET))

    async def go():
        cog, ch = make_cog([fff()])
        q = franchise.new_queue("Alien", franchise.parse_items("✅ Alien (1979)\nAliens (1986)\nAlien 3 (1992)"))
        store.save(movienight.STATE, {"fff": {"nominations": [], "polls": [], "queue": q}})
        await cog.run_queues()  # 4pm: Aliens announced
        clock["now"] = datetime(2026, 11, 6, 21, 31, tzinfo=ET)
        await cog.run_queues()
        card = next(t for t in ch.sent if isinstance(t, dict))
        assert card["embed"].title.startswith("🎬 Did FFF on Fri, Nov 6 happen?")
        labels = [getattr(c.item, "label", "") for c in card["view"].children]
        assert "Yes, we watched Aliens" in labels and "We skipped it" in labels
        msg = await confirm(cog, "FFF", "2026-11-06", approve=False)
        assert "skipped" in msg and "stays next" in msg and history() == []
        clock["now"] = datetime(2026, 11, 13, 16, 0, tzinfo=ET)
        await cog.run_queues()
        assert posts(ch)[-1] == "[franchise_next] Aliens (1986)"  # the same part, a week later

    asyncio.run(go())


def test_voice_and_the_poll_share_one_card(monkeypatch):
    use_clock(monkeypatch, datetime(2026, 11, 2, 22, 0, tzinfo=ET))

    async def go():
        cog, ch = make_cog([mmm()], voice_check=True, voice_min_people=2, voice_min_minutes=1)
        s = cog.series("MMM")
        await cog.ask_night(s, "2026-11-02", {"title": "Arrival", "year": 2016}, "The poll picked", 0)
        sess = {"day": "2026-11-02", "seconds": {"1": 3600, "2": 3600}, "first_seen": 0, "last_seen": 3600}
        cog.bot.home_guild = NS(get_member=lambda uid: NS(display_name=f"P{uid}"))
        await cog._voice_verdict(s, sess)
        (req,) = movies.load()["pending"].values()
        assert req["data"]["expected"]["title"] == "Arrival" and req["data"]["voice"]["people"] == 2
        assert len([t for t in ch.sent if isinstance(t, dict)]) == 1  # no second card
        await confirm(cog, "MMM", "2026-11-02")
        (e,) = movies.load()["history"]
        assert e["voice"]["people"] == 2 and history() == [("Arrival", "2026-11-02")]
        await cog.ask_night(s, "2026-11-02", {"title": "Arrival"}, "The poll picked")  # already logged: no card
        assert len([t for t in ch.sent if isinstance(t, dict)]) == 1

    asyncio.run(go())


def test_marking_watched_takes_a_date_and_no_rate_it_by_default(monkeypatch):
    use_clock(monkeypatch, datetime(2026, 11, 4, 12, 0, tzinfo=ET))

    async def go():
        cog, _ = make_cog([mmm()], rate=True)
        store.save(movienight.STATE, {"mmm": {"nominations": [nom("a", "Arrival", 2016)], "polls": []}})
        req = {"kind": "watched", "series": "MMM", "data": {"title": "Arrival", "date": "2026-10-26"}}
        assert movienight.describe_request(req) == "Mark **Arrival** as watched (MMM, watched 2026-10-26)"
        msg = await cog.apply(
            "watched", "MMM", {"id": "a", "title": "Arrival", "watched": True, "date": "2026-10-26"}, 1
        )
        assert "2026-10-26" in msg and "Rate it" not in msg
        (e,) = movies.load()["history"]
        assert e["date"] == "2026-10-26" and "rate_at" not in e
        assert store.load(movienight.STATE)["mmm"]["nominations"][0]["watched"].startswith("2026-10-26")

    asyncio.run(go())


def test_keywords_match_whole_words():
    assert keyword_in("art", "Art night") and keyword_in("art", "ARTS and crafts")
    assert not keyword_in("art", "Arthur Christmas") and not keyword_in("art", "smart people")
    assert keyword_in("game", "Game Day") and keyword_in("game", "Board games") and keyword_in("game day", "Gameday!")
    assert not keyword_in("game", "Endgame watch party") and not keyword_in("", "anything")
    assert keyword_in("MMM", "mmm: Movie Monday")
    rules = [NS(role="Movie fans", keywords=["MMM", "movie"]), NS(role="Franchise fans", keywords=["FFF"])]
    # The name decides; the description only counts when the name matches nothing.
    assert match_roles("FFF", rules, "Our Friday movie night") == ["Franchise fans"]
    assert match_roles("Friday hangout", rules, "Bring a movie") == ["Movie fans"]
    assert match_roles("Arthur", [NS(role="Art", keywords=["art"])]) == []
