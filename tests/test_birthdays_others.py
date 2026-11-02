"""Birthdays for people without Discord (friends off Discord, celebrities)."""

from datetime import date
from types import SimpleNamespace as NS

from pingwin.cogs.birthdays import other_message, others_due, parse_birthday, watched_with


def test_parse_birthday_formats():
    assert parse_birthday("Sep 2") == (9, 2, None)
    assert parse_birthday("September 2, 1964") == (9, 2, 1964)
    assert parse_birthday("9/2") == (9, 2, None)
    assert parse_birthday("9/2/1964") == (9, 2, 1964)
    assert parse_birthday("1964-09-02") == (9, 2, 1964)
    assert parse_birthday("Feb 29") == (2, 29, None)
    assert parse_birthday("Feb 30") is None and parse_birthday("Smarch 3") is None and parse_birthday("") is None


def test_wished_once_on_the_day():
    others = [NS(name="Keanu Reeves", date="Sep 2, 1964", note="Neo!"), NS(name="Nobody", date="soon", note="")]
    today = date(2026, 9, 2)
    due = others_due(others, {}, today)
    assert [k for k, _ in due] == ["keanu reeves"] and due[0][1]["y"] == 1964
    assert others_due(others, {"keanu reeves": "2026-09-02"}, today) == []
    assert others_due(others, {}, date(2026, 9, 3)) == []


def test_message_with_age_note_and_movie_nights():
    lib = {
        "movies": {
            "m1": {"id": "m1", "title": "The Matrix", "year": 1999, "stars": "Keanu Reeves, Laurence Fishburne"},
            "m2": {"id": "m2", "title": "John Wick", "year": 2014, "stars": "Keanu Reeves\nMichael Nyqvist"},
            "m3": {
                "id": "m3",
                "title": "Speedy",
                "year": 1928,
                "stars": "Harold Lloyd",
                "writer": "Keanu Reeves (story)",
            },
        },
        "history": [{"movie_id": "m1"}, {"movie_id": "m3"}, {"movie_id": "m2"}, {"movie_id": "m1"}],
    }
    films = watched_with(lib, "Keanu Reeves")
    assert films == ["The Matrix (1999)", "John Wick (2014)", "Speedy (1928)"]  # newest first, once each
    assert watched_with(lib, "Keanu") == []  # whole names only
    person = {"name": "Keanu Reeves", "y": 1964, "note": "Neo in The Matrix"}
    text = other_message("🎉 Happy birthday to **{name}**{age}!{note}", person, date(2026, 9, 2), films)
    assert text.startswith("🎉 Happy birthday to **Keanu Reeves**, 62 today! Neo in The Matrix")
    assert text.endswith("We've watched them in The Matrix (1999), John Wick (2014) and Speedy (1928).")
    plain = other_message(
        "Happy birthday, {name}{age}!{note}", {"name": "Grandma Jo", "y": None, "note": ""}, date(2026, 1, 1), []
    )
    assert plain == "Happy birthday, Grandma Jo!"
    gone = other_message(
        "Happy birthday to {name}{age}!", {"name": "Paul Walker", "y": 1973, "died": 2013}, date(2026, 9, 12), []
    )
    assert gone == "Happy birthday to Paul Walker, who would have been 53!"
