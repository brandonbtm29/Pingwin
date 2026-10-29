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
        assert (await cog.fill_details(8))["looked"] == 2  # this run's share (up to 4 requests a movie)
        lib = movies.load()
        lib["omdb"]["used"] = 700
        movies.save(lib)
        assert (await cog.fill_details(100))["looked"] == 0  # the last eighth is kept for title searches
        assert cog.search_budget() == {"used": 700, "daily": 800, "left": 100}
        assert cog._search_warning() == ""
        lib = movies.load()
        lib["omdb"]["used"] = 730
        movies.save(lib)
        assert "only 70 of today's 800" in cog._search_warning()

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


def test_hand_matching_fixes_movies_and_watchlist_items(monkeypatch):
    info = {"title": "Arrival", "year": 2016, "imdb_id": "tt2543164", "poster": "https://x/a.jpg", "genre": "Drama"}

    async def by_id(http, key, title, year=None, spent=None, imdb_id=None):
        spent[0] += 1
        return dict(info) if imdb_id == "tt2543164" else None

    monkeypatch.setattr(movies, "omdb_lookup", by_id)

    async def go():
        seed()
        lib = movies.load()
        fly = movies.find_movie(lib, "The Fly", 1986)
        fly.update(imdb_id="tt0000001", genre="Wrong", omdb_tries=0)
        cal = movies.find_movie(lib, "Dr. Caligari", 1989)
        cal["omdb_tries"] = 3
        movies.save(lib)
        state = store.load(movienight.STATE)
        state["mmm"]["nominations"][0]["omdb_tries"] = 3
        store.save(movienight.STATE, state)
        um = movies.unmatched(movies.load(), store.load(movienight.STATE))
        assert [(x["kind"], x["title"]) for x in um] == [("movie", "Dr. Caligari"), ("nom", "Arrival")]
        cog = make_cog()
        with pytest.raises(ValueError):
            await cog.match_imdb("movie", fly["id"], "not-an-id")
        msg = await cog.match_imdb("nom", "n1", "tt2543164", "mmm")
        assert "Arrival" in msg
        n = store.load(movienight.STATE)["mmm"]["nominations"][0]
        m = movies.load()["movies"][n["movie_id"]]
        assert m["imdb_id"] == "tt2543164" and m["poster"] == "https://x/a.jpg" and n["year"] == 2016
        await cog.match_imdb("movie", fly["id"], "tt2543164")  # a wrong match is replaced
        fixed = movies.load()["movies"][fly["id"]]
        assert fixed["imdb_id"] == "tt2543164" and fixed["genre"] == "Drama" and fixed["title"] == "The Fly"
        lib, watch = movies.load(), store.load(movienight.STATE)
        assert movies.retry_unmatched(lib, watch) == 1 and "omdb_tries" not in lib["movies"][cal["id"]]

    asyncio.run(go())


def test_lookup_falls_back_to_a_year_off(monkeypatch):
    asked = []

    class Resp:
        def __init__(self, d):
            self.d = d

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            return self.d

    class Http:
        def get(self, url, params):
            asked.append({k: v for k, v in params.items() if k != "apikey"})
            if "i" in params:
                return Resp({"Response": "True", "Title": "Kill List", "Year": "2011", "imdbID": params["i"]})
            if "s" in params and "y" not in params:
                hits = [
                    {"Title": "Kill List", "Year": "2011", "imdbID": "tt1788391"},
                    {"Title": "Kill List 2", "Year": "2030"},
                ]
                return Resp({"Response": "True", "Search": hits})
            return Resp({"Response": "False", "Error": "Movie not found!"})

    info = asyncio.run(movies.omdb_lookup(Http(), "k", "Kill List", 2012))
    assert info["imdb_id"] == "tt1788391" and len(asked) == 4
    assert movies.imdb_id_in("https://www.imdb.com/title/tt1788391/?ref_=x") == "tt1788391"


def test_adding_a_past_movie_night(monkeypatch):
    async def lookup(http, key, title, year=None, spent=None, imdb_id=None):
        spent[0] += 1
        return (
            {"title": "Arrival", "year": 2016, "imdb_id": "tt2543164", "looked_up": 1} if title == "Arrival" else None
        )

    monkeypatch.setattr(movies, "omdb_lookup", lookup)

    async def go():
        seed()
        cog = make_cog()
        s = cog.series("MMM")
        e, created = await cog.add_night(s, "Arrival", None, "tt2543164", "2025-12-01")
        assert created and e["date"] == "2025-12-01" and e["series"] == "MMM"
        lib = movies.load()
        assert lib["movies"][e["movie_id"]]["imdb_id"] == "tt2543164"
        assert [x["date"] for x in lib["history"]] == sorted(x["date"] for x in lib["history"])  # in date order
        n = store.load(movienight.STATE)["mmm"]["nominations"][0]
        assert n["watched"].startswith("2025-12-01")  # it came off the watchlist
        again, created = await cog.add_night(s, "Arrival", 2016, None, "2025-12-01")
        assert not created and again["id"] == e["id"]
        other, created = await cog.add_night(s, "Some Indie Film", 2001, None, "2025-12-08")
        assert created and movies.load()["movies"][other["movie_id"]]["title"] == "Some Indie Film"

    asyncio.run(go())


def test_movie_night_requests_and_the_movie_mod_role(monkeypatch):
    async def lookup(http, key, title, year=None, spent=None, imdb_id=None):
        return None

    monkeypatch.setattr(movies, "omdb_lookup", lookup)

    async def go():
        seed()
        cog = make_cog()
        req = {"kind": "night", "series": "MMM", "data": {"title": "Blade Runner", "year": 1982, "date": "2026-10-12"}}
        assert movienight.describe_request(req) == "Add **Blade Runner** (1982) to the MMM history, watched 2026-10-12"
        msg = await cog.apply("night", "MMM", req["data"], 1)
        assert "Added **Blade Runner (1982)**" in msg
        assert "already in the history" in await cog.apply("night", "MMM", req["data"], 1)
        # The movie mods role counts as a mod for movie night only.
        cog.cfg.mod_role = "Movie Mod"
        seen = []
        cog.bot.is_mod = lambda member, role=None: seen.append(role) or False
        assert cog.is_movie_mod(object()) is False and seen == ["Movie Mod"]

    asyncio.run(go())


def test_history_fixes_correct_the_movie_in_place():
    async def go():
        seed()
        lib = movies.load()
        fly = movies.find_movie(lib, "The Fly", 1986)
        fly.update(imdb_id="tt0091064", poster="https://x/fly.jpg")
        movies.save(lib)
        hid = next(e["id"] for e in lib["history"] if e["movie_id"] == fly["id"])
        cog = make_cog()
        cog._apply_edit({"hid": hid, "changes": {"year": 1987}})  # a year fix keeps the poster and IMDb link
        m = movies.load()["movies"][fly["id"]]
        assert m["year"] == 1987 and m["imdb_id"] == "tt0091064" and m["poster"] == "https://x/fly.jpg"
        cog._apply_edit(
            {"hid": hid, "changes": {"title": "Dr. Caligari", "year": 1989}}
        )  # an existing movie: point at it
        lib = movies.load()
        e = next(x for x in lib["history"] if x["id"] == hid)
        assert lib["movies"][e["movie_id"]]["title"] == "Dr. Caligari"

    asyncio.run(go())


def test_a_night_noticed_in_voice_goes_to_the_movie_mods(monkeypatch):
    from zoneinfo import ZoneInfo

    async def lookup(http, key, title, year=None, spent=None, imdb_id=None):
        return None

    monkeypatch.setattr(movies, "omdb_lookup", lookup)

    async def go():
        seed()
        cog = make_cog()
        vars(cog.cfg).update(
            voice_min_people=2, voice_min_minutes=30, rate=False, approval_channel=None, polls_channel=None
        )
        cog.bot.features.tz = ZoneInfo("America/New_York")
        cog.bot.features.welcome = NS(approval_channel=None)
        cog.bot.text_channel = lambda cid: None
        cog.bot.home_guild = None
        s = cog.series("MMM")
        sess = {
            "day": "2026-10-19",
            "seconds": {"1": 3600, "2": 3000, "3": 600},
            "first_seen": 0,
            "last_seen": 5400,
            "peak": 3,
        }
        await cog._voice_verdict(s, sess)
        (req,) = movies.load()["pending"].values()
        assert req["kind"] == "detected" and req["data"]["voice"]["people"] == 2
        assert [c["title"] for c in req["data"]["candidates"]] == ["Arrival"]  # from the watchlist
        assert "Which movie was it?" in movienight.describe_request(req)
        await cog._voice_verdict(s, sess)  # the same night isn't asked twice
        assert len(movies.load()["pending"]) == 1
        assert "Pick a movie" in await cog.decide(req["id"], True, NS(display_name="Riley", name="riley"))
        msg = await cog.decide(req["id"], True, NS(display_name="Riley", name="riley"), choice=0)
        assert "logged **Arrival**" in msg
        lib = movies.load()
        e = next(x for x in lib["history"] if x["date"] == "2026-10-19")
        assert e["voice"]["people"] == 2 and lib["movies"][e["movie_id"]]["title"] == "Arrival"
        # A night that's already logged just gets its turnout noted; too few people means no card.
        await cog._voice_verdict(s, {**sess, "seconds": {"1": 3600, "2": 3600, "4": 3600}})
        assert len(movies.load()["pending"]) == 1
        await cog._voice_verdict(s, {**sess, "day": "2026-10-26", "seconds": {"1": 3600}})
        assert len(movies.load()["pending"]) == 1
        # We skipped it.
        await cog._voice_verdict(s, {**sess, "day": "2026-11-02"})
        rid = next(r["id"] for r in movies.load()["pending"].values() if r["data"]["date"] == "2026-11-02")
        assert "skipped" in await cog.decide(rid, False, NS(display_name="Riley", name="riley"))
        assert not any(x["date"] == "2026-11-02" for x in movies.load()["history"])

    asyncio.run(go())
