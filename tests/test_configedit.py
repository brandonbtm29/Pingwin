import tomllib
from pathlib import Path

import pytest

from pingwin.config import ConfigError, MenuRole, load_config
from pingwin.configedit import save_failover, save_roles

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text((ROOT / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    return p


def test_save_roles_keeps_comments_and_other_sections(cfg_file):
    save_roles(
        title="Pings!",
        description="Tap away",
        required_role="Friend",
        menu=[MenuRole("Gamer", "🎮", "Game days"), MenuRole("Poll Pal")],
        path=cfg_file,
    )
    text = cfg_file.read_text(encoding="utf-8")
    assert "# Pingwin settings, shared by every PC" in text  # comments survive
    cfg = load_config(cfg_file, require_token=False)
    assert cfg.roles.menu_title == "Pings!"
    assert [(m.name, m.emoji) for m in cfg.roles.menu] == [("Gamer", "🎮"), ("Poll Pal", None)]
    assert cfg.failover.heartbeat_seconds == 30  # untouched


def test_invalid_edit_is_not_written(cfg_file):
    before = cfg_file.read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="twice"):
        save_roles(title="x", description="", required_role=None, menu=[MenuRole("A"), MenuRole("a")], path=cfg_file)
    assert cfg_file.read_text(encoding="utf-8") == before


def test_save_failover(cfg_file):
    save_failover(
        enabled=True,
        status_channel_id=1234567890123456789,
        pcs=["BTM-NOVA", "BACKUP-PC"],
        heartbeat_seconds=30,
        takeover_after_seconds=120,
        path=cfg_file,
    )
    data = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    assert data["failover"]["status_channel_id"] == 1234567890123456789
    assert load_config(cfg_file, require_token=False).failover.pcs == ("BTM-NOVA", "BACKUP-PC")
    with pytest.raises(ConfigError):
        save_failover(
            enabled=True, status_channel_id=1, pcs=[], heartbeat_seconds=60, takeover_after_seconds=60, path=cfg_file
        )


def test_save_extra_menus_round_trip(cfg_file):
    from pingwin.config import MenuConfig

    tz = MenuConfig(
        key="timezone",
        title="Your time zone",
        description="Pick one",
        mode="one",
        roles=(
            MenuRole("Eastern Time", "🌅", zone="America/New_York"),
            MenuRole("Mountain Time", zone="America/Denver"),
        ),
    )
    save_roles(
        title="Pings", description="", required_role="Friend", menu=[MenuRole("Gamer")], extra=[tz], path=cfg_file
    )
    cfg = load_config(cfg_file, require_token=False)
    assert [m.key for m in cfg.roles.menus] == ["main", "timezone"]
    menu, role = cfg.roles.find_role("mountain time")
    assert menu.mode == "one" and menu.is_timezone and role.zone == "America/Denver"
    # Saving only the ping menu keeps the extra menus.
    save_roles(title="Pings 2", description="", required_role="Friend", menu=[MenuRole("Gamer")], path=cfg_file)
    assert load_config(cfg_file, require_token=False).roles.get_menu("timezone") is not None
    # And extra=[] removes them.
    save_roles(title="Pings", description="", required_role=None, menu=[MenuRole("Gamer")], extra=[], path=cfg_file)
    assert len(load_config(cfg_file, require_token=False).roles.menus) == 1


def test_role_in_two_menus_is_rejected(cfg_file):
    from pingwin.config import MenuConfig

    other = MenuConfig("x", "Other", "", "many", (MenuRole("Gamer"),))
    with pytest.raises(ConfigError, match="two menus"):
        save_roles(
            title="P", description="", required_role=None, menu=[MenuRole("Gamer")], extra=[other], path=cfg_file
        )
    bad = MenuConfig("tz", "TZ", "", "one", (MenuRole("Moon Time", zone="Moon/Base"),))
    with pytest.raises(ConfigError, match="unknown time zone"):
        save_roles(title="P", description="", required_role=None, menu=[], extra=[bad], path=cfg_file)
