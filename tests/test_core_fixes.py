"""Damaged state files, secrets.env reloads, DST-safe relative times and config edge cases."""

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from pingwin import config, menus, store
from pingwin.bot import Pingwin
from pingwin.config import load_config


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    monkeypatch.delenv("PINGWIN_CONFIG", raising=False)
    monkeypatch.setattr(store, "_unreadable", set())
    return tmp_path


# ------------------------------------------------------------ damaged state files
def test_broken_state_file_is_kept_not_overwritten(data, caplog):
    path = data / "state" / "timezones.json"
    path.parent.mkdir()
    broken = '{"1": "America/Denver", "2": "Europe/London",}'  # trailing comma from a hand edit
    path.write_text(broken, encoding="utf-8")

    with caplog.at_level(logging.WARNING, "pingwin.store"):
        assert store.load("timezones") == {}  # the bot keeps going...
    kept = list(path.parent.glob("timezones.corrupt-*.json"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == broken  # ...and nothing is lost
    assert "timezones.corrupt-" in caplog.text

    store.save("timezones", {"3": "Asia/Tokyo"})  # the next save starts a fresh file beside the kept copy
    assert json.loads(path.read_text(encoding="utf-8")) == {"3": "Asia/Tokyo"}
    assert store.load("timezones") == {"3": "Asia/Tokyo"}
    assert len(list(path.parent.glob("timezones.corrupt-*.json"))) == 1  # moved aside only once


def test_two_broken_copies_dont_overwrite_each_other(data):
    path = data / "state" / "birthdays.json"
    path.parent.mkdir()
    for text in ("{oops", "[still oops"):
        path.write_text(text, encoding="utf-8")
        store.load("birthdays")
    kept = sorted(p.read_text(encoding="utf-8") for p in path.parent.glob("birthdays.corrupt-*.json"))
    assert kept == ["[still oops", "{oops"]


def test_empty_state_file_is_just_empty(data):
    path = data / "state" / "streams.json"
    path.parent.mkdir()
    path.write_text("", encoding="utf-8")
    assert store.load("streams") == {}
    assert not list(path.parent.glob("streams.corrupt-*"))


def test_unreadable_state_file_is_not_replaced(data, monkeypatch, caplog):
    path = data / "state" / "gameday.json"
    path.parent.mkdir()
    path.write_text('{"keep": true}', encoding="utf-8")
    real = Path.read_text

    def locked(self, *a, **k):
        if self == path:
            raise PermissionError("locked by another program")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", locked)
    assert store.load("gameday") == {}
    with caplog.at_level(logging.ERROR, "pingwin.store"):
        store.save("gameday", {"made": "up"})  # refused: it would wipe the real file
    assert "Not saving gameday.json" in caplog.text
    monkeypatch.setattr(Path, "read_text", real)
    assert json.loads(path.read_text(encoding="utf-8")) == {"keep": True}

    assert store.load("gameday") == {"keep": True}  # readable again: saving works again
    store.save("gameday", {"keep": False})
    assert store.load("gameday") == {"keep": False}


def test_broken_menus_file_is_kept(data):
    path = data / "menus.json"
    path.write_text('[{"guild_id": "1", "channel_id": "2", "message_id": "3",}]', encoding="utf-8")
    assert menus.load_menus() == []
    assert len(list(data.glob("menus.corrupt-*.json"))) == 1
    menus.remember(menus.PostedMenu(1, 2, 4))
    assert [m.message_id for m in menus.load_menus()] == [4]


def test_unreadable_menus_file_is_not_replaced(data, monkeypatch):
    path = data / "menus.json"
    path.write_text('[{"guild_id": "1", "channel_id": "2", "message_id": "3"}]', encoding="utf-8")
    real = Path.read_text
    monkeypatch.setattr(
        Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("busy")) if self == path else real(self)
    )
    menus.remember(menus.PostedMenu(1, 2, 4))
    menus.forget({3})
    monkeypatch.setattr(Path, "read_text", real)
    assert [m.message_id for m in menus.load_menus()] == [3]


# ------------------------------------------------------------ secrets.env reloads
NAMES = ("PINGWIN_TEST_KEY", "PINGWIN_TEST_OTHER", "PINGWIN_TEST_LOCAL", "PINGWIN_TEST_INHERITED")


@pytest.fixture
def secrets(data, monkeypatch, tmp_path):
    for name in NAMES:  # whatever the test sets is undone afterwards
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("PINGWIN_TEST_INHERITED", "from-windows")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("PINGWIN_TEST_LOCAL=this-pc\n", encoding="utf-8")
    monkeypatch.setattr(config, "_local_env", lambda: project / ".env")
    monkeypatch.setattr(config, "_inherited_env", None)
    monkeypatch.setattr(config, "_from_secrets", {})
    path = data / "secrets.env"

    def write(text):
        path.write_text(text, encoding="utf-8")
        stamp = path.stat().st_mtime + 5  # make sure the change is visible even on coarse file systems
        os.utime(path, (stamp, stamp))

    return write


def test_secrets_reload_updates_adds_and_removes(secrets):
    secrets("PINGWIN_TEST_KEY=one\nPINGWIN_TEST_LOCAL=shared\nPINGWIN_TEST_INHERITED=shared\n")
    config.load_env()
    assert os.environ["PINGWIN_TEST_KEY"] == "one"
    assert os.environ["PINGWIN_TEST_LOCAL"] == "this-pc"  # this PC's own .env wins, as before
    assert os.environ["PINGWIN_TEST_INHERITED"] == "from-windows"  # so does the real environment

    secrets("PINGWIN_TEST_KEY=two\nPINGWIN_TEST_OTHER=new\nPINGWIN_TEST_LOCAL=changed\nPINGWIN_TEST_INHERITED=x\n")
    assert sorted(config.load_secrets()) == ["PINGWIN_TEST_KEY", "PINGWIN_TEST_OTHER"]
    assert os.environ["PINGWIN_TEST_KEY"] == "two" and os.environ["PINGWIN_TEST_OTHER"] == "new"
    assert os.environ["PINGWIN_TEST_LOCAL"] == "this-pc" and os.environ["PINGWIN_TEST_INHERITED"] == "from-windows"

    secrets("PINGWIN_TEST_OTHER=new\n")  # a key deleted on another PC's dashboard
    assert config.load_secrets() == ["PINGWIN_TEST_KEY"]
    assert "PINGWIN_TEST_KEY" not in os.environ
    assert os.environ["PINGWIN_TEST_LOCAL"] == "this-pc" and os.environ["PINGWIN_TEST_INHERITED"] == "from-windows"


def test_missing_secrets_file_changes_nothing(secrets, data):
    secrets("PINGWIN_TEST_KEY=one\n")
    config.load_env()
    (data / "secrets.env").unlink()  # e.g. mid-sync on a new backup PC
    assert config.load_secrets() == []
    assert os.environ["PINGWIN_TEST_KEY"] == "one"


def test_bot_auto_reload_watches_secrets(secrets, data):
    (data / "config.toml").write_text("", encoding="utf-8")
    secrets("PINGWIN_TEST_KEY=one\n")
    config.load_env()
    bot = Pingwin.__new__(Pingwin)  # just the settings parts, no Discord connection
    bot.config = load_config(require_token=False)
    bot.coordinator = SimpleNamespace(active=False)
    bot.restart_needed = False
    bot._remember_settings_files()
    assert data / "secrets.env" in bot._watched

    secrets("PINGWIN_TEST_KEY=two\n")  # synced from the main PC
    bot._auto_reload()
    assert os.environ["PINGWIN_TEST_KEY"] == "two"
    assert not bot.restart_needed


# ------------------------------------------------------------ "in N hours" across DST
NY = ZoneInfo("America/New_York")


def test_in_hours_counts_real_time_across_dst():
    from pingwin.timeparse import parse_when

    fall = datetime(2026, 11, 1, 0, 30, tzinfo=NY)  # clocks go back at 2am
    when = parse_when("in 2 hours", fall)
    assert when.timestamp() - fall.timestamp() == 2 * 3600
    assert when.astimezone(UTC) == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 01:30 EST
    spring = datetime(2026, 3, 8, 1, 30, tzinfo=NY)  # clocks jump to 3am
    when = parse_when("in 1h 15m", spring)
    assert when.timestamp() - spring.timestamp() == 75 * 60
    assert (when.hour, when.minute) == (3, 45)
    plain = datetime(2026, 10, 19, 13, 0, tzinfo=NY)  # no DST change: same as before
    assert parse_when("in 30 min", plain) == plain + timedelta(minutes=30)


# ------------------------------------------------------------ config edge cases
def write_config(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_failover_on_without_channel_is_single_pc_with_a_warning(tmp_path):
    cfg = load_config(write_config(tmp_path, "[failover]\nenabled = true\n"), require_token=False).failover
    assert not cfg.active and cfg.missing_channel
    assert "status_channel_id" in cfg.warning
    ok = load_config(write_config(tmp_path, "[failover]\nenabled = true\nstatus_channel_id = 5\n"), require_token=False)
    assert ok.failover.active and ok.failover.warning is None
    off = load_config(write_config(tmp_path, "[failover]\nenabled = false\n"), require_token=False)
    assert off.failover.warning is None


def test_coordinator_runs_single_pc_without_channel():
    import asyncio

    from pingwin.config import FailoverConfig, Node
    from pingwin.failover import Coordinator

    bot = SimpleNamespace(
        config=SimpleNamespace(failover=FailoverConfig(True, None, 30, 120)), node=Node("A"), dispatch=lambda *a: 0
    )
    coord = Coordinator(bot)
    asyncio.run(coord.tick())
    assert coord.active and coord.leader == "A"


def test_bot_warns_when_failover_has_no_channel(data, caplog):
    (data / "config.toml").write_text("", encoding="utf-8")
    bot = Pingwin.__new__(Pingwin)
    bot.config = load_config(require_token=False)
    bot.coordinator = SimpleNamespace(active=False)
    bot.restart_needed = False
    (data / "config.toml").write_text("[failover]\nenabled = true\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, "pingwin"):
        bot.reload_settings()
    assert "status_channel_id isn't set" in caplog.text


def test_empty_time_zone_menu_stays_a_time_zone_menu(tmp_path):
    text = """
[[roles.extra]]
title = "What's your time zone?"
mode = "one"
timezone = true

[[roles.extra]]
title = "Games"

[[roles.extra]]
title = "Old zones"
[[roles.extra.roles]]
name = "Mountain"
zone = "America/Denver"

[[roles.extra]]
title = "Not zones"
timezone = false
[[roles.extra.roles]]
name = "Central"
zone = "America/Chicago"
"""
    roles = load_config(write_config(tmp_path, text), require_token=False).roles
    tz, games, old, explicit_off = roles.extra
    assert tz.is_timezone and not tz.roles  # saved before any roles were added
    assert not games.is_timezone
    assert old.is_timezone  # older files without the flag: still worked out from the zones
    assert not explicit_off.is_timezone


def test_time_zone_flag_must_be_true_or_false(tmp_path):
    from pingwin.config import ConfigError

    with pytest.raises(ConfigError, match="timezone must be true or false"):
        load_config(write_config(tmp_path, '[[roles.extra]]\ntitle = "TZ"\ntimezone = "yes"\n'), require_token=False)


def test_time_zone_flag_is_saved(tmp_path):
    from pingwin.config import MenuConfig, MenuRole
    from pingwin.configedit import save_roles

    path = write_config(tmp_path, "")
    save_roles(
        title="Pick your pings",
        description="",
        required_role="",
        menu=[MenuRole("Gamer", "🎮", "")],
        extra=[MenuConfig("tz", "What's your time zone?", "", "one", (), timezone=True)],
        path=path,
    )
    (tz,) = load_config(path, require_token=False).roles.extra
    assert tz.is_timezone and not tz.roles
