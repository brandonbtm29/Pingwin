"""Settings for Pingwin's optional features, defined once as a schema.

The same schema is used to read config.toml, to validate edits, and to draw the forms on the
dashboard's Features tab, so adding a setting means adding one line here.

Field types:
    toggle    true/false
    channel   a channel ID (shown as a channel picker)
    role      a role name (shown as a role picker; "" means none)
    number    a whole number with min/max
    text      one line of text
    textarea  several lines of text
    select    one of a few fixed choices
    secret    stored in secrets.env (never in config.toml), shown as set/not set
    rows      a list of rows; each row has its own columns (text, role, channel, tags)
"""

from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import ConfigError


def F(key: str, type_: str, label: str, default: Any = None, help: str = "", **extra: Any) -> dict[str, Any]:  # noqa: A002
    return {"key": key, "type": type_, "label": label, "default": default, "help": help, **extra}


SECTIONS: list[dict[str, Any]] = [
    {
        "key": "bot",
        "title": "General",
        "description": "Settings shared by the features below.",
        "fields": [
            F(
                "timezone",
                "text",
                "Time zone",
                "America/New_York",
                "For members who haven't picked their own with /timezone set. A name like America/New_York.",
            ),
        ],
    },
    {
        "key": "events",
        "title": "Event reminders",
        "description": "When someone creates a Discord Event, Pingwin pings the roles whose keywords match "
        "its name or description, then reminds them before it starts.",
        "fields": [
            F("enabled", "toggle", "Turn on event reminders", False),
            F("channel", "channel", "Post in", None, "For example #events."),
            F("announce_new", "toggle", "Announce new events", True),
            F("remind_minutes", "number", "Remind this many minutes before (0 = don't)", 60, min=0, max=1440),
            F("remind_at_start", "toggle", "Post when it starts", True),
            F("ping_interested", "toggle", "Also ping people who clicked Interested", False),
            F(
                "rules",
                "rows",
                "Which roles to ping",
                [],
                "An event pings a role when its name or description contains any of that role's keywords.",
                columns=[
                    {"key": "role", "type": "role", "label": "Role"},
                    {"key": "keywords", "type": "tags", "label": "Keywords (comma separated)"},
                ],
            ),
        ],
    },
    {
        "key": "birthdays",
        "title": "Birthdays",
        "description": "Members add theirs with /birthday set (month and day only). Pingwin posts a shout-out "
        "that morning.",
        "fields": [
            F("enabled", "toggle", "Turn on birthdays", False),
            F("channel", "channel", "Post in", None, "For example #birthdays."),
            F("hour", "number", "Post at this hour (0-23, in each member's own time zone)", 9, min=0, max=23),
            F(
                "message",
                "textarea",
                "Message",
                "🎂 Happy birthday {mentions}! 🎉",
                "{mentions} becomes the birthday people.",
            ),
            F("ping_role", "role", "Also ping this role", "", "Optional, for example Birthday Brigade."),
            F("birthday_role", "role", "Give this role for the day", "", "Optional."),
        ],
    },
    {
        "key": "movienight",
        "title": "Movie night",
        "description": "Members nominate movies to a watchlist; a mod turns them into a Discord poll that pings "
        "the series' role and announces the winner. Each series (like MMM or FFF) has its own watchlist.",
        "fields": [
            F("enabled", "toggle", "Turn on movie night", False),
            F(
                "series",
                "rows",
                "Series",
                [],
                "Each series gets its own watchlist, role and channel. With a showtime (like Mon 8:30pm, in the time "
                "zone under General), polls close right when the movie starts.",
                columns=[
                    {"key": "name", "type": "text", "label": "Name"},
                    {"key": "role", "type": "role", "label": "Role to ping"},
                    {"key": "channel", "type": "channel", "label": "Channel"},
                    {"key": "showtime", "type": "text", "label": "Showtime (e.g. Mon 8:30pm)"},
                ],
            ),
            F(
                "nominate",
                "select",
                "Who can nominate",
                "everyone",
                options=[["everyone", "Everyone"], ["role", "Members with a role"], ["mods", "Mods only"]],
            ),
            F("nominate_role", "role", "Role that can nominate", "", "Used when 'Members with a role' is picked."),
            F("max_per_person", "number", "Nominations per person per series (0 = no limit)", 3, min=0, max=50),
            F(
                "run_polls",
                "select",
                "Who can start polls",
                "mods",
                options=[["mods", "Mods only"], ["role", "Members with a role"], ["everyone", "Everyone"]],
            ),
            F("run_role", "role", "Role that can start polls", "", "Used when 'Members with a role' is picked."),
            F(
                "pick",
                "select",
                "Poll options come from",
                "choose",
                options=[
                    ["choose", "Whoever starts the poll picks them"],
                    ["oldest", "The oldest nominations"],
                    ["random", "Random nominations"],
                ],
            ),
            F("poll_options", "number", "Movies per poll", 10, min=2, max=10),
            F("poll_hours", "number", "Poll length in hours (default)", 24, min=1, max=768),
            F("multiple", "toggle", "Let people vote for more than one", False),
            F("mark_watched", "toggle", "Mark the winner as watched", True),
            F(
                "tie",
                "select",
                "On a tie",
                "mods",
                options=[["mods", "Announce the tie; a mod decides"], ["random", "Pick one of the tied at random"]],
            ),
        ],
    },
    {
        "key": "gameday",
        "title": "Game day sign-ups",
        "description": "/gameday create posts a 'Who's in?' card with buttons, then reminds everyone who said yes.",
        "fields": [
            F("enabled", "toggle", "Turn on game day sign-ups", False),
            F("channel", "channel", "Default channel", None, "Used when /gameday is run outside a text channel."),
            F("role", "role", "Ping this role for new game days", "", "For example Gamer."),
            F("remind_minutes", "number", "Remind sign-ups this many minutes before (0 = don't)", 60, min=0, max=1440),
        ],
    },
    {
        "key": "streams",
        "title": "Stream alerts",
        "description": "Pingwin posts once when a streamer goes live: on Twitch, on YouTube, or with Go Live "
        "(screen share) in a Discord voice channel.",
        "fields": [
            F("enabled", "toggle", "Turn on stream alerts", False),
            F(
                "discord_live",
                "toggle",
                "Alert when they Go Live in a Discord voice channel",
                True,
                "Needs only their Discord username below; no keys.",
            ),
            F("channel", "channel", "Post in", None, "For example #stream."),
            F("role", "role", "Ping this role", "", "For example Stream Squad."),
            F("check_minutes", "number", "Check every (minutes)", 3, min=2, max=60),
            F(
                "message",
                "textarea",
                "Message",
                "🔴 **{name}** is live! {title}\n{links}",
                "{name}, {title}, {game} and {links} are filled in.",
            ),
            F(
                "streamers",
                "rows",
                "Streamers",
                [],
                "Their Discord username for Go Live alerts, and/or Twitch login and YouTube handle (like @name).",
                columns=[
                    {"key": "name", "type": "text", "label": "Name"},
                    {"key": "discord", "type": "text", "label": "Discord username"},
                    {"key": "twitch", "type": "text", "label": "Twitch login"},
                    {"key": "youtube", "type": "text", "label": "YouTube handle"},
                ],
            ),
            F(
                "TWITCH_CLIENT_ID",
                "secret",
                "Twitch client ID",
                None,
                "From dev.twitch.tv/console → Register Your Application (Category: Other).",
            ),
            F("TWITCH_CLIENT_SECRET", "secret", "Twitch client secret", None),
            F(
                "YOUTUBE_API_KEY",
                "secret",
                "YouTube API key",
                None,
                "From console.cloud.google.com → APIs & Services → enable YouTube Data API v3 → Credentials.",
            ),
        ],
    },
    {
        "key": "attendance",
        "title": "Event attendance and Frumples",
        "description": "Pingwin takes attendance at Discord Events: everyone who stays in the event's voice channel "
        'long enough counts, and in-person events get an "I\'m here" button. When the event ends it adds Frumples '
        "in UnbelievaBoat and posts a recap.",
        "fields": [
            F("enabled", "toggle", "Turn on attendance", False),
            F("channel", "channel", "Post recaps and check-in buttons in", None, "For example #events."),
            F("currency", "text", "Currency name", "Frumples"),
            F("amount", "number", "Frumples per event attended", 10, min=0, max=100000),
            F("host_bonus", "number", "Extra for the host (whoever created the event)", 0, min=0, max=100000),
            F(
                "min_minutes",
                "number",
                "Minutes in the voice channel to count (shorter events need half their length)",
                30,
                min=1,
                max=600,
            ),
            F(
                "checkin",
                "select",
                '"I\'m here" button',
                "external",
                options=[
                    ["external", "Only for in-person events (location set to Somewhere Else)"],
                    ["all", "For every event"],
                    ["off", "Never"],
                ],
            ),
            F(
                "require_code",
                "toggle",
                "Check-in needs a code the host announces (they get it with /attendance code)",
                False,
            ),
            F(
                "payout",
                "select",
                "Pay Frumples",
                "unbelievaboat",
                options=[["unbelievaboat", "Into UnbelievaBoat balances"], ["off", "Don't pay; just keep attendance"]],
            ),
            F(
                "balance",
                "select",
                "UnbelievaBoat balance to add to",
                "cash",
                options=[["cash", "Cash"], ["bank", "Bank"]],
            ),
            F(
                "UNBELIEVABOAT_TOKEN",
                "secret",
                "UnbelievaBoat API token",
                None,
                "unbelievaboat.com/applications → Create Application → copy the token, then use its Authorize link to "
                "add it to your server (needs the Economy permission).",
            ),
        ],
    },
    {
        "key": "welcome",
        "title": "Welcome and approval",
        "description": "New members get a welcome message, and mods get a card with Approve and Deny buttons. "
        "Approving gives the member's role. Needs the Server Members intent.",
        "needs": ["members"],
        "fields": [
            F("enabled", "toggle", "Turn on welcome and approval", False),
            F("welcome_channel", "channel", "Welcome new members in", None, "For example #welcome."),
            F(
                "welcome_message",
                "textarea",
                "Welcome message",
                "Welcome {mention}! 👋 A mod will let you in shortly.",
                "{mention} and {name} are filled in.",
            ),
            F("approval_channel", "channel", "Approval cards go to", None, "A mod-only channel."),
            F("approve_role", "role", "Approving gives this role", "Friend"),
            F("mod_role", "role", "Who can approve", "", "Blank = anyone with the Manage Roles permission."),
            F("approved_channel", "channel", "Announce approved members in", None, "Optional."),
            F("approved_message", "textarea", "Announcement", "Say hi to {mention}! 🎉"),
            F(
                "deny_action",
                "select",
                "Deny button",
                "nothing",
                options=[["nothing", "Just mark the card"], ["kick", "Kick the member (needs Kick Members)"]],
            ),
        ],
    },
    {
        "key": "modlog",
        "title": "Mod log",
        "description": "Records server activity in a private channel. Needs the Server Members intent, and the "
        "Message Content intent for message edits and deletes.",
        "needs": ["members"],
        "fields": [
            F("enabled", "toggle", "Turn on the mod log", False),
            F("channel", "channel", "Log channel", None, "A mod-only channel."),
            F("joins", "toggle", "Joins", True),
            F("leaves", "toggle", "Leaves, kicks and bans", True),
            F("roles", "toggle", "Role changes", True),
            F("ignore_menu_roles", "toggle", "Skip ping-menu roles (they change often)", True),
            F("messages", "toggle", "Edited and deleted messages (needs Message Content)", True),
        ],
    },
]

SECTION_BY_KEY = {s["key"]: s for s in SECTIONS}
_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _SLUG.sub("-", text.casefold()).strip("-")[:40] or "series"


def _where(section: dict, field: dict) -> str:
    return f"{section['title']} → {field['label']}"


def _parse_value(section: dict, field: dict, raw: Any) -> Any:
    t = field["type"]
    if raw is None:
        raw = field["default"]
    if t == "toggle":
        return bool(raw)
    if t == "channel":
        if raw in (None, "", 0, "0"):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{_where(section, field)}: not a channel ID ({raw!r})") from e
    if t in ("role", "text", "textarea"):
        value = "" if raw is None else str(raw)
        return value.strip() if t != "textarea" else value
    if t == "number":
        try:
            value = int(raw)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{_where(section, field)}: must be a whole number") from e
        lo, hi = field.get("min"), field.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise ConfigError(f"{_where(section, field)}: must be between {lo} and {hi}")
        return value
    if t == "select":
        allowed = [o[0] for o in field["options"]]
        if raw not in allowed:
            raise ConfigError(f"{_where(section, field)}: must be one of {', '.join(allowed)}")
        return raw
    if t == "rows":
        if not isinstance(raw, list):
            raise ConfigError(f"{_where(section, field)}: must be a list")
        rows = []
        for item in raw:
            if not isinstance(item, dict):
                raise ConfigError(f"{_where(section, field)}: each row must be a table")
            row = {}
            for col in field["columns"]:
                v = item.get(col["key"])
                if col["type"] == "tags":
                    if isinstance(v, str):
                        v = [p for p in (x.strip() for x in v.split(",")) if p]
                    row[col["key"]] = tuple(str(x).strip() for x in (v or []) if str(x).strip())
                elif col["type"] == "channel":
                    row[col["key"]] = _parse_value(section, {**field, "type": "channel", "default": None}, v)
                else:
                    row[col["key"]] = "" if v is None else str(v).strip()
            if any(row.values()):
                rows.append(SimpleNamespace(**row))
        return tuple(rows)
    if t == "secret":
        return None  # secrets are read from the environment, see Features.secret()
    raise ConfigError(f"Unknown field type {t}")


class Features:
    """Parsed feature settings: features.events.enabled, features.movienight.series, ..."""

    def __init__(self, sections: dict[str, SimpleNamespace]) -> None:
        self._sections = sections
        for key, ns in sections.items():
            setattr(self, key, ns)

    @property
    def needs_members(self) -> bool:
        return bool(self.welcome.enabled or self.modlog.enabled)

    @property
    def needs_message_content(self) -> bool:
        return bool(self.modlog.enabled and self.modlog.messages)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.bot.timezone)

    @staticmethod
    def secret(name: str) -> str:
        import os

        return os.environ.get(name, "").strip()

    def as_json(self) -> dict[str, dict[str, Any]]:
        """Values for the dashboard: IDs as strings, rows as lists of dicts."""
        out: dict[str, dict[str, Any]] = {}
        for section in SECTIONS:
            ns = self._sections[section["key"]]
            values: dict[str, Any] = {}
            for field in section["fields"]:
                v = getattr(ns, field["key"], None)
                if field["type"] == "channel":
                    values[field["key"]] = str(v) if v else ""
                elif field["type"] == "secret":
                    values[field["key"]] = bool(self.secret(field["key"]))
                elif field["type"] == "rows":
                    rows = []
                    for r in v or ():
                        row = {}
                        for col in field["columns"]:
                            cv = getattr(r, col["key"])
                            if col["type"] == "tags":
                                cv = ", ".join(cv)
                            elif col["type"] == "channel":
                                cv = str(cv) if cv else ""
                            row[col["key"]] = cv
                        rows.append(row)
                    values[field["key"]] = rows
                else:
                    values[field["key"]] = v
            out[section["key"]] = values
        return out


def parse_features(data: dict[str, Any]) -> Features:
    sections: dict[str, SimpleNamespace] = {}
    for section in SECTIONS:
        raw = data.get(section["key"], {})
        if not isinstance(raw, dict):
            raise ConfigError(f"[{section['key']}] must be a table")
        values = {f["key"]: _parse_value(section, f, raw.get(f["key"])) for f in section["fields"]}
        sections[section["key"]] = SimpleNamespace(**values)
    features = Features(sections)
    try:
        ZoneInfo(features.bot.timezone)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ConfigError(f"General → Time zone: unknown time zone {features.bot.timezone!r}") from e
    from .timeparse import next_weekly

    for s in features.movienight.series:
        if getattr(s, "showtime", "") and next_weekly(s.showtime, datetime.now(features.tz)) is None:
            raise ConfigError(f"Movie night → Series: can't read the showtime {s.showtime!r} (try Mon 8:30pm)")
    keys = [slug(s.name) for s in features.movienight.series if s.name]
    if len(set(keys)) != len(keys):
        raise ConfigError("Movie night → Series: two series have the same name")
    return features


def to_toml_value(field: dict[str, Any], value: Any) -> Any:
    """Turn a dashboard value into what config.toml stores."""
    t = field["type"]
    if t == "toggle":
        return bool(value)
    if t == "channel":
        return int(value) if str(value or "").strip().isdigit() else 0
    if t == "number":
        try:
            return int(value)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{field['label']}: must be a whole number") from e
    if t == "rows":
        rows = []
        for item in value or []:
            row = {}
            for col in field["columns"]:
                v = item.get(col["key"], "")
                if col["type"] == "tags":
                    v = [p.strip() for p in str(v).split(",") if p.strip()]
                elif col["type"] == "channel":
                    v = int(v) if str(v or "").strip().isdigit() else 0
                else:
                    v = str(v or "").strip()
                row[col["key"]] = v
            if any(row.values()):
                rows.append(row)
        return rows
    return "" if value is None else str(value)
