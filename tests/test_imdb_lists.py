"""IMDb list exports compared with the history and watchlists."""

import pytest

from pingwin import imdblists, movies

CSV = (
    "﻿Position,Const,Created,Modified,Description,Title,Original Title,URL,Title Type,IMDb Rating,"
    "Runtime (mins),Year,Genres,Num Votes,Release Date,Directors,Your Rating,Date Rated\n"
    "1,tt0091064,2025-02-01,2025-02-01,,The Fly,The Fly,https://www.imdb.com/title/tt0091064/,Movie,7.6,96,1986,"
    '"Drama, Horror, Sci-Fi",200000,1986-08-15,David Cronenberg,9,2025-02-01\n'
    "2,tt2543164,2025-03-01,2025-03-01,,Arrival,Arrival,https://www.imdb.com/title/tt2543164/,Movie,7.9,116,2016,"
    "Drama,700000,2016-11-11,Denis Villeneuve,,\n"
    "3,tt0063350,2025-04-01,2025-04-01,,Night of the Living Dead,,,Movie,7.8,96,1968,Horror,100000,,"
    "George A. Romero,,\n"
    "4,tt0903747,2025-05-01,2025-05-01,,Breaking Bad,,,TV Series,9.5,49,2008,Crime,2000000,,,,\n"
)


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))


def test_parse_skips_tv_and_reads_the_columns():
    items = imdblists.parse_csv(CSV)
    assert [i["title"] for i in items] == ["The Fly", "Arrival", "Night of the Living Dead"]
    fly = items[0]
    assert fly["imdb_id"] == "tt0091064" and fly["year"] == 1986 and fly["runtime"] == 96
    assert fly["your_rating"] == 9 and fly["added"] == "2025-02-01" and fly["genre"] == "Drama, Horror, Sci-Fi"
    with pytest.raises(ValueError):
        imdblists.parse_csv("a,b\n1,2\n")


def test_compare_and_fill_details():
    lib = movies.load()
    fly = movies.upsert_movie(lib, {"title": "The Fly", "year": 1986})
    alien = movies.upsert_movie(lib, {"title": "Alien", "year": 1979})
    lib["history"] += [movies.new_entry(fly, "2026-01-05", "MMM"), movies.new_entry(alien, "2026-01-12", "MMM")]
    res = imdblists.save_list(lib, "Horror log", "https://www.imdb.com/list/ls012345678/", imdblists.parse_csv(CSV))
    assert res["id"] == "ls012345678" and res["items"] == 3 and res["filled"] == 1
    assert lib["movies"][fly["id"]]["imdb_id"] == "tt0091064" and lib["movies"][fly["id"]]["genre"]
    noms = {"MMM": [{"id": "n1", "title": "Arrival", "year": 2016, "watched": None}]}
    out = imdblists.compare(lib, lib["imdb_lists"]["ls012345678"], noms)
    assert out["counts"] == {"total": 3, "watched": 1, "listed": 1, "neither": 1}
    by = {i["title"]: i for i in out["items"]}
    assert by["The Fly"]["watched"][0]["date"] == "2026-01-05"
    assert by["Arrival"]["lists"] == ["MMM"]
    assert [m["title"] for m in out["missing"]] == ["Alien"]


def test_bad_links_are_dropped_and_names_make_ids():
    lib = movies.load()
    res = imdblists.save_list(lib, "  My   list ", "javascript:alert(1)", imdblists.parse_csv(CSV))
    assert res["id"] == "n-mylist" and lib["imdb_lists"]["n-mylist"]["url"] == ""
