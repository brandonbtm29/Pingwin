"""Movie pages: member sign-ins only reach /movies, admins can import; every command sits under /pingwin."""

import asyncio
import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from test_remote import ORIGIN, PUBLIC, data, fake_bot, remote  # noqa: F401  (data is an autouse fixture)

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


def test_members_add_and_change_their_own_review(tmp_path, data):  # noqa: F811
    async def scenario(client, app):
        admin = headers(app, "admin")
        await client.post(
            "/api/movies/admin", json={"action": "import", "sheet_csv": SHEET, "doc_text": DOC}, headers=admin
        )
        hid = (await (await client.get("/api/movies", headers=admin)).json())["history"][0]["id"]
        me = headers(app, "member")  # signed in with Discord as user 42, "Riley"
        r = await client.post("/api/movies/review", json={"hid": hid, "score": "11", "comment": "x"}, headers=me)
        assert r.status == 400
        body = {"hid": hid, "score": "8.5", "comment": "Loved the fly.", "ask": "How many teleporters?"}
        r = await client.post("/api/movies/review", json=body, headers=me)
        assert r.status == 200, await r.text()
        r = await client.post("/api/movies/review", json={**body, "score": "9"}, headers=me)
        assert "updated" in (await r.json())["message"]
        got = (await (await client.get("/api/movies", headers=me)).json())["history"][0]["ratings"]
        mine = [x for x in got if x["key"] == "42"]
        assert len(mine) == 1 and mine[0]["score"] == 9.0 and mine[0]["who"] == "Riley"
        assert mine[0]["ask"] == "How many teleporters?"
        r = await client.post("/api/movies/review", json={**body, "as_user": "99"}, headers=me)
        assert r.status == 403  # members can't review for someone else

    run(scenario, tmp_path)


def test_general_watchlist_is_always_there(tmp_path, data):  # noqa: F811
    async def scenario(client, app):
        body = await (await client.get("/api/movies", headers=headers(app, "member"))).json()
        assert body["series"][-1] == "General" and body["series_full"]["General"] == "General"

    run(scenario, tmp_path)


def test_view_only_link_and_personal_sign_in(tmp_path, data, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from pingwin import movieaccess
    from pingwin.dashboard import server

    async def as_member(bot, uid):
        return "member", "", SimpleNamespace(display_name="Riley")

    monkeypatch.setattr(server, "check_member", as_member)

    async def scenario(client, app):
        token = movieaccess.share_token()
        r = await client.get("/movies?view=wrong", headers=PUBLIC, allow_redirects=False)
        assert r.status == 401  # the sign-in page, not the movie pages
        r = await client.get(f"/movies?view={token}", headers=PUBLIC, allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/movies"  # secret leaves the address bar
        cookie = r.cookies["pingwin_view"].value
        guest = {**PUBLIC, "Cookie": f"pingwin_view={cookie}", "X-Pingwin": "1", "Origin": ORIGIN}
        body = await (await client.get("/api/movies", headers=guest)).json()
        assert body["viewer"]["level"] == "viewer"
        r = await client.post("/api/movies/review", json={"hid": "x", "score": "8"}, headers=guest)
        assert r.status == 403 and "shared link" in (await r.json())["error"]
        r = await client.get("/api/logs", headers=guest)
        assert r.status == 401
        movieaccess.reset_share()  # old link stops working
        r = await client.get("/api/movies", headers=guest)
        assert r.status == 401
        code = movieaccess.new_login_code(42, "Riley")
        r = await client.get(f"/movies/login?code={code}", headers=PUBLIC)
        assert r.status == 200 and "Signed in" in await r.text()
        session = r.cookies["pingwin_session"].value
        me = {**PUBLIC, "Cookie": f"pingwin_session={session}", "X-Pingwin": "1", "Origin": ORIGIN}
        body = await (await client.get("/api/movies", headers=me)).json()
        assert body["viewer"]["level"] == "member" and body["viewer"]["user_id"] == "42"
        r = await client.get(f"/movies/login?code={code}", headers=PUBLIC)  # used up
        assert r.status == 403

    run(scenario, tmp_path)
