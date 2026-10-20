"""Edit config.toml in place for the dashboard, keeping comments and layout.

Every edit is validated with the same rules Pingwin uses at startup before the
file is replaced, so a bad value never reaches disk.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from pathlib import Path

import tomlkit
from tomlkit.items import Table
from tomlkit.toml_document import TOMLDocument

from .config import Config, ConfigError, MenuConfig, MenuRole, config_path, parse_config


def _table(doc: TOMLDocument, key: str) -> Table:
    if key not in doc:
        doc[key] = tomlkit.table()
    return doc[key]  # type: ignore[return-value]


def edit_config(change: Callable[[TOMLDocument], None], path: Path | None = None) -> Config:
    """Apply `change` to config.toml, validate, then save atomically. Returns the new Config."""
    path = path or config_path()
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    change(doc)
    text = tomlkit.dumps(doc)
    try:
        config = parse_config(tomllib.loads(text), require_token=False)
    except tomllib.TOMLDecodeError as e:  # shouldn't happen, but never write a broken file
        raise ConfigError(f"That change would break config.toml: {e}") from e
    tmp = path.with_name(path.name + ".pingwin-tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)
    return config


def _roles_aot(menu: list[MenuRole] | tuple[MenuRole, ...]):
    aot = tomlkit.aot()
    for m in menu:
        t = tomlkit.table()
        t["name"] = m.name
        if m.emoji:
            t["emoji"] = m.emoji
        if m.description:
            t["description"] = m.description
        if m.zone:
            t["zone"] = m.zone
        aot.append(t)
    return aot


def save_roles(
    *,
    title: str,
    description: str,
    required_role: str | None,
    menu: list[MenuRole],
    extra: list[MenuConfig] | None = None,
    path: Path | None = None,
) -> Config:
    """Save the ping menu, and the extra menus too when `extra` is given (None leaves them alone)."""

    def change(doc: TOMLDocument) -> None:
        roles = _table(doc, "roles")
        roles["required_role"] = required_role or ""
        roles["menu_title"] = title
        roles["menu_description"] = description
        menu_aot = _roles_aot(menu)
        extra_aot = None
        if extra is not None:
            extra_aot = tomlkit.aot()
            for m in extra:
                t = tomlkit.table()
                t["key"] = m.key
                t["title"] = m.title
                t["description"] = m.description
                t["mode"] = m.mode
                t["roles"] = _roles_aot(m.roles)
                extra_aot.append(t)
        # Arrays of tables have to come after the plain values, and "menu" before "extra" reads best.
        for k in ("menu", "extra"):
            if k in roles:
                if k == "extra" and extra_aot is None:
                    extra_aot = roles[k]
                del roles[k]
        roles["menu"] = menu_aot
        if extra_aot is not None and len(extra_aot):
            roles["extra"] = extra_aot

    return edit_config(change, path)


def save_dashboard(*, admin_role: str | None, remote_full_access: bool, path: Path | None = None) -> Config:
    def change(doc: TOMLDocument) -> None:
        t = _table(doc, "dashboard")
        t["admin_role"] = admin_role or ""
        t["remote_full_access"] = remote_full_access

    return edit_config(change, path)


def save_failover(
    *,
    enabled: bool,
    status_channel_id: int | None,
    pcs: list[str],
    heartbeat_seconds: int,
    takeover_after_seconds: int,
    path: Path | None = None,
) -> Config:
    def change(doc: TOMLDocument) -> None:
        f = _table(doc, "failover")
        f["enabled"] = enabled
        f["status_channel_id"] = status_channel_id or 0
        f["pcs"] = pcs
        f["heartbeat_seconds"] = heartbeat_seconds
        f["takeover_after_seconds"] = takeover_after_seconds

    return edit_config(change, path)


def save_section(key: str, values: dict, path: Path | None = None) -> Config:
    """Save one Features-tab section. Secrets go to secrets.env; everything else to config.toml."""
    from .features import SECTION_BY_KEY, to_toml_value

    section = SECTION_BY_KEY.get(key)
    if section is None:
        raise ConfigError(f"Unknown settings section {key!r}")
    secrets = {}

    def change(doc: TOMLDocument) -> None:
        table = _table(doc, key)
        for field in section["fields"]:
            if field["key"] not in values:
                continue
            value = values[field["key"]]
            if field["type"] == "secret":
                if value not in (None, True, False):  # True/False just mean "set"/"not set"
                    secrets[field["key"]] = str(value).strip()
                continue
            toml_value = to_toml_value(field, value)
            if field["type"] == "rows":
                aot = tomlkit.aot()
                for row in toml_value:
                    t = tomlkit.table()
                    for k, v in row.items():
                        t[k] = v
                    aot.append(t)
                table[field["key"]] = aot
            else:
                table[field["key"]] = toml_value

    config = edit_config(change, path)
    if secrets:
        save_secrets(secrets)
    return config


def save_secrets(values: dict[str, str]) -> None:
    """Set (or clear, with "") lines in secrets.env, keeping everything else."""
    import os

    from .config import data_dir

    path = data_dir() / "secrets.env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        name = line.split("=", 1)[0].strip()
        if name in remaining:
            if remaining[name]:
                out.append(f"{name}={remaining[name]}")
            remaining.pop(name)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in remaining.items() if v]
    tmp = path.with_name(path.name + ".pingwin-tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    for k, v in values.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
