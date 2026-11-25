"""Polls that post themselves after movie night, and the new ways Pingwin picks a poll's movies."""

import asyncio
import random
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
from test_movie_fixes import ET, make_cog, mmm, nom, use_clock

from pingwin import movies, store
from pingwin.cogs import movienight
from pingwin.cogs.movienight import pick_movies


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def listed(i, title, lst, watched=None, movie_id=None):
    return {**nom(i, title, watched=watched), "list": lst, "movie_id": movie_id}


def watchlist(rubber_watched=True):
    return [
        listed("h1", "Halloween", "Horror"),
        listed("h2", "Saw", "Horror"),
        listed("h3", "Rubber", "Horror", watched="2026-11-23T20:00:00+00:00" if rubber_watched else None),
        listed("h4", "The Thing", "Horror"),
        listed("n1", "Hitch", "Not horror"),
        listed("n2", "Inception", "Not horror"),
        listed("n3", "Challengers", "Not horror"),
    ]


def test_last_poll_keeps_its_movies_and_swaps_the_watched_one_within_its_list():
    last = ["h1", "h2", "h3", "n1", "gone"]  # Rubber was watched; "gone" was taken off the watchlist
    for seed in range(20):
        ids = [n["id"] for n in pick_movies(watchlist(), "carryover", 10, last=last, rng=random.Random(seed))]
        assert ids[:2] == ["h1", "h2"] and ids[3] == "n1"  # the same order
        assert ids[2] == "h4"  # the only Horror movie left
        assert ids[4] in ("n2", "n3")  # anything that's left
        assert len(ids) == len(set(ids)) == 5
    assert len(pick_movies(watchlist(), "carryover", 3)) == 3  # no last poll: random


def test_random_mixes_by_list_and_by_genre():
    picks = pick_movies(watchlist(), "by_list", 10, mix=[("horror", 2), ("Not Horror", 1)], rng=random.Random(1))
    assert sorted(n["list"] for n in picks) == ["Horror", "Horror", "Not horror"]
    assert "h3" not in {n["id"] for n in picks}  # watched movies never come back
    noms = [listed("a", "A", "", movie_id="m-a"), listed("b", "B", "", movie_id="m-b"), listed("c", "C", "")]
    genres = {"m-a": "Comedy, Romance", "m-b": "Drama"}
    picks = pick_movies(noms, "by_genre", 10, mix=[("comedy", 3), ("Drama", 1)], genres=genres, rng=random.Random(2))
    assert sorted(n["id"] for n in picks) == ["a", "b"]  # C has no genre yet; nothing else is a comedy


def test_the_next_poll_posts_itself_once_the_night_is_answered(monkeypatch):
    clock = use_clock(monkeypatch, datetime(2026, 11, 17, 12, 0, tzinfo=ET))  # a Tuesday

    async def go():
        s = NS(**vars(mmm()), auto_poll=True)
        cog, _ = make_cog(
            [s], auto_poll_minutes=90, auto_pick="carryover", poll_options=6, poll_hours=24, multiple=False, pick_mix=()
        )
        last = {"message_id": 1, "channel_id": 1, "options": ["h1", "h2", "h3", "n1"], "status": "done"}
        last["ends"] = "2026-11-24T01:30:00+00:00"
        store.save(movienight.STATE, {"mmm": {"nominations": watchlist(rubber_watched=False), "polls": [last]}})
        posted = []

        async def post_poll(
            s, picks, hours, question, closes=None, ranked=False, *, by=0, fallback=None, multiple=None, ranks=None
        ):
            posted.append([n["id"] for n in picks])
            return NS(), ""

        cog.post_poll = post_poll
        await cog.run_auto_polls()  # just turned on: last Monday's showing is already over, so not that one
        assert posted == [] and store.load(movienight.STATE)["mmm"]["auto_poll"] == "2026-11-16"
        clock["now"] = datetime(2026, 11, 23, 21, 30, tzinfo=ET)  # an hour after Monday's showtime
        await cog.run_auto_polls()
        assert posted == []  # too soon
        clock["now"] = datetime(2026, 11, 23, 22, 5, tzinfo=ET)
        await cog.ask_night(s, "2026-11-23", {"title": "Rubber", "year": 2010}, "The poll picked", 0)
        await cog.run_auto_polls()
        assert posted == []  # the night's card is still waiting
        (req,) = movies.load()["pending"].values()
        await cog.decide(req["id"], True, NS(display_name="Mod", name="Mod"))  # "Yes, we watched Rubber"
        await cog.run_auto_polls()
        assert posted == [["h1", "h2", "h4", "n1"]]  # Rubber gave way to the only Horror movie left
        await cog.run_auto_polls()
        assert len(posted) == 1  # once per showing

    asyncio.run(go())


def test_no_automatic_poll_while_one_is_running_or_after_a_week_off(monkeypatch):
    clock = use_clock(monkeypatch, datetime(2026, 11, 23, 22, 30, tzinfo=ET))

    async def go():
        s = NS(**vars(mmm()), auto_poll=True)
        cog, _ = make_cog([s], auto_poll_minutes=90, auto_pick="random", poll_options=3, poll_hours=24, pick_mix=())
        running = {"message_id": 2, "channel_id": 1, "options": ["h1", "n1"], "status": "open"}
        running["ends"] = "2026-12-01T01:30:00+00:00"
        state = {"nominations": watchlist(), "polls": [running], "auto_poll": "2026-11-16"}
        store.save(movienight.STATE, {"mmm": state})
        posted = []

        async def post_poll(*a, **kw):
            posted.append(a)
            return NS(), ""

        cog.post_poll = post_poll
        await cog.run_auto_polls()
        assert posted == [] and store.load(movienight.STATE)["mmm"]["auto_poll"] == "2026-11-23"
        state = store.load(movienight.STATE)
        state["mmm"]["polls"] = []
        store.save(movienight.STATE, state)
        cog.add_break(s, 1, datetime(2026, 11, 30).date())  # next Monday is a week off
        clock["now"] = datetime(2026, 11, 30, 22, 30, tzinfo=ET)
        await cog.run_auto_polls()
        assert posted == [] and store.load(movienight.STATE)["mmm"]["auto_poll"] == "2026-11-30"

    asyncio.run(go())
