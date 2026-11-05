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
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import data_dir

log = logging.getLogger("pingwin.store")


def state_dir() -> Path:
    d = data_dir() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


# State files that couldn't be read (locked, permissions, ...) and so mustn't be overwritten with
# whatever was made up in their place. Cleared by the next successful load.
_unreadable: set[str] = set()


def set_aside(path: Path, reason: object) -> Path | None:
    """Move a settings/state file that can't be parsed (e.g. a hand edit with a typo) out of the way as
    <name>.corrupt-<timestamp>.json, so the next save can't overwrite what was in it. Raises OSError if it can't."""
    try:
        if not path.read_bytes().strip():
            return None  # empty: nothing to keep
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
        n = 1
        while dst.exists():
            n += 1
            dst = path.with_name(f"{path.stem}.corrupt-{stamp}-{n}{path.suffix}")
        os.replace(path, dst)
    except OSError as e:
        log.warning("%s is damaged (%s) and couldn't be moved aside: %s", path.name, reason, e)
        raise
    log.warning(
        "%s is damaged (%s). Moved it to %s and started fresh; fix it and rename it back to restore it.",
        path.name,
        reason,
        dst.name,
    )
    return dst


def load(name: str, default: Callable[[], Any] = dict) -> Any:
    path = state_dir() / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _unreadable.discard(name)
        return default()
    except ValueError as e:  # broken JSON: keep the file, then carry on without it
        try:
            set_aside(path, e)
            _unreadable.discard(name)
        except OSError:
            _unreadable.add(name)
        return default()
    except OSError as e:  # couldn't read it right now; it may be fine, so don't let a save replace it
        log.warning("Couldn't read %s (%s); changes to it won't be saved until it can be read", path.name, e)
        _unreadable.add(name)
        return default()
    _unreadable.discard(name)
    return data


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
    if name in _unreadable:
        log.error("Not saving %s: the copy on disk couldn't be read, and saving would replace it", path.name)
        return
    tmp = path.with_name(path.name + ".pingwin-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
