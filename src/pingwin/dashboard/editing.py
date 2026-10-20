"""Keeping several admins from stepping on each other in the dashboard.

* **Presence:** each dashboard says who has it open and on which tab. Dashboards on other PCs share
  this through a small file per PC in the settings folder (state/presence/), which SyncThing copies
  within seconds.
* **Stale saves:** every page load gets a version for each settings section. Saving with an old
  version is refused, so nobody quietly overwrites someone else's newer change.
* **Sync conflicts:** if two PCs change the same file before SyncThing has caught up, SyncThing keeps
  both and names the loser `*.sync-conflict-*`. The dashboard lists those and lets you pick one.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import time
import tomllib
from pathlib import Path
from typing import Any

from .. import store
from ..config import ConfigError, config_path, data_dir, parse_config

LAST_EDITS = "last-edits"
PRESENCE_DIR = "presence"
HERE_SECONDS = 45  # someone on this PC's dashboard counts as "here" this long after their last ping
AWAY_SECONDS = 90  # other PCs' presence files arrive through SyncThing, so allow a bit longer

LABELS = {
    "roles": "the role menus",
    "failover": "PCs & failover",
    "dashboard": "remote access",
    "birthday-list": "the birthday list",
}


class StaleEdit(Exception):
    """Someone else saved this section after the page was loaded."""


# --------------------------------------------------------------------- versions
def _read_config(path: Path | None = None) -> dict[str, Any]:
    try:
        with (path or config_path()).open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _digest(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


def versions(path: Path | None = None) -> dict[str, str]:
    """A short fingerprint of each [section] of config.toml (missing sections included as empty)."""
    from ..features import SECTIONS

    data = _read_config(path)
    keys = {"roles", "failover", "dashboard", *(s["key"] for s in SECTIONS), *data.keys()}
    return {k: _digest(data.get(k)) for k in sorted(keys)}


def state_version(name: str) -> str:
    return _digest(store.load(name))


def label(key: str) -> str:
    if key in LABELS:
        return LABELS[key]
    from ..features import SECTION_BY_KEY

    section = SECTION_BY_KEY.get(key)
    return f"the {section['title']} settings" if section else f"the {key} settings"


def _ago(ts: float) -> str:
    s = max(0, int(time.time() - ts))
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60} min ago"
    return f"{s // 3600} h ago"


def check(key: str, base: str | None, current: str) -> None:
    """Raise StaleEdit if the section changed since the page got version `base`."""
    if not base or base == current:
        return
    last = store.load(LAST_EDITS).get(key)
    who = (
        f"{last['who']} (on {last['pc']}) saved {label(key)} {_ago(last['at'])}"
        if last
        else (f"Someone changed {label(key)}")
    )
    raise StaleEdit(
        f"{who}, after you opened this page, so your save would undo their change. "
        "Reload to see the latest version, then make your change again."
    )


def record(key: str, who: str, pc: str) -> None:
    edits = store.load(LAST_EDITS)
    edits[key] = {"who": who, "pc": pc, "at": time.time()}
    store.save(LAST_EDITS, edits)


def last_edits() -> dict[str, Any]:
    edits = store.load(LAST_EDITS)
    return {k: {**v, "ago": _ago(v["at"])} for k, v in edits.items()}


# --------------------------------------------------------------------- presence
class Presence:
    def __init__(self, pc: str) -> None:
        self.pc = pc
        self.here: dict[str, dict[str, Any]] = {}
        self._written: str = ""
        self._written_at = 0.0

    def _dir(self) -> Path:
        d = store.state_dir() / PRESENCE_DIR
        d.mkdir(exist_ok=True)
        return d

    def _file(self) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", self.pc) or "pc"
        return self._dir() / f"{safe}.json"

    def ping(self, viewer: Any, tab: str, dirty: bool) -> None:
        self.here[viewer.sid] = {
            "sid": viewer.sid,
            "name": viewer.name,
            "avatar": viewer.avatar,
            "kind": viewer.kind,
            "pc": self.pc,
            "tab": str(tab)[:30],
            "dirty": bool(dirty),
            "seen": time.time(),
        }
        self._write()

    def leave(self, sid: str) -> None:
        if self.here.pop(sid, None) is not None:
            self._write(force=True)

    def _live(self) -> list[dict[str, Any]]:
        now = time.time()
        self.here = {k: v for k, v in self.here.items() if now - v["seen"] < HERE_SECONDS}
        return list(self.here.values())

    def _write(self, force: bool = False) -> None:
        entries = self._live()
        shape = json.dumps(sorted((e["sid"], e["tab"], e["dirty"]) for e in entries))
        # Only touch the synced file when something changed, or now and then so others know we're still here.
        if not force and shape == self._written and time.time() - self._written_at < 30:
            return
        path = self._file()
        try:
            if entries:
                tmp = path.with_name(path.name + ".pingwin-tmp")
                tmp.write_text(json.dumps({"pc": self.pc, "at": time.time(), "people": entries}), encoding="utf-8")
                os.replace(tmp, path)
            elif path.exists():
                path.unlink()
        except OSError:
            return
        self._written, self._written_at = shape, time.time()

    def others(self, sid: str) -> list[dict[str, Any]]:
        people = [e for e in self._live() if e["sid"] != sid]
        now = time.time()
        try:
            files = list(self._dir().glob("*.json"))
        except OSError:
            files = []
        for f in files:
            if f == self._file():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for e in data.get("people", []):
                if now - float(e.get("seen", 0)) < AWAY_SECONDS:
                    people.append(e)
        return sorted(people, key=lambda e: (e.get("pc") != self.pc, e.get("name", "")))


# --------------------------------------------------------------------- SyncThing conflicts
CONFLICT = re.compile(r"\.sync-conflict-(?P<date>\d{8})-(?P<time>\d{6})-(?P<device>[A-Z0-9]{7})")
SKIP_DIRS = {".stversions", ".stfolder", PRESENCE_DIR}


def _original_name(name: str) -> str:
    return CONFLICT.sub("", name, count=1)


def find_conflicts(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or data_dir()
    out = []
    try:
        candidates = [p for p in root.rglob("*.sync-conflict-*") if p.is_file()]
    except OSError:
        return []
    for p in sorted(candidates):
        rel = p.relative_to(root)
        if SKIP_DIRS & set(rel.parts[:-1]) or not CONFLICT.search(p.name):
            continue
        original = p.with_name(_original_name(p.name))
        m = CONFLICT.search(p.name)
        try:
            other = p.read_text(encoding="utf-8").splitlines()
            current = original.read_text(encoding="utf-8").splitlines() if original.exists() else []
        except (OSError, UnicodeDecodeError):
            other, current = [], []
        diff = list(difflib.unified_diff(current, other, "current version", "other copy", lineterm="", n=2))
        out.append(
            {
                "file": rel.as_posix(),
                "original": original.relative_to(root).as_posix(),
                "when": f"{m['date'][:4]}-{m['date'][4:6]}-{m['date'][6:]} {m['time'][:2]}:{m['time'][2:4]}",
                "device": m["device"],
                "diff": "\n".join(diff[:300]) + ("\n…" if len(diff) > 300 else ""),
                "same": not diff,
            }
        )
    return out


def resolve_conflict(rel: str, action: str, root: Path | None = None) -> str:
    """keep_current: delete the conflict copy. use_other: make the conflict copy the real file."""
    root = (root or data_dir()).resolve()
    path = (root / rel).resolve()
    if root not in path.parents or not CONFLICT.search(path.name) or not path.is_file():
        raise ConfigError("That conflict file isn't there anymore.")
    original = path.with_name(_original_name(path.name))
    if action == "keep_current":
        path.unlink()
        return f"Kept the current {original.name} and deleted the other copy."
    if action != "use_other":
        raise ConfigError("Unknown choice")
    text = path.read_text(encoding="utf-8")
    if original.name == "config.toml":
        try:
            parse_config(tomllib.loads(text), require_token=False)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"The other copy of config.toml has a typo, so it can't be used: {e}") from e
    elif original.suffix == ".json":
        try:
            json.loads(text)
        except ValueError as e:
            raise ConfigError(f"The other copy of {original.name} is damaged, so it can't be used.") from e
    os.replace(path, original)
    return f"Now using the other copy of {original.name}."
