"""The movie library: importing a log sheet and a ratings document, ratings, and lookups."""

from pingwin import movies

SHEET = """Date,Movie,Year,Duration (min),Session,IMDB Avg. Rating,Our Avg. Rating,Director(s),Writer(s),Star(s)
"Monday, January 29, 2024",Mandy,2018,121,Movie Monday,6.5,4.3,Panos Cosmatos,Panos Cosmatos,Nicolas Cage
"Monday, February 5, 2024",Cube,1997,90,Movie Monday,7.1,8.2,Vincenzo Natali,,Nicole de Boer
"Friday, January 3, 2025",The Fast and the Furious,2001,106,Franchise Fridays,6.8,,Rob Cohen,,Vin Diesel
"Monday, September 14, 2026",La La Land,128,2016,Movie Monday,Not Found,,,,
"Monday, April 15, 2024",The Descent Part 2,2009,94,Bonus B-Movie,5.7,2.3,Jon Harris,,
,The Thing,1982,109,Movie Monday,8.2,,John Carpenter,,Kurt Russell
"Monday, February 17, 2025",Night of the Living Dead,1968,96,Movie Monday,7.8,5.8,,,
"Monday, February 24, 2025",Night of the Living Dead,1990,92,Movie Monday,6.8,4.5,,,
"""

DOC = """Movie Nights

Template 3
Mandy 4
The Cube 5

Template

Date watched:

Alex (/10):

To :

Mandy

Date watched: Monday, January 29, 2024

Sam (5/10): “Wouldn’t watch without friends”

-How many heads out of mayo jars

Alex (4/10): Interesting visuals and color choices.
Second line of the comment.

To Sam: How many goblins out of wasps?

The Cube

Date watched: Monday, February 5, 2024

Sam (8/10): “The cube is spooky.”

Jordan (9/10)- cops/ prime factors

Alex (7.5/10)

Goofy ending!!!

Riley (NA/10): Didn't see most of it

Night of the Living Dead (whatever)

Alex: 5/10
Interesting bunch.
"""


def test_parse_sheet_handles_swapped_columns_and_missing_dates():
    rows = movies.parse_sheet(SHEET)
    assert [r["title"] for r in rows][:2] == ["Mandy", "Cube"]
    assert rows[0]["date"] == "2024-01-29" and rows[0]["group_avg"] == 4.3 and rows[0]["runtime"] == 121
    lalaland = next(r for r in rows if r["title"] == "La La Land")
    assert (lalaland["year"], lalaland["runtime"]) == (2016, 128)
    assert next(r for r in rows if r["title"] == "The Thing")["date"] is None


def test_parse_ratings_doc():
    titles = {movies.norm(r["title"]) for r in movies.parse_sheet(SHEET)}
    secs = movies.parse_ratings_doc(DOC, titles)
    assert [s["title"] for s in secs] == ["Mandy", "The Cube", "Night of the Living Dead (whatever)"]
    mandy = secs[0]
    assert mandy["date"] == "2024-01-29"
    assert [(r["name"], r["score"]) for r in mandy["ratings"]] == [("Sam", 5.0), ("Alex", 4.0)]
    assert "Second line" in mandy["ratings"][1]["comment"]
    assert mandy["ratings"][1]["ask"] == "How many goblins out of wasps?"
    cube = secs[1]
    assert [(r["name"], r["score"]) for r in cube["ratings"]] == [
        ("Sam", 8.0),
        ("Jordan", 9.0),
        ("Alex", 7.5),
        ("Riley", None),
    ]
    assert "Goofy ending" in cube["ratings"][2]["comment"]


def test_import_is_merged_and_repeatable():
    lib = {"movies": {}, "history": [], "pending": {}}
    sheet = movies.parse_sheet(SHEET)
    doc = movies.parse_ratings_doc(DOC, {movies.norm(r["title"]) for r in sheet})
    members = {"111": "Sammy (Sam) sam_the_great", "222": "Alex G alexg"}
    full = {"MMM": "Movie Mondays", "FFF": "Franchise Friday"}
    stats = movies.import_history(lib, sheet, doc, ["MMM", "FFF"], members, full)
    assert stats["entries"] == 8 and stats["ratings"] == 6  # the undated, ambiguous section is skipped
    series = {lib["movies"][e["movie_id"]]["title"]: e["series"] for e in lib["history"]}
    assert series["Mandy"] == "MMM" and series["The Fast and the Furious"] == "FFF"
    assert series["The Descent Part 2"] == "Bonus B-Movie"
    mandy = next(e for e in lib["history"] if lib["movies"][e["movie_id"]]["title"] == "Mandy")
    assert mandy["ratings"]["111"]["score"] == 5.0 and mandy["ratings"]["111"]["user_id"] == "111"
    assert mandy["ratings"]["222"]["answered"] == "How many heads out of mayo jars"  # Sam asked Alex
    assert movies.ratings_avg(mandy) == 4.5
    cube = next(e for e in lib["history"] if lib["movies"][e["movie_id"]]["title"] == "Cube")
    assert "name:jordan" in cube["ratings"]  # no member matched: kept by name
    again = movies.import_history(lib, sheet, doc, ["MMM", "FFF"], members, full)
    assert again["entries"] == 0 and again["ratings"] == 0 and len(lib["history"]) == 8


def test_ratings_and_question_chain():
    lib = {"movies": {}, "history": [], "pending": {}}
    m = movies.upsert_movie(lib, {"title": "Alien", "year": 1979, "runtime": 117})
    e = movies.new_entry(m, "2026-10-23", "FFF")
    assert movies.open_question(e) == ""
    movies.add_rating(e, "1", name="Sam", score=8, comment="Great", ask="How many eggs out of cats?")
    assert movies.open_question(e) == "How many eggs out of cats?"
    movies.add_rating(e, "2", name="Alex", score=6.5, answered="How many eggs out of cats?", answer="3 eggs")
    assert movies.ratings_avg(e) == 7.2
    assert movies.parse_score("7.5/10") == 7.5 and movies.parse_score("11") is None
    assert movies.parse_score("nope") is None


def test_titles_and_ids():
    assert movies.norm("The Thing") == movies.norm("Thing, The") == "thing"
    assert movies.movie_id("The Fly", 1986) == "fly-1986"
    lib = {"movies": {}, "history": [], "pending": {}}
    movies.upsert_movie(lib, {"title": "The Fly", "year": 1986})
    movies.upsert_movie(lib, {"title": "The Fly", "year": 1958})
    assert movies.find_movie(lib, "the fly", 1958)["year"] == 1958
    assert movies.find_movie(lib, "Fly")["title"] == "The Fly"


def test_omdb_fields():
    info = movies.from_omdb(
        {
            "Title": "Alien",
            "Year": "1979",
            "Runtime": "117 min",
            "imdbID": "tt0078748",
            "imdbRating": "8.5",
            "Director": "Ridley Scott",
            "Actors": "Sigourney Weaver",
            "Poster": "N/A",
            "Response": "True",
        }
    )
    assert (info["year"], info["runtime"], info["imdb_rating"], info["poster"]) == (1979, 117, 8.5, None)


TOC_DOC = """Movie ratings
Mandy        2
The Fly (1986)        3
Signs        4
Alien (1979)        5
________________
Template
Date watched:
Name (/10):
* To :
________________
Mandy
Date watched: Monday, January 29, 2024
Sam (5/10): Wild.
-How many goblins out of wasps?
Alex (4/10): Too loud.
________________
The Fly (1986)
Sam (8/10): Gross.
* To Alex: How many teleporters out of baboons?
Alex (7/10): 3/10. Loved it.
* Second thought: the ending.
________________
Sam (9/10): No title on this one.
Alex (8/10): Corn.
________________
Alien (1979) 8.5
Jordan (10/10): Perfect.
"""


def test_ratings_doc_with_separators_and_contents():
    secs = movies.parse_ratings_doc(TOC_DOC)
    assert [s["title"] for s in secs] == ["Mandy", "The Fly (1986)", "Signs", "Alien (1979) 8.5"]
    mandy, fly, signs, alien = secs
    assert mandy["ratings"][0]["ask"] == "How many goblins out of wasps?"
    assert fly["date"] is None and len(fly["ratings"]) == 2
    assert fly["ratings"][0]["ask"] == "How many teleporters out of baboons?"
    assert "the ending" in fly["ratings"][1]["comment"]
    assert [r["name"] for r in signs["ratings"]] == ["Sam", "Alex"]
    assert alien["ratings"][0]["score"] == 10.0


def test_close_titles_match_by_year():
    assert movies._close(movies.norm("Birdemic"), movies.norm("Birdemic: Shock and Terror"))
    assert movies._close(
        movies.norm("Baba Yaga Terror of the Forest"), movies.norm("Baba Yaga: Terror of the Dark Forest")
    )
    assert not movies._close(movies.norm("Alien"), movies.norm("Aliens vs Predator"))
    assert not movies._close(movies.norm("The Fog"), movies.norm("The Fly"))


def test_people_merge_names():
    lib = {"movies": {}, "history": [], "pending": {}, "people": {}}
    e1 = movies.new_entry({"id": "a-2000", "title": "A"}, "2024-01-01", "MMM", source="import")
    e2 = movies.new_entry({"id": "b-2001", "title": "B"}, "2024-01-08", "MMM", source="import")
    movies.add_rating(e1, "name:mrs smith", name="Mrs Smith", score=7)
    movies.add_rating(e2, "name:mrs. smith", name="Mrs. Smith", score=9)
    movies.add_rating(e2, "111", name="sam_discord", score=8, user_id="111")
    lib["history"] += [e1, e2]
    assert movies.rater_names(lib)["111"] == "sam_discord"
    changed = movies.set_people(lib, {"name:mrs smith": "Sam", "name:mrs. smith": " Sam ", "111": "Sam", "nope": "X"})
    assert changed == 3 and "nope" not in lib["people"]
    assert set(movies.rater_names(lib).values()) == {"Sam"}
    assert movies.set_people(lib, {"111": ""}) == 1
    assert movies.rater_names(lib)["111"] == "sam_discord"


def test_parse_list_message():
    rows = movies.parse_list_message(
        "Movies list part 2\n✅ The Fly\n✅Alien (1979)\n🇽 Halloween\n🇽The Pagemaster (1994)\n"
        "🇽Merlin (1998 miniseries, 3 hours)\n🇽 Challengers 2024\n🇽 Zorro (The Mask of)\n🇽1917\n"
    )
    got = [(r["title"], r["year"], r["done"]) for r in rows]
    assert got == [
        ("The Fly", None, True),
        ("Alien", 1979, True),
        ("Halloween", None, False),
        ("The Pagemaster", 1994, False),
        ("Merlin", 1998, False),
        ("Challengers", 2024, False),
        ("Zorro (The Mask of)", None, False),
        ("1917", None, False),
    ]
    assert rows[4]["note"] == "miniseries, 3 hours"
    plain = movies.parse_list_message("Watchlist:\n- Heat\n2. Ronin\n")
    assert [(r["title"], r["done"]) for r in plain] == [("Heat", False), ("Ronin", False)]


class _FakeOmdb:
    """Stands in for aiohttp: answers OMDb-style JSON from a list of responses."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(params)
        answer = self.answers.pop(0)

        class _R:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *a):
                return False

            async def json(self_inner, content_type=None):
                return answer

        return _R()


def test_omdb_lookup_counts_requests_and_spots_limits():
    import asyncio

    import pytest

    found = {"Response": "True", "Title": "The Fly", "Year": "1986", "Genre": "Horror, Sci-Fi", "imdbID": "tt1"}
    spent = [0]
    got = asyncio.run(movies.omdb_lookup(_FakeOmdb([found]), "k", "The Fly", 1986, spent))
    assert got["genre"] == "Horror, Sci-Fi" and spent == [1]
    miss = {"Response": "False", "Error": "Movie not found!"}
    spent = [0]
    assert asyncio.run(movies.omdb_lookup(_FakeOmdb([miss, {"Response": "False"}]), "k", "Nope", None, spent)) is None
    assert spent == [2]
    spent = [0]
    via_search = _FakeOmdb([miss, {"Search": [{"imdbID": "tt9"}]}, found])
    assert asyncio.run(movies.omdb_lookup(via_search, "k", "Fly", None, spent))["imdb_id"] == "tt1"
    assert spent == [3] and via_search.calls[2]["i"] == "tt9"
    with pytest.raises(movies.OmdbLimit):
        asyncio.run(movies.omdb_lookup(_FakeOmdb([{"Response": "False", "Error": "Request limit reached!"}]), "k", "X"))
    with pytest.raises(movies.OmdbKeyError):
        asyncio.run(movies.omdb_lookup(_FakeOmdb([{"Response": "False", "Error": "Invalid API key!"}]), "k", "X"))


def test_details_fill_existing_movies_safely():
    m = {"id": "dr-caligari-1989", "title": "Dr. Caligari", "year": 1989, "director": "Sheet Director"}
    namesake = {"title": "The Cabinet of Dr. Caligari", "year": 1920, "genre": "Horror"}
    assert not movies.plausible(m, namesake)
    info = {
        "title": "Dr Caligari",
        "year": 1989,
        "genre": "Comedy",
        "director": "OMDb Director",
        "imdb_id": "tt2",
        "looked_up": 5.0,
    }
    movies.note_miss(m, 1.0)
    movies.merge_details(m, info)
    assert m["title"] == "Dr. Caligari" and m["id"] == "dr-caligari-1989"  # ours kept
    assert m["director"] == "Sheet Director" and m["genre"] == "Comedy" and m["looked_up"] == 5.0
    assert "omdb_tries" not in m


def test_details_todo_and_retries():
    lib = {"movies": {}, "history": [], "pending": {}, "people": {}}
    old = movies.upsert_movie(lib, {"title": "Old", "year": 2000})
    new = movies.upsert_movie(lib, {"title": "New", "year": 2020})
    done = movies.upsert_movie(lib, {"title": "Done", "year": 2010})
    done["looked_up"] = 1.0
    lib["history"] += [movies.new_entry(old, "2024-01-01", "MMM"), movies.new_entry(new, "2025-01-01", "MMM")]
    watch = {"mmm": {"nominations": [{"id": "n1", "title": "Waiting", "year": None, "watched": None}]}}
    todo = movies.details_todo(lib, watch, now=100.0)
    assert [t[2] if t[0] == "movie" else t[3] for t in todo] == ["New", "Old", "Waiting"]
    movies.note_miss(new, now=100.0)
    assert not movies.needs_details(new, 100.0 + 60)  # missed: wait a week
    assert movies.needs_details(new, 100.0 + movies.RETRY_AFTER + 1)
    new["omdb_tries"] = movies.MAX_TRIES
    assert not movies.needs_details(new, 1e12)  # gave up
    p = movies.details_progress(lib, watch)
    assert p == {"done": 1, "total": 4, "not_found": 1, "left": 2}


def test_people_can_link_discord_members():
    lib = {"movies": {}, "history": [], "pending": {}, "people": {}}
    e = movies.new_entry({"id": "a-2000", "title": "A"}, "2024-01-01", "MMM")
    movies.add_rating(e, "name:mrs smith", name="Mrs Smith", score=7)
    movies.add_rating(e, "111", name="old display name", score=8, user_id="111")
    lib["history"].append(e)
    members = {"111": "Sam", "222": "Alex"}
    assert movies.rater_names(lib, members)["111"] == "Sam"  # their current server name
    assert movies.set_people(lib, {"name:mrs smith": "@222"}) == 1
    assert movies.rater_names(lib, members)["name:mrs smith"] == "Alex"
    assert movies.rater_links(lib) == {"111": "111", "name:mrs smith": "222"}
    movies.set_people(lib, {"name:mrs smith": "@sam"})  # not a user id: just a name
    assert lib["people"]["name:mrs smith"] == "sam"


def test_series_names_from_full_names():
    full = {"MM": "Movie Mondays", "FF": "Franchise Friday"}
    assert movies.series_name("movie monday", ["MM", "FF"], full) == "MM"
    assert movies.series_name("FRANCHISE FRIDAYS", ["MM", "FF"], full) == "FF"
    assert movies.series_name("mm", ["MM", "FF"], full) == "MM"
    assert movies.series_name("Bonus Night", ["MM", "FF"], full) == "Bonus Night"
    assert movies.series_name("", ["MM"], full) == "Other"


QUESTION_DOC = """The Fly
Date watched: 1/5/2026
Sam (8/10): Gross in the best way.
* To Alina: How many teleporters out of ten?
Alex (7/10): Loved the ending.
Alina (9/10): Five teleporters.
To ellie from alina and sebastian: Moms crawling across the ceiling?
Ellie (6/10): Fine.
"""


def test_questions_know_who_asked_whom(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    secs = movies.parse_ratings_doc(QUESTION_DOC)
    sam, alex, alina, ellie = secs[0]["ratings"]
    assert sam["ask_to"] == "Alina" and sam["ask"].startswith("How many teleporters")
    assert alina["ask_to"] == "Ellie" and alina["asked_by"] == "Alina & Sebastian"
    lib = movies.load()
    sheet = [
        {
            "date": "2026-01-05",
            "title": "The Fly",
            "year": 1986,
            "runtime": 96,
            "imdb_rating": 7.6,
            "director": None,
            "writer": None,
            "stars": None,
            "session": "MMM",
            "group_avg": None,
        }
    ]
    movies.import_history(lib, sheet, secs, ["MMM"])
    e = lib["history"][0]
    chain = movies.question_chain(e, movies.rater_names(lib))
    assert chain["name:sam"]["to"] == ["Alina"]
    assert chain["name:alina"]["from"] == "Sam"  # Sam's question skipped Alex and went to Alina
    assert chain["name:alex"]["from"] is None
    assert chain["name:ellie"]["from"] == "Alina & Sebastian"
    # Ratings imported before questions were linked get linked on the next import.
    for r in e["ratings"].values():
        r.pop("answered_from")
        r["answered"] = "wrong"
    movies.import_history(lib, sheet, secs, ["MMM"])
    assert movies.question_chain(e, movies.rater_names(lib)) == chain
    assert e["ratings"]["name:alex"]["answered"] == ""


def test_discord_ratings_remember_who_asked():
    e = {"ratings": {}}
    movies.add_rating(e, "1", name="Sam", score=8, ask="How many flies?")
    asker = movies.open_asker(e, skip="2")
    movies.add_rating(e, "2", name="Alex", score=7, answer="Three", answered="How many flies?", answered_from=asker)
    chain = movies.question_chain(e, {"1": "Sam", "2": "Alex"})
    assert chain["2"]["from"] == "Sam" and chain["1"]["to"] == ["Alex"]
