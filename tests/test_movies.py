"""The movie library: importing a log sheet and a ratings document, ratings, and lookups."""

from pingwin import movies

SHEET = """Date,Movie,Year,Duration (min),Session,IMDB Avg. Rating,Our Avg. Rating,Director(s),Writer(s),Star(s)
"Monday, January 29, 2024",Mandy,2018,121,Movie Monday,6.5,4.3,Panos Cosmatos,Panos Cosmatos,Nicolas Cage
"Monday, February 5, 2024",Cube,1997,90,Movie Monday,7.1,8.2,Vincenzo Natali,,Nicole de Boer
"Friday, January 3, 2025",The Fast and the Furious,2001,106,Film Franchise Fridays,6.8,,Rob Cohen,,Vin Diesel
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
    stats = movies.import_history(lib, sheet, doc, ["MMM", "FFF"], members)
    assert stats["entries"] == 8 and stats["ratings"] == 6  # the undated, ambiguous section is skipped
    series = {lib["movies"][e["movie_id"]]["title"]: e["series"] for e in lib["history"]}
    assert series["Mandy"] == "MMM" and series["The Fast and the Furious"] == "FFF"
    assert series["The Descent Part 2"] == "Bonus B-Movie"
    mandy = next(e for e in lib["history"] if lib["movies"][e["movie_id"]]["title"] == "Mandy")
    assert mandy["ratings"]["111"]["score"] == 5.0 and mandy["ratings"]["111"]["user_id"] == "111"
    assert mandy["ratings"]["222"]["answered"] == ""  # first in the chain
    assert movies.ratings_avg(mandy) == 4.5
    cube = next(e for e in lib["history"] if lib["movies"][e["movie_id"]]["title"] == "Cube")
    assert "name:jordan" in cube["ratings"]  # no member matched: kept by name
    again = movies.import_history(lib, sheet, doc, ["MMM", "FFF"], members)
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
