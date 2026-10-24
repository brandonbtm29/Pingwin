"""Small JSON files for feature data (birthdays, watchlists, sign-ups, ...).

They live in the shared settings folder under state/, so backup PCs have the same data when they
take over. Only the PC that's answering changes them.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from .config import data_dir

log = logging.getLogger("pingwin.store")


def state_dir() -> Path:
    d = data_dir() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(name: str, default: Callable[[], Any] = dict) -> Any:
    path = state_dir() / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default()
    except (OSError, ValueError) as e:
        log.warning("Couldn't read %s (%s); starting fresh", path.name, e)
        return default()


def backups_dir() -> Path:
    d = state_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot(name: str, keep: int = 14) -> Path | None:
    """Copy a state file to state/backups/<name>-<date>.json once a day, keeping the newest `keep` copies.

    Called before the day's first save, so a copy from before any change is always there."""
    src = state_dir() / f"{name}.json"
    if not src.exists() or src.stat().st_size == 0:
        return None
    dst = backups_dir() / f"{name}-{date.today().isoformat()}.json"
    if dst.exists():
        return None
    try:
        json.loads(src.read_text(encoding="utf-8"))  # don't keep a broken file as a backup
        shutil.copy2(src, dst)
    except (OSError, ValueError) as e:
        log.warning("Couldn't back up %s (%s)", src.name, e)
        return None
    for old in sorted(backups_dir().glob(f"{name}-????-??-??.json"))[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return dst


def backups(name: str) -> list[dict[str, Any]]:
    return [
        {"file": p.name, "day": p.stem[len(name) + 1 :], "bytes": p.stat().st_size}
        for p in sorted(backups_dir().glob(f"{name}-????-??-??.json"), reverse=True)
    ]


def save(name: str, data: Any) -> None:
    path = state_dir() / f"{name}.json"
    tmp = path.with_name(path.name + ".pingwin-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
