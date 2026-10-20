"""Remote dashboard: Funnel requests, Discord sessions, edit warnings, sync conflicts, multi-menus."""

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp.test_utils import TestClient, TestServer

from pingwin.config import ConfigError, MenuConfig, MenuRole, load_config
from pingwin.dashboard import editing, tailscale
from pingwin.dashboard.auth import SESSION_COOKIE, STATE_COOKIE, Sessions
from pingwin.dashboard.server import COOKIE, SESSIONS, Remote, create_app

ROOT = Path(__file__).resolve().parents[1]
KEY = "k" * 32
HOST = "pc.tail1234.ts.net"
PUBLIC = {"Host": f"{HOST}:10000", "Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "203.0.113.9"}
ORIGIN = f"https://{HOST}:10000"


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text((ROOT / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    monkeypatch.setenv("PINGWIN_CONFIG", str(p))
    return tmp_path


def fake_bot(full_access=False):
    cfg = load_config(require_token=False)
    bot = SimpleNamespace(
        reloaded=0,
        node=SimpleNamespace(name="MAIN-PC"),
        home_guild=None,
        guilds=[],
        get_guild=lambda _id: None,
        application_id=123,
        config=SimpleNamespace(
            dashboard=SimpleNamespace(remote_full_access=full_access, admin_role=None, port=8787),
            roles=cfg.roles,
            failover=cfg.failover,
            features=cfg.features,
            guild_id=None,
        ),
    )
    bot.reload_settings = lambda: setattr(bot, "reloaded", bot.reloaded + 1)
    return bot


def remote(online=True):
    r = Remote(8787)
    r.status = tailscale.Status(installed=True, running=True, dns_name=HOST, funnel_on=online)
    return r


def run(scenario, *, online=True, full_access=False, tmp=None):
    async def go():
        bot = fake_bot(full_access)
        sessions = Sessions(Path(tmp or ".") / "sessions.json")
        app = create_app(bot, KEY, remote=remote(online), sessions=sessions)
        async with TestClient(TestServer(app, host="127.0.0.1")) as client:
            await scenario(client, app)

    asyncio.run(go())


def session_for(app, name="Jordan"):
    return app[SESSIONS].create({"id": "42", "name": name, "avatar": None})


def test_funnel_requests_are_refused_when_offline(tmp_path):
    async def scenario(client, app):
        r = await client.get("/", headers=PUBLIC)
        assert r.status == 403

    run(scenario, online=False, tmp=tmp_path)


def test_public_visitors_get_discord_sign_in_and_the_key_never_works(tmp_path):
    async def scenario(client, app):
        r = await client.get("/", headers=PUBLIC)
        assert r.status == 401 and "isn't set up yet" in await r.text()
        os.environ["DISCORD_CLIENT_SECRET"] = "s3cret"
        try:
            r = await client.get("/", headers=PUBLIC)
            assert "Sign in with Discord" in await r.text()
            r = await client.get("/auth/login", headers=PUBLIC, allow_redirects=False)
            assert r.status == 302 and r.headers["Location"].startswith("https://discord.com/oauth2/authorize")
            query = parse_qs(urlparse(r.headers["Location"]).query)
            assert query["redirect_uri"] == ["https://pc.tail1234.ts.net:10000/auth/callback"]
            assert query["scope"] == ["identify"] and STATE_COOKIE in r.cookies
        finally:
            os.environ.pop("DISCORD_CLIENT_SECRET")
        r = await client.get("/api/state", headers={**PUBLIC, "Cookie": f"{COOKIE}={KEY}"})
        assert r.status == 401
        r = await client.get(f"/login?key={KEY}", headers=PUBLIC, allow_redirects=False)
        assert r.status == 401
        r = await client.get("/api/logs", headers={**PUBLIC, "Host": "evil.example"})
        assert r.status == 403
        r = await client.get("/static/style.css", headers=PUBLIC)
        assert r.status == 200

    run(scenario, tmp=tmp_path)


def test_bad_oauth_state_is_refused(tmp_path):
    async def scenario(client, app):
        r = await client.get("/auth/callback?code=x&state=y", headers=PUBLIC)
        assert "expired" in await r.text()

    run(scenario, tmp=tmp_path)


def test_discord_sessions_can_edit_but_not_pair(tmp_path):
    async def scenario(client, app):
        token = session_for(app)
        hdr = {**PUBLIC, "Cookie": f"{SESSION_COOKIE}={token}", "X-Pingwin": "1"}
        r = await client.get("/api/logs", headers=hdr)
        assert r.status == 200
        body = {"enabled": False, "status_channel_id": "", "pcs": ["MAIN-PC"]}
        r = await client.post("/api/failover", json=body, headers=hdr)  # no Origin: refused
        assert r.status == 403
        r = await client.post("/api/failover", json=body, headers={**hdr, "Origin": "https://evil.example"})
        assert r.status == 403
        r = await client.post("/api/failover", json=body, headers={**hdr, "Origin": ORIGIN})
        assert r.status == 200, await r.text()
        assert editing.last_edits()["failover"]["who"] == "Jordan"
        r = await client.post("/api/action", json={"action": "pair_invite"}, headers={**hdr, "Origin": ORIGIN})
        assert r.status == 403
        r = await client.post("/api/remote", json={"action": "funnel_off"}, headers={**hdr, "Origin": ORIGIN})
        assert r.status == 403
        r = await client.post("/api/action", json={"action": "stop"}, headers={**hdr, "Origin": ORIGIN})
        assert r.status == 403
        r = await client.post("/api/signout", json={}, headers={**hdr, "Origin": ORIGIN})
        assert r.status == 200
        r = await client.get("/api/logs", headers=hdr)
        assert r.status == 401

    run(scenario, tmp=tmp_path)


def test_stale_saves_are_stopped(tmp_path):
    async def scenario(client, app):
        hdr = {"Cookie": f"{COOKIE}={KEY}", "X-Pingwin": "1"}
        old = editing.versions()["failover"]
        body = {"enabled": False, "status_channel_id": "", "pcs": ["A-PC"], "base_version": old}
        r = await client.post("/api/failover", json=body, headers=hdr)
        assert r.status == 200
        body["pcs"] = ["B-PC"]  # still based on the old version
        r = await client.post("/api/failover", json=body, headers=hdr)
        assert r.status == 409
        data = await r.json()
        assert data["stale"] and "MAIN-PC" in data["error"]
        body["base_version"] = editing.versions()["failover"]
        r = await client.post("/api/failover", json=body, headers=hdr)
        assert r.status == 200

    run(scenario, tmp=tmp_path)


def test_presence_shows_other_people(tmp_path):
    async def scenario(client, app):
        token = session_for(app, "Jordan")
        owner = {"Cookie": f"{COOKIE}={KEY}", "X-Pingwin": "1"}
        guest = {**PUBLIC, "Cookie": f"{SESSION_COOKIE}={token}", "X-Pingwin": "1", "Origin": ORIGIN}
        r = await client.post("/api/presence", json={"tab": "roles", "dirty": True}, headers=guest)
        assert (await r.json())["others"] == []
        r = await client.post("/api/presence", json={"tab": "roles", "dirty": False}, headers=owner)
        others = (await r.json())["others"]
        assert [(o["name"], o["tab"], o["dirty"]) for o in others] == [("Jordan", "roles", True)]

    run(scenario, tmp=tmp_path)


def test_presence_from_other_pcs(tmp_path):
    other = editing.Presence("BACKUP-PC")
    other.ping(SimpleNamespace(sid="s1", name="Jordan", avatar=None, kind="owner"), "features", True)
    me = editing.Presence("MAIN-PC")
    assert [(o["name"], o["pc"]) for o in me.others("me")] == [("Jordan", "BACKUP-PC")]
    other.leave("s1")
    assert me.others("me") == []


def test_sync_conflicts_are_listed_and_resolved(data):
    cfg = data / "config.toml"
    conflict = data / "config.sync-conflict-20261019-201500-ABCDEF1.toml"
    conflict.write_text(cfg.read_text(encoding="utf-8").replace("guild_id = 0", "guild_id = 5"), encoding="utf-8")
    found = editing.find_conflicts()
    assert [c["original"] for c in found] == ["config.toml"]
    assert "guild_id = 5" in found[0]["diff"] and found[0]["device"] == "ABCDEF1"
    editing.resolve_conflict(found[0]["file"], "use_other")
    assert "guild_id = 5" in cfg.read_text(encoding="utf-8") and not conflict.exists()
    bad = data / "config.sync-conflict-20261019-201501-ABCDEF1.toml"
    bad.write_text("this is = = not toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="typo"):
        editing.resolve_conflict(bad.name, "use_other")
    editing.resolve_conflict(bad.name, "keep_current")
    assert not bad.exists()
    with pytest.raises(ConfigError):
        editing.resolve_conflict("../../etc/passwd", "keep_current")


def test_sessions_expire_and_revoke(tmp_path):
    s = Sessions(tmp_path / "s.json")
    t1 = s.create({"id": "1", "name": "A"})
    t2 = s.create({"id": "2", "name": "B"})
    assert s.get(t1)["name"] == "A"
    assert t1 not in (tmp_path / "s.json").read_text()  # only hashes are stored
    s.items[next(iter(s.items))]["expires"] = time.time() - 1
    assert s.get(t1) is None
    assert s.revoke() == 1 and s.get(t2) is None


def test_role_menus_with_time_zones(tmp_path):
    from pingwin import zones
    from pingwin.cogs.roles import toggle

    tz = MenuConfig(
        "timezone",
        "TZ",
        "",
        "one",
        (MenuRole("Eastern Time", zone="America/New_York"), MenuRole("Mountain Time", zone="America/Denver")),
    )
    eastern, mountain = SimpleNamespace(name="Eastern Time"), SimpleNamespace(name="Mountain Time")

    class Member:
        id = 7
        display_name = "Sam"

        def __init__(self):
            self.roles = [eastern]

        async def add_roles(self, *roles, reason=None):
            self.roles += list(roles)

        async def remove_roles(self, *roles, reason=None):
            self.roles = [r for r in self.roles if r not in roles]

    persona = SimpleNamespace(say=lambda key, **kw: key)
    m = Member()
    msg = asyncio.run(toggle(m, mountain, tz, tz.roles[1], persona))
    assert m.roles == [mountain] and msg.startswith("role_added") and "Mountain" in msg
    assert zones.get(7).key == "America/Denver"
    asyncio.run(toggle(m, mountain, tz, tz.roles[1], persona))  # tap again: cleared
    assert m.roles == [] and zones.get(7) is None


def test_saving_several_menus_from_the_dashboard(tmp_path):
    async def scenario(client, app):
        hdr = {"Cookie": f"{COOKIE}={KEY}", "X-Pingwin": "1"}
        body = {
            "required_role": "Friend",
            "menus": [
                {"key": "main", "title": "Pings", "description": "", "mode": "many", "roles": [{"name": "Gamer"}]},
                {
                    "key": "",
                    "title": "Your time zone",
                    "description": "",
                    "mode": "one",
                    "roles": [{"name": "Central Time", "zone": "America/Chicago"}],
                },
            ],
        }
        r = await client.post("/api/roles", json=body, headers=hdr)
        assert r.status == 200, await r.text()

    run(scenario, tmp=tmp_path)
    cfg = load_config(require_token=False)
    assert [m.key for m in cfg.roles.menus] == ["main", "your-time-zone"]
    assert cfg.roles.find_role("central time")[1].zone == "America/Chicago"
