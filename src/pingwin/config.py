"""Settings: where they live, and loading config.toml plus the bot token.

Two kinds of settings:

* **Shared** (identical on every PC that runs Pingwin) live in the *data folder*,
  normally a SyncThing folder set with PINGWIN_DATA:
      config.toml   main settings
      secrets.env   DISCORD_TOKEN
      personas/     custom personalities (optional)
* **Per-PC** settings live in the project's own .env (never synced):
      PINGWIN_DATA      path to the data folder
      PINGWIN_NODE      this PC's name, as listed in [failover] pcs (defaults to the computer name)
"""

from __future__ import annotations

import os
import socket
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

MAX_MENU_ROLES = 25  # Discord allows 5 rows x 5 buttons per message
MAX_LABEL_LENGTH = 80  # Discord's button label limit


class ConfigError(Exception):
    """Raised when config.toml or a persona file is missing or invalid."""


def data_dir() -> Path:
    """The shared settings folder (PINGWIN_DATA), or the project folder if unset."""
    raw = os.environ.get("PINGWIN_DATA", "").strip().strip('"')
    return Path(raw).expanduser() if raw else PROJECT_ROOT


def config_path() -> Path:
    raw = os.environ.get("PINGWIN_CONFIG", "").strip().strip('"')
    return Path(raw) if raw else data_dir() / "config.toml"


@dataclass(frozen=True)
class Node:
    """This PC's identity when several PCs run Pingwin."""

    name: str


def load_node() -> Node:
    name = os.environ.get("PINGWIN_NODE", "").strip() or socket.gethostname()
    return Node(name=name[:40])


@dataclass(frozen=True)
class MenuRole:
    name: str
    emoji: str | None = None
    description: str = ""


@dataclass(frozen=True)
class RolesConfig:
    required_role: str | None
    menu_title: str
    menu_description: str
    menu: tuple[MenuRole, ...]

    def is_menu_role(self, role_name: str) -> bool:
        return role_name.casefold() in {m.name.casefold() for m in self.menu}


@dataclass(frozen=True)
class FailoverConfig:
    enabled: bool
    status_channel_id: int | None
    heartbeat_seconds: int
    takeover_after_seconds: int
    pcs: tuple[str, ...] = ()  # in order of preference

    @property
    def active(self) -> bool:
        return self.enabled and self.status_channel_id is not None

    def rank(self, pc: str) -> int:
        """1 = first choice. PCs not in the list come after all listed ones."""
        key = pc.casefold()
        for i, name in enumerate(self.pcs, start=1):
            if name.casefold() == key:
                return i
        return len(self.pcs) + 1

    def describe(self, pc: str) -> str:
        rank = self.rank(pc)
        if rank > len(self.pcs):
            return "not in the backup list"
        return "main PC" if rank == 1 else f"backup #{rank - 1}"


@dataclass(frozen=True)
class DashboardConfig:
    enabled: bool = True
    port: int = 8787


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int | None
    persona: str
    roles: RolesConfig
    failover: FailoverConfig
    update_check_minutes: int
    dashboard: DashboardConfig = DashboardConfig()


def _int(value: object, field: str, *, minimum: int | None = None) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{field} must be a whole number, got {value!r}") from e
    if minimum is not None and number < minimum:
        raise ConfigError(f"{field} must be at least {minimum}, got {number}")
    return number


def _parse_menu(raw: object) -> tuple[MenuRole, ...]:
    if not isinstance(raw, list):
        raise ConfigError("[[roles.menu]] must be a list of role blocks")
    menu: list[MenuRole] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"roles.menu entry {i} is not a table")
        name = str(entry.get("name", "")).strip()
        if not name:
            raise ConfigError(f"roles.menu entry {i} is missing a name")
        if len(name) > MAX_LABEL_LENGTH:
            raise ConfigError(f"Role name is too long for a button (max {MAX_LABEL_LENGTH}): {name}")
        key = name.casefold()
        if key in seen:
            raise ConfigError(f"Role listed twice in the menu: {name}")
        seen.add(key)
        emoji = str(entry.get("emoji") or "").strip() or None
        menu.append(MenuRole(name=name, emoji=emoji, description=str(entry.get("description", "")).strip()))
    if len(menu) > MAX_MENU_ROLES:
        raise ConfigError(f"The menu has {len(menu)} roles; Discord allows at most {MAX_MENU_ROLES} buttons")
    return tuple(menu)


def _parse_failover(raw: dict) -> FailoverConfig:
    heartbeat = _int(raw.get("heartbeat_seconds", 30), "failover.heartbeat_seconds", minimum=10)
    takeover = _int(raw.get("takeover_after_seconds", 120), "failover.takeover_after_seconds", minimum=30)
    if takeover < heartbeat * 2 + 10:
        raise ConfigError(
            "failover.takeover_after_seconds must be well above heartbeat_seconds "
            f"(at least {heartbeat * 2 + 10} with a {heartbeat}s heartbeat)"
        )
    pcs = raw.get("pcs", [])
    if not isinstance(pcs, list) or not all(isinstance(p, str) and p.strip() for p in pcs):
        raise ConfigError('failover.pcs must be a list of PC names, e.g. ["MAIN-PC", "FRIENDS-PC"]')
    names = [p.strip() for p in pcs]
    if len({n.casefold() for n in names}) != len(names):
        raise ConfigError("failover.pcs lists the same PC twice")
    return FailoverConfig(
        pcs=tuple(names),
        enabled=bool(raw.get("enabled", False)),
        status_channel_id=_int(raw.get("status_channel_id", 0), "failover.status_channel_id") or None,
        heartbeat_seconds=heartbeat,
        takeover_after_seconds=takeover,
    )


def _parse_dashboard(raw: dict) -> DashboardConfig:
    port = _int(os.environ.get("PINGWIN_DASHBOARD_PORT") or raw.get("port", 8787), "dashboard.port", minimum=1024)
    if port > 65535:
        raise ConfigError(f"dashboard.port must be at most 65535, got {port}")
    return DashboardConfig(enabled=bool(raw.get("enabled", True)), port=port)


def load_config(path: Path | None = None, *, require_token: bool = True) -> Config:
    path = path or config_path()
    if not path.exists():
        raise ConfigError(f"No config file at {path}. Copy config.example.toml there as config.toml.")
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path.name} has a typo: {e}") from e
    return parse_config(data, require_token=require_token)


def parse_config(data: dict, *, require_token: bool = True) -> Config:
    """Build a Config from already-parsed TOML (also used to validate edits before saving)."""
    bot = data.get("bot", {})
    roles = data.get("roles", {})

    guild_id = _int(os.environ.get("PINGWIN_GUILD_ID") or bot.get("guild_id") or 0, "guild_id") or None

    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if require_token and not token:
        raise ConfigError(f"DISCORD_TOKEN is not set. Put it in {data_dir() / 'secrets.env'} (DISCORD_TOKEN=...).")

    return Config(
        token=token,
        guild_id=guild_id,
        persona=str(bot.get("persona", "pingwin")).strip() or "pingwin",
        roles=RolesConfig(
            required_role=str(roles.get("required_role", "")).strip() or None,
            menu_title=str(roles.get("menu_title", "Pick your pings")),
            menu_description=str(roles.get("menu_description", "")),
            menu=_parse_menu(roles.get("menu", [])),
        ),
        failover=_parse_failover(data.get("failover", {})),
        dashboard=_parse_dashboard(data.get("dashboard", {})),
        update_check_minutes=_int(data.get("updates", {}).get("check_minutes", 30), "updates.check_minutes", minimum=0),
    )
