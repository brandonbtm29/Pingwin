from pathlib import Path

import pytest

from pingwin.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_example_config_loads(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    monkeypatch.delenv("PINGWIN_GUILD_ID", raising=False)
    cfg = load_config(ROOT / "config.example.toml")
    assert cfg.persona == "pingwin"
    assert cfg.guild_id is None
    assert cfg.roles.required_role == "Friend"
    assert [m.name for m in cfg.roles.menu][:2] == ["Movie Night Maniac", "Film Franchise Fanatic"]
    assert cfg.roles.is_menu_role("gamer")  # case-insensitive


def test_missing_token(monkeypatch, tmp_path):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        load_config(write(tmp_path, ""))
    assert load_config(write(tmp_path, ""), require_token=False).token == ""


def test_duplicate_roles_rejected(tmp_path):
    p = write(tmp_path, '[[roles.menu]]\nname = "Gamer"\n[[roles.menu]]\nname = "gamer"\n')
    with pytest.raises(ConfigError, match="twice"):
        load_config(p, require_token=False)


def test_too_many_roles(tmp_path):
    blocks = "".join(f'[[roles.menu]]\nname = "R{i}"\n' for i in range(26))
    with pytest.raises(ConfigError, match="at most 25"):
        load_config(write(tmp_path, blocks), require_token=False)


def test_env_guild_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("PINGWIN_GUILD_ID", "123")
    assert load_config(write(tmp_path, "[bot]\nguild_id = 9\n"), require_token=False).guild_id == 123


def test_bad_toml(tmp_path):
    with pytest.raises(ConfigError, match="typo"):
        load_config(write(tmp_path, "[bot\n"), require_token=False)


def test_failover_settings(tmp_path):
    cfg = load_config(write(tmp_path, "[failover]\nenabled = true\nstatus_channel_id = 42\n"), require_token=False)
    assert cfg.failover.active and cfg.failover.status_channel_id == 42
    assert cfg.failover.heartbeat_seconds == 30 and cfg.failover.takeover_after_seconds == 120
    off = load_config(write(tmp_path, "[failover]\nenabled = true\n"), require_token=False)
    assert not off.failover.active  # no channel yet


def test_takeover_must_exceed_heartbeat(tmp_path):
    p = write(tmp_path, "[failover]\nheartbeat_seconds = 60\ntakeover_after_seconds = 100\n")
    with pytest.raises(ConfigError, match="takeover_after_seconds"):
        load_config(p, require_token=False)


def test_data_dir_from_env(monkeypatch, tmp_path):
    from pingwin.config import config_path, load_node

    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    monkeypatch.delenv("PINGWIN_CONFIG", raising=False)
    assert config_path() == tmp_path / "config.toml"
    monkeypatch.setenv("PINGWIN_NODE", "BACKUP")
    assert load_node().name == "BACKUP"


def test_pc_list(tmp_path):
    cfg = load_config(write(tmp_path, '[failover]\npcs = ["BTM-NOVA", "BACKUP-PC"]\n'), require_token=False)
    assert cfg.failover.pcs == ("BTM-NOVA", "BACKUP-PC")
    with pytest.raises(ConfigError, match="twice"):
        load_config(write(tmp_path, '[failover]\npcs = ["A", "a"]\n'), require_token=False)
    with pytest.raises(ConfigError, match="list of PC names"):
        load_config(write(tmp_path, '[failover]\npcs = "A"\n'), require_token=False)
