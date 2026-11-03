"""Movie mods changing watchlists from the movie pages: remove, move, put back."""

import asyncio
from types import SimpleNamespace as NS

import pytest

from pingwin import store
from pingwin.cogs import movienight


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def make_cog():
    async def never():
        await asyncio.Event().wait()

    cfg = NS(
        enabled=True,
        auto_details=False,
        omdb_daily=0,
        series=[NS(name="MMM", full_name="Movie Mondays"), NS(name="FFF")],
    )
    bot = NS(features=NS(movienight=cfg, secret=lambda k: ""), is_active=True, wait_until_ready=never)
    cog = movienight.MovieNight(bot)
    cog.check.cancel()
    cog.fill_loop.cancel()
    return cog


def nom(i, title, watched=None, lst=""):
    return {"id": i, "title": title, "year": None, "by": 1, "watched": watched, "list": lst}


def seed():
    store.save(
        movienight.STATE,
        {
            "mmm": {"nominations": [nom("a", "Arrival", lst="Sci-fi"), nom("b", "Alien", "2026-10-01T00:00:00")]},
            "fff": {"nominations": [nom("c", "Heat")]},
        },
    )


def noms(key):
    return store.load(movienight.STATE).get(key, {}).get("nominations", [])


def test_remove_move_and_put_back():
    async def go():
        seed()
        cog = make_cog()
        assert "Moved **Arrival** to the FFF" in cog.watchlist_change("MMM", "a", "move", "FFF")
        assert [n["id"] for n in noms("mmm")] == ["b"]
        moved = next(n for n in noms("fff") if n["id"] == "a")
        assert moved["list"] == ""  # sub-lists belong to the old watchlist
        assert "back on the Movie Mondays" in cog.watchlist_change("Movie Mondays", "b", "unwatch")
        assert noms("mmm")[0]["watched"] is None
        cog.watchlist_change("FFF", "c", "remove")
        assert [n["id"] for n in noms("fff")] == ["a"]

    asyncio.run(go())


def test_move_to_general_and_refusals():
    async def go():
        seed()
        cog = make_cog()
        cog.watchlist_change("MMM", "a", "move", "General")
        assert [n["id"] for n in noms("general")] == ["a"]
        with pytest.raises(ValueError, match="another watchlist"):
            cog.watchlist_change("MMM", "b", "move", "MMM")
        with pytest.raises(ValueError, match="isn't on the list"):
            cog.watchlist_change("MMM", "zzz", "remove")
        store.save(
            movienight.STATE, {"mmm": {"nominations": [nom("x", "Heat")]}, "fff": {"nominations": [nom("y", "heat")]}}
        )
        with pytest.raises(ValueError, match="already on the FFF"):
            cog.watchlist_change("MMM", "x", "move", "FFF")
        with pytest.raises(ValueError, match="Unknown"):
            cog.watchlist_change("MMM", "x", "explode")

    asyncio.run(go())
