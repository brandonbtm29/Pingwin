"""Movie pages: member sign-ins only reach /movies, admins can import; every command sits under /pingwin."""

import asyncio
import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from test_remote import ORIGIN, PUBLIC, data, fake_bot, remote  # noqa: F401  (data is an autouse fixture)

from pingwin.cogs.text import PREFIX, title_and_year
from pingwin.dashboard.auth import SESSION_COOKIE, Sessions
from pingwin.dashboard.server import SESSIONS, create_app

KEY = "k" * 32

SHEET = """Date,Movie,Year,Duration (min),Session,IMDB Avg. Rating,Our Avg. Rating,Director(s),Writer(s),Star(s)
1/5/2026,The Fly,1986,96,MMM,7.6,8.0,David Cronenberg,,
"""
DOC = """The Fly
Date watched: 1/5/2026
Sam (8/10): Gross in the best way.
Alex (7/10): Loved the ending.
"""


def run(scenario, tmp):
    async def go():
        bot = fake_bot()
        app = create_app(bot, KEY, remote=remote(True), sessions=Sessions(Path(tmp) / "sessions.json"))
        async with TestClient(TestServer(app, host="127.0.0.1")) as client:
            await scenario(client, app)

    asyncio.run(go())


def headers(app, level):
    token = app[SESSIONS].create({"id": "42", "name": "Riley", "avatar": None, "level": level})
    return {**PUBLIC, "Cookie": f"{SESSION_COOKIE}={token}", "X-Pingwin": "1", "Origin": ORIGIN}


def test_members_only_see_the_movie_pages(tmp_path):
    async def scenario(client, app):
        hdr = headers(app, "member")
        r = await client.get("/api/movies", headers=hdr)
        assert r.status == 200, await r.text()
        body = await r.json()
        assert body["viewer"]["level"] == "member"
        assert body["history"] == []
        r = await client.get("/api/logs", headers=hdr)
        assert r.status == 403
        r = await client.post("/api/failover", json={}, headers=hdr)
        assert r.status == 403
        r = await client.get("/", headers=hdr, allow_redirects=False)
        assert r.status in (302, 303) and r.headers["Location"].endswith("/movies")
        r = await client.get("/movies", headers=hdr)
        assert r.status == 200 and "Movie nights" in await r.text()
        r = await client.post("/api/movies/admin", json={"action": "import", "sheet_csv": SHEET}, headers=hdr)
        assert r.status == 403

    run(scenario, tmp_path)


def test_admins_import_history_from_uploads(tmp_path, data):  # noqa: F811
    async def scenario(client, app):
        hdr = headers(app, "admin")
        payload = {"action": "import", "sheet_csv": SHEET, "doc_text": DOC}
        r = await client.post("/api/movies/admin", json=payload, headers=hdr)
        assert r.status == 200, await r.text()
        assert (await r.json())["stats"]["entries"] == 1
        r = await client.post("/api/movies/admin", json=payload, headers=hdr)  # again: nothing new
        assert (await r.json())["stats"]["entries"] == 0
        body = await (await client.get("/api/movies", headers=hdr)).json()
        (entry,) = body["history"]
        assert entry["movie"]["title"] == "The Fly"
        assert sorted(x["name"] for x in entry["ratings"]) == ["Alex", "Sam"]
        assert entry["avg"] == 7.5

    run(scenario, tmp_path)
    saved = json.loads((data / "state" / "movielib.json").read_text(encoding="utf-8"))
    assert len(saved["history"]) == 1


def test_every_command_is_under_pingwin(data):  # noqa: F811
    from pingwin.bot import EXTENSIONS, Pingwin
    from pingwin.config import load_config

    async def go():
        bot = Pingwin(load_config(require_token=False))
        for ext in EXTENSIONS:
            await bot.load_extension(ext)
        bot._nest_commands()
        top = [c.name for c in bot.tree.get_commands()]
        assert top == ["pingwin"]
        root = bot.tree.get_command("pingwin")
        names = {c.name for c in root.commands}
        assert {"movie", "birthday", "timezone", "rolemenu", "gameday", "attendance", "status"} <= names
        movie = root.get_command("movie")
        assert {"nominate", "rate", "history", "page", "poll"} <= {c.name for c in movie.commands}
        await bot.close()

    asyncio.run(go())


def test_typed_commands():
    assert PREFIX.match("-pingwin movie list MMM")
    assert PREFIX.match("  -Pingwin help")
    assert not PREFIX.match("-pingwinx")
    assert not PREFIX.match("hey -pingwin")
    assert title_and_year(["The", "Thing", "1982"]) == ("The Thing", 1982)
    assert title_and_year(["1917"]) == ("1917", None)
    assert title_and_year(["Alien", "(1979)"]) == ("Alien", 1979)


def test_admins_set_who_ratings_count_as(tmp_path, data):  # noqa: F811
    async def scenario(client, app):
        hdr = headers(app, "admin")
        await client.post(
            "/api/movies/admin", json={"action": "import", "sheet_csv": SHEET, "doc_text": DOC}, headers=hdr
        )
        body = await (await client.get("/api/movies", headers=hdr)).json()
        keys = {r["name"]: r["key"] for r in body["history"][0]["ratings"]}
        people = {keys["Sam"]: "Samantha", keys["Alex"]: "Samantha"}
        r = await client.post("/api/movies/admin", json={"action": "people", "people": people}, headers=hdr)
        assert r.status == 200, await r.text()
        body = await (await client.get("/api/movies", headers=hdr)).json()
        assert {x["who"] for x in body["history"][0]["ratings"]} == {"Samantha"}
        assert {x["name"] for x in body["history"][0]["ratings"]} == {"Sam", "Alex"}  # as written, kept
        member = headers(app, "member")
        r = await client.post("/api/movies/admin", json={"action": "people", "people": people}, headers=member)
        assert r.status == 403

    run(scenario, tmp_path)
