"""Dashboard security fixes: secrets.env lines, sync conflicts, list imports, approvals, sign-in links, bad
requests, role re-checks and who's-here."""

import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace as NS

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_remote import ORIGIN, PUBLIC, data, remote  # noqa: F401  (data is an autouse fixture)

from pingwin import movieaccess, store
from pingwin.config import ConfigError, data_dir, load_config
from pingwin.configedit import check_secret, save_secrets
from pingwin.dashboard import editing, server
from pingwin.dashboard.auth import SESSION_COOKIE, Sessions
from pingwin.dashboard.server import COOKIE, SESSIONS, create_app

KEY = "k" * 32
OWNER = {"Cookie": f"{COOKIE}={KEY}", "X-Pingwin": "1"}  # on this PC, with the key
INJECTED = "abc\nDISCORD_CLIENT_SECRET=evil\nGIT_CONFIG_COUNT=1\nGIT_CONFIG_KEY_0=core.sshCommand"


class Perms(NS):
    administrator = False
    manage_guild = False
    view_channel = True
    read_message_history = True
    manage_threads = False


def member(uid, name="Riley", roles=("Friend",)):
    return NS(
        id=int(uid),
        display_name=name,
        name=name.lower(),
        bot=False,
        global_name=None,
        roles=[NS(name=r) for r in roles],
        guild_permissions=Perms(),
        display_avatar=NS(with_size=lambda n: NS(url="https://cdn.example/a.png")),
    )


def make_guild(*members, gid=1, name="Club", fetch=None):
    people = {m.id: m for m in members}

    async def fetch_member(uid):
        if fetch is not None:
            raise fetch
        raise discord.NotFound(NS(status=404, reason="Not Found"), {"message": "Unknown Member", "code": 10007})

    return NS(id=gid, name=name, members=list(members), get_member=people.get, fetch_member=fetch_member)


def make_bot(guild=None, full_access=False, cog=None):
    cfg = load_config(require_token=False)
    bot = NS(
        reloaded=0,
        node=NS(name="MAIN-PC"),
        home_guild=guild,
        guilds=[guild] if guild else [],
        get_guild=lambda _id: guild,
        application_id=123,
        config=NS(
            dashboard=NS(remote_full_access=full_access, admin_role=None, port=8787),
            roles=cfg.roles,
            failover=cfg.failover,
            features=cfg.features,
            guild_id=None,
        ),
    )
    bot.reload_settings = lambda: setattr(bot, "reloaded", bot.reloaded + 1)
    bot.get_cog = lambda name: cog if name == "MovieNight" else None
    return bot


def run(scenario, bot=None, routes=()):
    async def go():
        b = bot or make_bot()
        app = create_app(b, KEY, remote=remote(True), sessions=Sessions(data_dir() / "sessions.json"))
        for method, path, handler in routes:
            app.router.add_route(method, path, handler)
        async with TestClient(TestServer(app, host="127.0.0.1")) as client:
            await scenario(client, app)

    asyncio.run(go())


def signed_in(app, level="admin", uid="42", name="Riley"):
    token = app[SESSIONS].create({"id": uid, "name": name, "avatar": None, "level": level})
    return {**PUBLIC, "Cookie": f"{SESSION_COOKIE}={token}", "X-Pingwin": "1", "Origin": ORIGIN}


class FakeCog:
    """Just enough of the MovieNight cog for the movie page handlers."""

    def __init__(self):
        self.calls = []
        self.all_series = [NS(name="General", full_name="")]

    def series(self, name):
        return self.all_series[0] if name else None

    async def details(self, title, year, imdb_id):
        return None

    async def apply(self, kind, series, change, by):
        self.calls.append(("apply", kind, by))
        return "applied"

    async def submit(self, kind, series, change, who):
        self.calls.append(("submit", kind, who.id))
        return "sent"

    async def nominate_text(self, who, s, title, year, imdb_id):
        self.calls.append(("nominate_text", who.id))
        return "ok"

    def import_list(self, series, rows, by, label=""):
        self.calls.append(("import_list", [r["title"] for r in rows], by))
        return {"added": len(rows), "watched": 0, "already": 0}

    async def fill_details(self, n):
        self.calls.append(("fill_details", n))
        return {"message": f"looked up {n}"}


# --------------------------------------------------------------------- 1. secrets.env
def test_secret_values_must_be_one_line():
    assert check_secret("OMDB_API_KEY", "  abc123  ") == "abc123"
    for bad in ("a\nb", "a\rb", "a\x00b", "a\x1bb", "a\x0bb", "a b", "a\u0085b"):
        with pytest.raises(ConfigError, match="line breaks"):
            check_secret("OMDB_API_KEY", bad)
    with pytest.raises(ConfigError):
        check_secret("BAD NAME", "x")
    with pytest.raises(ConfigError):
        save_secrets({"OMDB_API_KEY": INJECTED})
    assert not (data_dir() / "secrets.env").exists()


def test_only_full_access_changes_secrets_and_never_with_extra_lines(monkeypatch):
    monkeypatch.setenv("OMDB_API_KEY", "before")  # (restored afterwards)
    secrets_file = data_dir() / "secrets.env"

    async def scenario(client, app):
        admin = signed_in(app, "admin")
        body = {"section": "movienight", "values": {"OMDB_API_KEY": INJECTED}}
        r = await client.post("/api/features", json=body, headers=admin)
        assert r.status == 403 and "owner" in (await r.json())["error"]
        r = await client.post("/api/features", json={**body, "values": {"OMDB_API_KEY": ""}}, headers=admin)
        assert r.status == 403  # clearing one is a change too
        assert not secrets_file.exists()
        # Saving the rest of the section (secrets just say set / not set) still works without full access.
        r = await client.post("/api/features", json={**body, "values": {"OMDB_API_KEY": True}}, headers=admin)
        assert r.status == 200, await r.text()
        # The owner can't add lines either.
        before = editing.versions()["movienight"]
        r = await client.post("/api/features", json=body, headers=OWNER)
        assert r.status == 400 and "line breaks" in (await r.json())["error"]
        assert not secrets_file.exists() and editing.versions()["movienight"] == before
        r = await client.post("/api/features", json={**body, "values": {"OMDB_API_KEY": " k3y "}}, headers=OWNER)
        assert r.status == 200, await r.text()
        assert secrets_file.read_text(encoding="utf-8") == "OMDB_API_KEY=k3y\n"

    run(scenario)


def test_remote_client_secret_is_checked_before_saving(monkeypatch):
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "before")

    async def scenario(client, app):
        hdr = signed_in(app, "admin")  # full access is on for this bot
        before = editing.versions()["dashboard"]
        body = {"action": "save", "admin_role": "Mods", "client_secret": "s3cret GIT_CONFIG_COUNT=1"}
        r = await client.post("/api/remote", json=body, headers=hdr)
        assert r.status == 400
        assert editing.versions()["dashboard"] == before and not (data_dir() / "secrets.env").exists()
        r = await client.post("/api/remote", json={**body, "client_secret": "s3cret"}, headers=hdr)
        assert r.status == 200, await r.text()
        assert (data_dir() / "secrets.env").read_text(encoding="utf-8") == "DISCORD_CLIENT_SECRET=s3cret\n"

    run(scenario, make_bot(full_access=True))


# --------------------------------------------------------------------- 2. sync conflicts
def _conflicts(root: Path):
    (root / "secrets.env").write_text("DISCORD_TOKEN=real.token\n", encoding="utf-8")
    (root / "secrets.sync-conflict-20261101-120000-ABCDEFG.env").write_text("DISCORD_TOKEN=old\n", encoding="utf-8")
    state = root / "state"
    state.mkdir(exist_ok=True)
    (state / "movie_access.json").write_text('{"codes": {}}', encoding="utf-8")
    (state / "movie_access.sync-conflict-20261101-120000-ABCDEFG.json").write_text(
        '{"codes": {"SIGNIN-CODE-123": {"id": "7"}}}', encoding="utf-8"
    )
    cfg = root / "config.toml"
    (root / "config.sync-conflict-20261101-120000-ABCDEFG.toml").write_text(
        cfg.read_text(encoding="utf-8"), encoding="utf-8"
    )


def test_conflicts_hide_secrets_from_admins_without_full_access(data):  # noqa: F811
    _conflicts(data)
    secret_copy = "secrets.sync-conflict-20261101-120000-ABCDEFG.env"
    access_copy = "state/movie_access.sync-conflict-20261101-120000-ABCDEFG.json"

    async def scenario(client, app):
        admin = signed_in(app, "admin")
        listed = (await (await client.get("/api/conflicts", headers=admin)).json())["conflicts"]
        assert sorted(c["original"] for c in listed) == ["config.toml", "state/movie_access.json"]
        assert "SIGNIN-CODE" not in str(listed) and "DISCORD_TOKEN" not in str(listed)
        assert [c["locked"] for c in listed if c["original"] == "state/movie_access.json"] == [True]
        r = await client.post("/api/presence", json={"tab": "status"}, headers=admin)
        assert (await r.json())["conflicts"] == 2
        for f in (secret_copy, access_copy, "state/../" + secret_copy):
            r = await client.post("/api/conflicts", json={"file": f, "action": "use_other"}, headers=admin)
            assert r.status == 403, f
        assert (data / secret_copy).exists() and (data / access_copy).exists()
        assert "real.token" in (data / "secrets.env").read_text(encoding="utf-8")
        r = await client.post(
            "/api/conflicts",
            json={"file": "config.sync-conflict-20261101-120000-ABCDEFG.toml", "action": "keep_current"},
            headers=admin,
        )
        assert r.status == 200, await r.text()
        # The PC's owner sees (and can sort out) everything.
        listed = (await (await client.get("/api/conflicts", headers=OWNER)).json())["conflicts"]
        assert len(listed) == 2 and "SIGNIN-CODE" in str(listed) and "DISCORD_TOKEN" in str(listed)
        r = await client.post("/api/conflicts", json={"file": secret_copy, "action": "keep_current"}, headers=OWNER)
        assert r.status == 200 and not (data / secret_copy).exists()

    run(scenario)
    with pytest.raises(ConfigError):
        editing.resolve_conflict(access_copy, "keep_current", full_access=False)


# --------------------------------------------------------------------- 3. importing a list from a message
def _channel(cid, guild, perms=None, content="The Fly (1986)\nArrival (2016)", fail=None):
    async def fetch_message(mid):
        if fail is not None:
            raise fail
        return NS(content=content, author=NS(id=900))

    return NS(id=cid, guild=guild, permissions_for=lambda m: perms or Perms(), fetch_message=fetch_message)


def test_list_imports_only_from_channels_the_mod_can_read():
    mod = member(503, "Mo")
    home = make_guild(mod)
    other = NS(id=999, name="Elsewhere")
    cog = FakeCog()
    channels = {
        10: _channel(10, home),
        11: _channel(11, home, perms=Perms(read_message_history=False)),
        12: _channel(12, home, perms=Perms(view_channel=False)),
        20: _channel(20, other, content="ban list draft:\nsecret stuff"),
        13: _channel(
            13,
            home,
            fail=discord.Forbidden(NS(status=403, reason="Forbidden"), {"message": "Missing Access 50001 secret"}),
        ),
    }
    bot = make_bot(home, cog=cog)
    bot.get_channel = channels.get

    async def fetch_channel(cid):
        raise discord.NotFound(NS(status=404, reason="Not Found"), {"message": "Unknown Channel"})

    bot.fetch_channel = fetch_channel

    def link(cid, gid=1):
        return {"action": "import_list", "series": "General", "link": f"https://discord.com/channels/{gid}/{cid}/42"}

    async def scenario(client, app):
        hdr = signed_in(app, "admin", uid="503", name="Mo")
        r = await client.post("/api/movies/admin", json=link(20, gid=1), headers=hdr)  # another server's channel
        assert r.status == 404
        r = await client.post("/api/movies/admin", json=link(77), headers=hdr)  # a channel Pingwin can't find
        assert r.status == 404
        for cid in (11, 12):  # in the server, but the mod can't read it
            r = await client.post("/api/movies/admin", json=link(cid), headers=hdr)
            assert r.status == 403, cid
        r = await client.post("/api/movies/admin", json=link(13), headers=hdr)
        err = (await r.json())["error"]
        assert r.status == 404 and "50001" not in err and "secret" not in err  # Discord's own text isn't echoed
        assert cog.calls == []
        r = await client.post("/api/movies/admin", json=link(10), headers=hdr)
        assert r.status == 200, await r.text()
        assert cog.calls == [("import_list", ["The Fly", "Arrival"], 900)]
        r = await client.post("/api/movies/admin", json=link(20), headers=OWNER)  # not even the owner
        assert r.status == 404
        r = await client.post("/api/movies/admin", json=link(11), headers=OWNER)  # the owner: any channel here
        assert r.status == 200

    run(scenario, bot)


# --------------------------------------------------------------------- 4. approvals when Pingwin can't find you
def test_members_pingwin_cannot_find_are_not_treated_as_the_owner():
    cog = FakeCog()
    bot = make_bot(None, cog=cog)  # not connected: nobody can be looked up

    async def scenario(client, app):
        night = {"kind": "night", "series": "General", "title": "Fake Movie", "date": "2026-11-01"}
        nominate = {"kind": "nominate", "series": "General", "title": "Spam"}
        for level in ("member", "admin"):
            hdr = signed_in(app, level, uid="555")
            r = await client.post("/api/movies/suggest", json=nominate, headers=hdr)
            assert r.status == 403, level
        r = await client.post("/api/movies/suggest", json=night, headers=signed_in(app, "member", uid="555"))
        assert r.status == 403
        assert cog.calls == []
        r = await client.post("/api/movies/suggest", json=nominate, headers=OWNER)
        assert r.status == 200 and cog.calls == [("apply", "nominate", 0)]

    run(scenario, bot)


def test_found_members_still_go_through_approvals():
    cog = FakeCog()
    bot = make_bot(make_guild(member(555)), cog=cog)

    async def scenario(client, app):
        hdr = signed_in(app, "member", uid="555")
        night = {"kind": "night", "series": "General", "title": "A Movie", "date": "2026-11-01"}
        r = await client.post("/api/movies/suggest", json=night, headers=hdr)
        assert r.status == 200
        r = await client.post(
            "/api/movies/suggest", json={"kind": "nominate", "series": "General", "title": "X"}, headers=hdr
        )
        assert r.status == 200
        assert cog.calls == [("submit", "night", 555), ("nominate_text", 555)]

    run(scenario, bot)


@pytest.mark.parametrize("guild, shown", [(make_guild(name="Club"), "Club"), (None, "the dashboard")])
def test_dashboard_suggestions_say_who_added_them(guild, shown):
    from pingwin.features import slug

    store.save("movienight", {slug("General"): {"nominations": [{"id": "a1", "title": "Owner pick", "by": 0}]}})

    async def scenario(client, app):
        body = await (await client.get("/api/movies", headers=signed_in(app, "member"))).json()
        (general,) = [w for w in body["watchlists"] if w["series"] == "General"]
        assert [(i["title"], i["by"]) for i in general["items"]] == [("Owner pick", shown)]

    run(scenario, make_bot(guild, cog=FakeCog()))


# --------------------------------------------------------------------- 5. personal sign-in links
def test_personal_sign_in_needs_the_page_token(monkeypatch):
    async def as_member(bot, uid):
        return "member", "", NS(display_name="Mallory")

    monkeypatch.setattr(server, "check_member", as_member)

    async def scenario(client, app):
        code = movieaccess.new_login_code(777, "Mallory")  # the attacker's own link
        form = {"code": code}
        # Another website auto-submits the form (no-referrer: Origin null), or sends no Origin at all.
        for extra in ({"Origin": "null"}, {}):
            r = await client.post("/movies/login", data=form, headers={**PUBLIC, **extra}, allow_redirects=False)
            assert r.status == 403 and SESSION_COOKIE not in r.cookies
        # A token the attacker got from their own visit doesn't help without this browser's cookie.
        r = await client.get(f"/movies/login?code={code}", headers=PUBLIC)
        token = re.search(r'name="token" value="([^"]+)"', await r.text())[1]
        client.session.cookie_jar.clear()  # (the attacker's browser has it, not the victim's)
        r = await client.post(
            "/movies/login", data={**form, "token": token}, headers={**PUBLIC, "Origin": "null"}, allow_redirects=False
        )
        assert r.status == 403 and SESSION_COOKIE not in r.cookies
        cookie = {"Cookie": f"{server.LOGIN_COOKIE}={token}"}
        r = await client.post(
            "/movies/login", data={**form, "token": "x" * 32}, headers={**PUBLIC, **cookie}, allow_redirects=False
        )
        assert r.status == 403
        # The code wasn't used up by any of that: the real page still works.
        r = await client.post(
            "/movies/login",
            data={**form, "token": token},
            headers={**PUBLIC, **cookie, "Origin": "null"},
            allow_redirects=False,
        )
        assert r.status == 303 and SESSION_COOKIE in r.cookies

    run(scenario)


# --------------------------------------------------------------------- 6. odd request bodies
def test_bad_bodies_are_400_not_500(monkeypatch):
    from pingwin import movies

    monkeypatch.setenv(movies.SECRET, "omdb")
    cog = FakeCog()

    async def boom(request):  # a handler tripping over a value of the wrong kind
        raise TypeError("unhashable type: 'list'")

    async def scenario(client, app):
        hdr = signed_in(app, "admin")
        paths = [
            "/api/movies/review",
            "/api/movies/suggest",
            "/api/movies/admin",
            "/api/presence",
            "/api/features",
            "/api/failover",
            "/api/roles",
            "/api/action",
            "/api/conflicts",
            "/api/watchlist",
            "/api/birthdays",
            "/api/attendance",
        ]
        for path in paths:
            for body in ("[1]", "{oops", '"text"', "null", '{"a": NaN}', ""):
                r = await client.post(path, data=body, headers={**hdr, "Content-Type": "application/json"})
                assert r.status == 400, (path, body, r.status)
                assert "error" in await r.json()
        r = await client.post("/api/movies/admin", json={"action": "lookup", "requests": "abc"}, headers=hdr)
        assert r.status == 400
        r = await client.post("/api/movies/admin", json={"action": "lookup", "requests": 10**9}, headers=hdr)
        assert r.status == 200 and cog.calls == [("fill_details", 1000)]
        r = await client.post("/api/watchlist", json={"series": ["x"], "id": "1", "action": "remove"}, headers=hdr)
        assert r.status == 400
        r = await client.post("/api/movies/admin", json={"action": "import", "sheet_csv": ["x"]}, headers=hdr)
        assert r.status == 400
        r = await client.post("/api/boom", json={}, headers=OWNER)
        assert r.status == 400 and "unhashable" not in (await r.json())["error"]
        r = await client.get("/api/boom", headers=OWNER)
        assert r.status == 500  # reading pages: still a real error

    run(scenario, make_bot(cog=cog), routes=[("POST", "/api/boom", boom), ("GET", "/api/boom", boom)])


# --------------------------------------------------------------------- 7. role re-checks
def test_role_rechecks_only_sign_out_when_discord_says_so():
    trouble = discord.HTTPException(NS(status=503, reason="Unavailable"), "upstream down")

    async def scenario(client, app):
        bot = app[server.BOT]
        hdr = signed_in(app, "member", uid="555")
        item = next(iter(app[SESSIONS].items.values()))

        def check():
            item["checked"] = 0  # due for a re-check
            return client.get("/api/movies", headers=hdr)

        bot.home_guild = None  # not connected to Discord
        r = await check()
        assert r.status == 200 and item["checked"] > time.time() - 300  # kept, asked again in a bit
        bot.home_guild = make_guild(fetch=trouble)  # Discord trouble
        assert (await check()).status == 200
        bot.home_guild = make_guild(member(555, roles=()))  # lost the Friend role
        assert (await check()).status == 401
        hdr = signed_in(app, "member", uid="555")
        item = next(iter(app[SESSIONS].items.values()))
        bot.home_guild = make_guild()  # left the server (fetch_member: Unknown Member)
        assert (await check()).status == 401
        assert app[SESSIONS].items == {}

    run(scenario)


def test_role_recheck_keeps_the_member_when_all_is_well():
    async def scenario(client, app):
        bot = app[server.BOT]
        hdr = signed_in(app, "member", uid="555", name="Old name")
        item = next(iter(app[SESSIONS].items.values()))
        item["checked"] = 0
        bot.home_guild = make_guild(member(555, name="New name"))
        assert (await client.get("/api/movies", headers=hdr)).status == 200
        assert item["name"] == "New name" and item["level"] == "member" and item["checked"] > time.time() - 5

    run(scenario)


# --------------------------------------------------------------------- 8. who's here
def test_members_do_not_see_or_appear_in_presence():
    async def scenario(client, app):
        admin = signed_in(app, "admin", uid="1", name="Ada")
        mem = signed_in(app, "member", uid="2", name="Max")
        r = await client.post("/api/presence", json={"tab": "roles"}, headers=admin)
        assert r.status == 200
        r = await client.post("/api/presence", json={"tab": "features"}, headers=mem)
        body = await r.json()
        assert body["others"] == [] and body["conflicts"] == 0
        r = await client.post("/api/presence", json={"tab": "status"}, headers=OWNER)
        assert [o["name"] for o in (await r.json())["others"]] == ["Ada"]

    run(scenario)
