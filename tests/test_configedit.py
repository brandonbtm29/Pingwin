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
