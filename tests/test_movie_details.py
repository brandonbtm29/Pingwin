"""The background movie-details filler: budget, merging into existing movies, watchlist links, pausing."""

import asyncio
from types import SimpleNamespace as NS

import pytest

from pingwin import movies, store
from pingwin.cogs import movienight


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def make_cog(daily=800):
    async def never():
        await asyncio.Event().wait()

    cfg = NS(enabled=True, auto_details=True, omdb_daily=daily, series=[NS(name="MMM")])
    bot = NS(features=NS(movienight=cfg, secret=lambda k: "key"), is_active=True, wait_until_ready=never)
    cog = movienight.MovieNight(bot)
    cog.check.cancel()
    cog.fill_loop.cancel()
    cog._http = lambda: None  # OMDb is faked in these tests
    return cog


def seed():
    lib = movies.load()
    fly = movies.upsert_movie(lib, {"title": "The Fly", "year": 1986, "director": "From the sheet"})
    cal = movies.upsert_movie(lib, {"title": "Dr. Caligari", "year": 1989})
    lib["history"] += [movies.new_entry(fly, "2024-01-01", "MMM"), movies.new_entry(cal, "2024-01-08", "MMM")]
    movies.save(lib)
    store.save(
        movienight.STATE, {"mmm": {"nominations": [{"id": "n1", "title": "Arrival", "year": None, "watched": None}]}}
    )


ANSWERS = {
    "The Fly": {"title": "The Fly", "year": 1986, "genre": "Horror", "director": "OMDb", "looked_up": 1.0},
    "Dr. Caligari": {"title": "The Cabinet of Dr. Caligari", "year": 1920, "genre": "Horror", "looked_up": 1.0},
    "Arrival": {"title": "Arrival", "year": 2016, "genre": "Sci-Fi", "poster": "https://x/p.jpg", "looked_up": 1.0},
}


def test_fill_details_merges_links_and_counts(monkeypatch):
    async def fake(http, key, title, year=None, spent=None, imdb_id=None):
        spent[0] += 2
        return ANSWERS.get(title)

    monkeypatch.setattr(movies, "omdb_lookup", fake)

    async def go():
        seed()
        cog = make_cog()
        res = await cog.fill_details(100)
        assert res["looked"] == 3 and res["found"] == 2
        lib = movies.load()
        fly = movies.find_movie(lib, "The Fly", 1986)
        assert fly["genre"] == "Horror" and fly["director"] == "From the sheet" and fly["looked_up"]
        cal = movies.find_movie(lib, "Dr. Caligari", 1989)
        assert not cal.get("looked_up") and cal["omdb_tries"] == 1  # 1920 namesake rejected
        assert len(lib["movies"]) == 3  # Arrival added; no duplicate Caligari
        n = store.load(movienight.STATE)["mmm"]["nominations"][0]
        assert lib["movies"][n["movie_id"]]["poster"] == "https://x/p.jpg" and n["year"] == 2016
        assert lib["omdb"]["used"] == 6
        again = await cog.fill_details(100)  # nothing left that's due
        assert again["looked"] == 0

    asyncio.run(go())


def test_fill_details_respects_budgets(monkeypatch):
    async def fake(http, key, title, year=None, spent=None, imdb_id=None):
        spent[0] += 3
        return ANSWERS.get(title)

    monkeypatch.setattr(movies, "omdb_lookup", fake)

    async def go():
        seed()
        cog = make_cog(daily=800)
        assert (await cog.fill_details(6))["looked"] == 2  # this run's share
        lib = movies.load()
        lib["omdb"]["used"] = 798
        movies.save(lib)
        assert (await cog.fill_details(100))["looked"] == 0  # today's budget

    asyncio.run(go())


def test_limit_pauses_until_tomorrow(monkeypatch):
    async def limited(http, key, title, year=None, spent=None, imdb_id=None):
        spent[0] += 1
        raise movies.OmdbLimit("Request limit reached!")

    monkeypatch.setattr(movies, "omdb_lookup", limited)

    async def go():
        seed()
        cog = make_cog()
        res = await cog.fill_details(100)
        assert res["looked"] == 0 and "limit" in res["message"]
        assert "limit" in (await cog.fill_details(100))["message"]  # still paused, no new requests
        assert movies.load()["omdb"]["used"] == 1

    asyncio.run(go())


def test_search_suggests_library_then_omdb_and_caches(monkeypatch):
    calls = []

    async def fake_search(http, key, query, year=None, spent=None):
        calls.append(query)
        spent[0] += 1
        return [{"title": "The Fly II", "year": 1989, "imdb_id": "tt0097368", "poster": None}]

    monkeypatch.setattr(movies, "omdb_search", fake_search)

    async def go():
        seed()
        cog = make_cog()
        res = await cog.search("the fly")
        assert [h["title"] for h in res["library"]] == ["The Fly"] and res["library"][0]["watched"]
        assert res["omdb"][0]["imdb_id"] == "tt0097368"
        again = await cog.search("The  Fly")  # same words: from the cache
        assert again["omdb"] == res["omdb"] and calls == ["the fly"]
        assert movies.load()["omdb"]["used"] == 1
        short = await cog.search("fl")  # too short for OMDb, library only
        assert short["omdb"] == [] and calls == ["the fly"]

    asyncio.run(go())


def test_search_without_a_key_says_so():
    async def go():
        seed()
        cog = make_cog()
        cog.bot.features.secret = lambda k: ""
        res = await cog.search("arrival")
        assert res["omdb"] == [] and "OMDb API key" in res["note"]

    asyncio.run(go())


def test_picked_suggestion_carries_the_imdb_id():
    m = movienight.PICKED.fullmatch("tt0097368|The Fly II|1989")
    assert m["id"] == "tt0097368" and m["title"] == "The Fly II" and m["year"] == "1989"
    assert movienight.PICKED.fullmatch("|Some Indie|") is not None
    assert movienight.PICKED.fullmatch("just typed a title") is None
