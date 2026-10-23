"""Per-member time zones.

Discord shows <t:...> timestamps in each reader's own time, so posts already read correctly for
everyone. Zones matter when someone *types* a time ("fri 8pm") and for things that happen at a
local hour (birthday shout-outs). Members pick theirs with /pingwin timezone set; anyone who hasn't uses
the server's time zone from the Features tab.
"""

from __future__ import annotations

from datetime import tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from . import store

STATE = "timezones"

# Friendly names shown first in /pingwin timezone set.
COMMON: list[tuple[str, str]] = [
    ("Eastern", "America/New_York"),
    ("Central", "America/Chicago"),
    ("Mountain", "America/Denver"),
    ("Mountain (Arizona, no DST)", "America/Phoenix"),
    ("Pacific", "America/Los_Angeles"),
    ("Alaska", "America/Anchorage"),
    ("Hawaii", "Pacific/Honolulu"),
    ("UK", "Europe/London"),
    ("Central Europe", "Europe/Berlin"),
    ("UTC", "UTC"),
]

# Words people put after a time: "8pm MT", "7:30pm eastern".
ABBREVIATIONS: dict[str, str] = {
    **{w: "America/New_York" for w in ("et", "est", "edt", "eastern")},
    **{w: "America/Chicago" for w in ("ct", "cst", "cdt", "central")},
    **{w: "America/Denver" for w in ("mt", "mst", "mdt", "mountain")},
    **{w: "America/Los_Angeles" for w in ("pt", "pst", "pdt", "pacific")},
    **{w: "America/Phoenix" for w in ("az", "arizona")},
    **{w: "America/Anchorage" for w in ("akt", "akst", "akdt", "alaska")},
    **{w: "Pacific/Honolulu" for w in ("hst", "hawaii")},
    **{w: "UTC" for w in ("utc", "gmt", "z")},
}

_LABELS = {zone: label for label, zone in COMMON}


def zone(name: str | None) -> ZoneInfo | None:
    """A ZoneInfo from a zone name, a friendly name or an abbreviation; None if unknown."""
    if not name:
        return None
    key = name.strip()
    low = key.casefold()
    for label, z in COMMON:
        if low == label.casefold():
            return ZoneInfo(z)
    if low in ABBREVIATIONS:
        return ZoneInfo(ABBREVIATIONS[low])
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def split_zone(text: str) -> tuple[str, ZoneInfo | None]:
    """Strip a trailing zone word: 'fri 8pm MT' -> ('fri 8pm', Denver)."""
    words = text.strip().split()
    if len(words) >= 2:
        z = ABBREVIATIONS.get(words[-1].casefold().strip("()."))
        if z:
            return " ".join(words[:-1]), ZoneInfo(z)
    return text, None


def label(tz: tzinfo) -> str:
    """'Mountain' for America/Denver; otherwise the zone name."""
    key = getattr(tz, "key", None) or str(tz)
    return _LABELS.get(key, key)


def search(current: str, limit: int = 25) -> list[tuple[str, str]]:
    """(display, value) pairs for autocomplete: common zones first, then any matching zone name."""
    cur = current.strip().casefold()
    out = [(lbl, z) for lbl, z in COMMON if not cur or cur in lbl.casefold() or cur in z.casefold()]
    if cur:
        seen = {z for _, z in out}
        out += [(z, z) for z in sorted(available_timezones()) if cur in z.casefold() and z not in seen]
    return out[:limit]


def all_members() -> dict[str, str]:
    return store.load(STATE)


def get(user_id: int) -> ZoneInfo | None:
    return zone(store.load(STATE).get(str(user_id)))


def set_for(user_id: int, name: str | None) -> None:
    state = store.load(STATE)
    if name is None:
        state.pop(str(user_id), None)
    else:
        state[str(user_id)] = name
    store.save(STATE, state)


def for_member(user_id: int, default: tzinfo, saved: dict[str, str] | None = None) -> tzinfo:
    """The member's zone, or the server's if they haven't picked one."""
    name = (saved if saved is not None else store.load(STATE)).get(str(user_id))
    return zone(name) or default
