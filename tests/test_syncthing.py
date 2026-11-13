"""Asking SyncThing whether the shared folder is in sync: reading its config.xml and its local API."""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import web

from pingwin import syncthing
from pingwin.syncthing import FolderSync, _since, find_api, folder_sync, summarize

ME, BACKUP, LAPTOP = "AAAAAAA-ME", "BBBBBBB-BACKUP", "CCCCCCC-LAPTOP"


def write_config(d, folder_path, port=8384, tls="false", key="secret-key"):
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.xml").write_text(
        f"""<configuration version="37">
    <folder id="other" label="Other" path="{folder_path.parent / "Other"}"><device id="{ME}"/></folder>
    <folder id="pingwin-settings" label="Pingwin" path="{folder_path}">
        <device id="{ME}"/><device id="{BACKUP}"/><device id="{LAPTOP}"/>
    </folder>
    <gui enabled="true" tls="{tls}"><address>0.0.0.0:{port}</address><apikey>{key}</apikey></gui>
</configuration>""",
        encoding="utf-8",
    )


def stamp(seconds_ago):
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.1234567Z")


def test_reads_where_the_api_is_and_which_folder(tmp_path):
    shared = tmp_path / "SyncThing" / "Pingwin"
    write_config(tmp_path / "st", shared)
    api = find_api(shared / "state", [tmp_path / "missing", tmp_path / "st"])  # a file inside the folder
    assert api.base == "http://127.0.0.1:8384" and api.key == "secret-key" and api.context is False
    assert api.folder == "pingwin-settings" and api.devices == (ME, BACKUP, LAPTOP)
    assert find_api(tmp_path / "Elsewhere", [tmp_path / "st"]) is None
    write_config(tmp_path / "tls", shared, tls="true")  # https, but SyncThing's certificate isn't there
    assert find_api(shared, [tmp_path / "tls"]) is None


def test_times_from_syncthing():
    assert abs(_since(stamp(30)) - 30) < 2  # seven decimal places, like SyncThing on Windows
    assert abs(_since((datetime.now(UTC) - timedelta(seconds=5)).isoformat()) - 5) < 2
    assert _since("0001-01-01T00:00:00Z") is None and _since("soon") is None


def test_summarize():
    status = {"myID": ME, "uptime": 64}
    connections = {
        BACKUP: {"connected": True, "paused": False, "startedAt": stamp(40)},
        LAPTOP: {"connected": False, "startedAt": "0001-01-01T00:00:00Z"},
        ME: {"connected": False},
    }
    idle = summarize(status, {"state": "idle", "needFiles": 0, "needBytes": 0}, connections, (ME, BACKUP, LAPTOP))
    assert idle.idle and idle.waiting_for == "" and idle.uptime == 64 and len(idle.peers) == 1
    assert 38 < idle.peers[0] < 42
    busy = summarize(status, {"state": "syncing", "needFiles": 1, "needBytes": 300}, connections, (ME, BACKUP))
    assert not busy.idle and busy.waiting_for == "SyncThing has 1 file to bring over"
    scan = summarize(status, {"state": "scanning"}, {}, (ME,))
    assert scan.waiting_for == "SyncThing is checking the folder" and scan.peers == ()
    assert summarize(status, {"state": "sync-preparing"}, {}, ()).waiting_for == "SyncThing is sync preparing"


@pytest.fixture
def fake_syncthing(tmp_path, monkeypatch):
    """A stand-in for SyncThing's API on this PC, and a config.xml pointing at it."""
    seen = {"keys": set(), "folders": set()}

    def check(request):
        seen["keys"].add(request.headers.get("X-API-Key"))
        if request.headers.get("X-API-Key") != "secret-key":
            raise web.HTTPForbidden()

    async def status(request):
        check(request)
        return web.json_response({"myID": ME, "uptime": 120})

    async def db(request):
        check(request)
        seen["folders"].add(request.query.get("folder"))
        return web.json_response({"state": "idle", "needFiles": 0, "needDeletes": 0, "needBytes": 0})

    async def connections(request):
        check(request)
        return web.json_response({"connections": {BACKUP: {"connected": True, "startedAt": stamp(300)}}})

    app = web.Application()
    app.router.add_get("/rest/system/status", status)
    app.router.add_get("/rest/db/status", db)
    app.router.add_get("/rest/system/connections", connections)
    return app, seen


def test_asks_syncthing_on_this_pc(tmp_path, monkeypatch, fake_syncthing):
    app, seen = fake_syncthing
    shared = tmp_path / "SyncThing" / "Pingwin"

    async def go():
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        write_config(tmp_path / "st", shared, port=port)
        monkeypatch.setattr(syncthing, "config_dirs", lambda: [tmp_path / "st"])
        try:
            got = await folder_sync(shared)
            write_config(tmp_path / "st", shared, port=port, key="wrong")
            refused = await folder_sync(shared)
        finally:
            await runner.cleanup()
        return got, refused

    got, refused = asyncio.run(go())
    assert isinstance(got, FolderSync) and got.idle and got.uptime == 120 and 295 < got.peers[0] < 305
    assert seen["folders"] == {"pingwin-settings"} and "secret-key" in seen["keys"]
    assert refused is None  # SyncThing said no: wait the old way


def test_no_syncthing_here(tmp_path, monkeypatch):
    monkeypatch.setattr(syncthing, "config_dirs", lambda: [tmp_path / "nothing"])
    assert asyncio.run(folder_sync(tmp_path)) is None
    write_config(tmp_path / "st", tmp_path / "Pingwin", port=9)  # configured, but nothing listening
    monkeypatch.setattr(syncthing, "config_dirs", lambda: [tmp_path / "st"])
    start = time.monotonic()
    assert asyncio.run(folder_sync(tmp_path / "Pingwin")) is None
    assert time.monotonic() - start < syncthing.TIMEOUT + 1
