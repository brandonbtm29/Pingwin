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

from .config import Config, ConfigError, MenuRole, config_path, parse_config


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


def save_roles(
    *, title: str, description: str, required_role: str | None, menu: list[MenuRole], path: Path | None = None
) -> Config:
    def change(doc: TOMLDocument) -> None:
        roles = _table(doc, "roles")
        roles["required_role"] = required_role or ""
        roles["menu_title"] = title
        roles["menu_description"] = description
        aot = tomlkit.aot()
        for m in menu:
            t = tomlkit.table()
            t["name"] = m.name
            if m.emoji:
                t["emoji"] = m.emoji
            if m.description:
                t["description"] = m.description
            aot.append(t)
        roles["menu"] = aot

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
