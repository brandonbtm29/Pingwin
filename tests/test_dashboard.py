"""The dashboard must only answer locally, only to the signed-in browser, and only to its own page."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from pingwin.config import load_config
from pingwin.dashboard.server import COOKIE, create_app

ROOT = Path(__file__).resolve().parents[1]
KEY = "k" * 32


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text((ROOT / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("PINGWIN_CONFIG", str(p))
    return p


def fake_bot():
    bot = SimpleNamespace(reloaded=0)

    def reload_settings():
        bot.reloaded += 1

    bot.reload_settings = reload_settings
    return bot


def run(scenario):
    async def go():
        bot = fake_bot()
        async with TestClient(TestServer(create_app(bot, KEY), host="127.0.0.1")) as client:
            await scenario(client, bot)

    asyncio.run(go())


SIGNED_IN = {"Cookie": f"{COOKIE}={KEY}"}
WRITE = {**SIGNED_IN, "X-Pingwin": "1"}


def test_requires_sign_in():
    async def scenario(client, bot):
        r = await client.get("/api/logs")
        assert r.status == 401
        r = await client.get("/")
        assert r.status == 401 and "open_dashboard" in await r.text()

    run(scenario)


def test_login_sets_cookie_and_wrong_key_fails():
    async def scenario(client, bot):
        r = await client.get("/login?key=wrong", allow_redirects=False)
        assert r.status == 401
        r = await client.get(f"/login?key={KEY}", allow_redirects=False)
        assert r.status == 302
        cookie = r.cookies[COOKIE]
        assert cookie.value == KEY and cookie["httponly"] and cookie["samesite"] == "Strict"

    run(scenario)


def test_rejects_other_hostnames():
    async def scenario(client, bot):
        r = await client.get("/api/logs", headers={**SIGNED_IN, "Host": "evil.example:8787"})
        assert r.status == 403

    run(scenario)


def test_writes_need_dashboard_header_and_local_origin(cfg_file):
    body = {"title": "T", "description": "", "required_role": "", "menu": [{"name": "Gamer"}]}

    async def scenario(client, bot):
        r = await client.post("/api/roles", json=body, headers=SIGNED_IN)
        assert r.status == 403
        r = await client.post("/api/roles", json=body, headers={**WRITE, "Origin": "https://evil.example"})
        assert r.status == 403
        assert bot.reloaded == 0
        r = await client.post("/api/roles", json=body, headers={**WRITE, "Origin": "http://127.0.0.1:8787"})
        assert r.status == 200, await r.text()
        assert bot.reloaded == 1

    run(scenario)
    assert [m.name for m in load_config(cfg_file, require_token=False).roles.menu] == ["Gamer"]


def test_bad_settings_return_a_readable_error(cfg_file):
    body = {"title": "T", "description": "", "required_role": "", "menu": [{"name": "A"}, {"name": "a"}]}

    async def scenario(client, bot):
        r = await client.post("/api/roles", json=body, headers=WRITE)
        assert r.status == 400
        assert "twice" in (await r.json())["error"]

    run(scenario)


def test_security_headers_and_static_files():
    async def scenario(client, bot):
        r = await client.get("/static/app.js", headers=SIGNED_IN)
        assert r.status == 200
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        r = await client.get("/static/../server.py", headers=SIGNED_IN)
        assert r.status == 404

    run(scenario)
