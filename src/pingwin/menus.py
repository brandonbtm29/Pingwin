"""Remembers where role menus were posted, so they can be refreshed in place.

Stored as menus.json in the shared settings folder, so every PC knows about them.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import data_dir

log = logging.getLogger("pingwin.menus")


@dataclass(frozen=True)
class PostedMenu:
    guild_id: int
    channel_id: int
    message_id: int
    menu: str = "main"  # which menu (see [[roles.extra]]); older files don't say, so "main"

    @property
    def url(self) -> str:
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.message_id}"


def _path() -> Path:
    return data_dir() / "menus.json"


def load_menus() -> list[PostedMenu]:
    path = _path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [
            PostedMenu(int(m["guild_id"]), int(m["channel_id"]), int(m["message_id"]), str(m.get("menu") or "main"))
            for m in raw
        ]
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("Couldn't read %s (%s); starting a fresh list", path, e)
        return []


def save_menus(menus: list[PostedMenu]) -> None:
    path = _path()
    # IDs as strings: they're too big for JavaScript numbers.
    payload = [{k: str(v) for k, v in asdict(m).items()} for m in menus]
    tmp = path.with_name(path.name + ".pingwin-tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def remember(menu: PostedMenu) -> None:
    menus = [m for m in load_menus() if m.message_id != menu.message_id]
    save_menus([*menus, menu])


def forget(message_ids: set[int]) -> None:
    if message_ids:
        save_menus([m for m in load_menus() if m.message_id not in message_ids])
