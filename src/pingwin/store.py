"""Small JSON files for feature data (birthdays, watchlists, sign-ups, ...).

They live in the shared settings folder under state/, so backup PCs have the same data when they
take over. Only the PC that's answering changes them.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
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


def save(name: str, data: Any) -> None:
    path = state_dir() / f"{name}.json"
    tmp = path.with_name(path.name + ".pingwin-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
