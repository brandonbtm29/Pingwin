"""Personalities: swappable message templates so the bot's voice is configurable."""

from __future__ import annotations

import random
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import PROJECT_ROOT, ConfigError, data_dir

BUNDLED_PERSONAS = PROJECT_ROOT / "personas"
_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# Used when a persona file leaves a message out, so every persona keeps working.
DEFAULT_MESSAGES: dict[str, str] = {
    "role_added": "Added you to **{role}**.",
    "role_removed": "Removed you from **{role}**.",
    "not_allowed": "You need the **{required}** role first.",
    "role_missing": "That role doesn't exist anymore. Ask a mod to repost the menu.",
    "no_permission": "I'm not allowed to manage **{role}**. A mod needs to move my role higher.",
    "menu_footer": "Tap again to opt out",
    "about": "I'm {name}.",
    "reloaded": "Settings reloaded.",
    "movie_nominated": "🎬 **{title}** joined the {series} watchlist (suggested by {by}).",
    "movie_rated": "Thanks! You gave it **{score}/10**. ⭐",
    "movie_rate_prompt": "How was it? Rate it out of 10 and say what you thought. Ratings close {closes}.",
    "movie_summary": "📊 **{title}**: {count}, group average **{avg}/10**",
    "movie_poll_none": "🍿 The {series} poll closed with no votes.",
    "movie_poll_tie": "🍿 It's a tie between {names}! A mod will pick (`/pingwin movie tonight`).",
    "movie_poll_winner": "🍿 The votes are in! **{title}** wins with {votes}{extra}.",
    "gameday_reminder": "⏰ **{title}** starts {when}!",
    "gameday_starting": "🎮 **{title}** is starting now!",
    "event_new": "📅 **New event: {event}**\n{when}",
    "event_soon": "⏰ **{event}** starts {when}!",
    "event_starting": "▶️ **{event}** is starting now!",
    "checkin_open": "📍 **{event}** has started! Here? Tap to check in.",
    "franchise_next": "🎬 {series}: **{title}**, part {part} of {total} of {franchise}. Starts {when}.",
    "franchise_done": "🏁 That's all of **{franchise}**! Time to pick the next franchise: "
    "`/pingwin movie poll {short}`.",
    "franchise_poll_winner": "🍿 The next franchise is **{title}** ({votes}{extra}). A mod will line up its movies.",
}


class _KeepUnknown(dict):
    """Leaves unknown {placeholders} as-is instead of crashing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@dataclass
class Persona:
    name: str
    nickname: str | None = None
    messages: dict[str, list[str]] = field(default_factory=dict)

    def say(self, key: str, **values: object) -> str:
        options = self.messages.get(key) or [DEFAULT_MESSAGES.get(key, key)]
        template = random.choice(options)
        try:
            return template.format_map(_KeepUnknown(name=self.name, **{k: str(v) for k, v in values.items()}))
        except (ValueError, IndexError, AttributeError, KeyError):
            # A stray "{" or "{0}" in a template: show it raw rather than failing the reply.
            return template


def persona_path(name: str, directory: Path | None = None) -> Path:
    """Find a persona file: the shared data folder first, then the ones bundled with the code."""
    if not _VALID_NAME.match(name):
        raise ConfigError(f"Persona names can only use letters, numbers, - and _: {name!r}")
    folders = [directory] if directory else [data_dir() / "personas", BUNDLED_PERSONAS]
    for folder in folders:
        path = folder / f"{name}.toml"
        if path.exists():
            return path
    raise ConfigError(f"No persona named {name!r} in " + " or ".join(str(f) for f in folders))


def load_persona(name: str, directory: Path | None = None) -> Persona:
    path = persona_path(name, directory)
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path.name} has a typo: {e}") from e

    messages: dict[str, list[str]] = {}
    for key, value in data.get("messages", {}).items():
        if isinstance(value, str):
            value = [value]
        if not (isinstance(value, list) and value and all(isinstance(v, str) for v in value)):
            raise ConfigError(f"{path.name}: messages.{key} must be text or a non-empty list of text")
        messages[key] = value

    return Persona(
        name=str(data.get("name", "Pingwin")).strip() or "Pingwin",
        nickname=str(data.get("nickname", "")).strip()[:32] or None,  # Discord nickname limit is 32
        messages=messages,
    )
