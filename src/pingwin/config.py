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
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

MAX_MENU_ROLES = 25  # Discord allows 5 rows x 5 buttons per message
MAX_LABEL_LENGTH = 80  # Discord's button label limit


class ConfigError(Exception):
    """Raised when config.toml or a persona file is missing or invalid."""


class MissingSettings(ConfigError):
    """config.toml or the token isn't there (yet), e.g. still syncing to a new backup PC."""


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
    zone: str | None = None  # time zone menus: picking this role sets the member's time zone


MENU_MODES = ("many", "one")  # many: toggle each role; one: picking a role swaps out the others


@dataclass(frozen=True)
class MenuConfig:
    """One button menu. "main" is the original ping menu; others are extra menus like a time zone picker."""

    key: str
    title: str
    description: str
    mode: str
    roles: tuple[MenuRole, ...]

    @property
    def is_timezone(self) -> bool:
        return any(r.zone for r in self.roles)


@dataclass(frozen=True)
class RolesConfig:
    required_role: str | None
    menu_title: str
    menu_description: str
    menu: tuple[MenuRole, ...]
    extra: tuple[MenuConfig, ...] = ()

    @property
    def menus(self) -> tuple[MenuConfig, ...]:
        main = MenuConfig("main", self.menu_title, self.menu_description, "many", self.menu)
        return (main, *self.extra)

    def get_menu(self, key: str | None) -> MenuConfig | None:
        key = (key or "main").casefold()
        return next((m for m in self.menus if m.key.casefold() == key), None)

    def find_role(self, role_name: str) -> tuple[MenuConfig, MenuRole] | None:
        key = role_name.casefold()
        for menu in self.menus:
            for r in menu.roles:
                if r.name.casefold() == key:
                    return menu, r
        return None

    def is_menu_role(self, role_name: str) -> bool:
        return self.find_role(role_name) is not None


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
    # Who may sign in with Discord from outside this PC: anyone with Manage Server, plus this role.
    admin_role: str | None = None
    # Signed-in admins may also add/remove backup PCs and change remote access (normally only this PC's owner).
    remote_full_access: bool = False


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int | None
    persona: str
    roles: RolesConfig
    failover: FailoverConfig
    update_check_minutes: int
    dashboard: DashboardConfig = DashboardConfig()
    features: Any = None  # pingwin.features.Features


def _int(value: object, field: str, *, minimum: int | None = None) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{field} must be a whole number, got {value!r}") from e
    if minimum is not None and number < minimum:
        raise ConfigError(f"{field} must be at least {minimum}, got {number}")
    return number


def _parse_menu(raw: object, where: str = "roles.menu") -> tuple[MenuRole, ...]:
    if not isinstance(raw, list):
        raise ConfigError(f"[[{where}]] must be a list of role blocks")
    menu: list[MenuRole] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} entry {i} is not a table")
        name = str(entry.get("name", "")).strip()
        if not name:
            raise ConfigError(f"{where} entry {i} is missing a name")
        if len(name) > MAX_LABEL_LENGTH:
            raise ConfigError(f"Role name is too long for a button (max {MAX_LABEL_LENGTH}): {name}")
        key = name.casefold()
        if key in seen:
            raise ConfigError(f"Role listed twice in the menu: {name}")
        seen.add(key)
        emoji = str(entry.get("emoji") or "").strip() or None
        zone = str(entry.get("zone") or "").strip() or None
        if zone:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError) as e:
                raise ConfigError(f"{name}: unknown time zone {zone!r} (use a name like America/Denver)") from e
        menu.append(MenuRole(name=name, emoji=emoji, description=str(entry.get("description", "")).strip(), zone=zone))
    if len(menu) > MAX_MENU_ROLES:
        raise ConfigError(f"A menu has {len(menu)} roles; Discord allows at most {MAX_MENU_ROLES} buttons")
    return tuple(menu)


def _menu_key(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:40]


def _parse_extra_menus(raw: object, main: tuple[MenuRole, ...]) -> tuple[MenuConfig, ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise ConfigError("[[roles.extra]] must be a list of menus")
    out: list[MenuConfig] = []
    keys = {"main"}
    seen_roles = {m.name.casefold(): "the ping menu" for m in main}
    for i, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"roles.extra menu {i} is not a table")
        title = str(entry.get("title", "")).strip() or f"Menu {i + 1}"
        key = _menu_key(str(entry.get("key", "")) or title) or f"menu-{i + 1}"
        if key in keys:
            raise ConfigError(f"Two menus share the name {key!r}; give each menu its own title")
        keys.add(key)
        mode = str(entry.get("mode", "many")).strip().lower()
        if mode not in MENU_MODES:
            raise ConfigError(f"Menu {title!r}: mode must be 'many' or 'one'")
        roles = _parse_menu(entry.get("roles", []), f"roles.extra.roles (menu {title!r})")
        for r in roles:
            other = seen_roles.get(r.name.casefold())
            if other:
                raise ConfigError(f"{r.name} is in two menus ({other} and {title!r}); a role can only be in one")
            seen_roles[r.name.casefold()] = repr(title)
        out.append(
            MenuConfig(
                key=key, title=title, description=str(entry.get("description", "")).strip(), mode=mode, roles=roles
            )
        )
    return tuple(out)


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
    return DashboardConfig(
        enabled=bool(raw.get("enabled", True)),
        port=port,
        admin_role=str(raw.get("admin_role", "")).strip() or None,
        remote_full_access=bool(raw.get("remote_full_access", False)),
    )


def _parse_features(data: dict):
    from .features import parse_features  # imported here to avoid a circular import

    return parse_features(data)


def load_config(path: Path | None = None, *, require_token: bool = True) -> Config:
    path = path or config_path()
    if not path.exists():
        raise MissingSettings(f"No config file at {path}. Copy config.example.toml there as config.toml.")
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

    main_menu = _parse_menu(roles.get("menu", []))
    guild_id = _int(os.environ.get("PINGWIN_GUILD_ID") or bot.get("guild_id") or 0, "guild_id") or None

    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if require_token and not token:
        raise MissingSettings(f"DISCORD_TOKEN is not set. Put it in {data_dir() / 'secrets.env'} (DISCORD_TOKEN=...).")

    return Config(
        token=token,
        guild_id=guild_id,
        persona=str(bot.get("persona", "pingwin")).strip() or "pingwin",
        roles=RolesConfig(
            required_role=str(roles.get("required_role", "")).strip() or None,
            menu_title=str(roles.get("menu_title", "Pick your pings")),
            menu_description=str(roles.get("menu_description", "")),
            menu=main_menu,
            extra=_parse_extra_menus(roles.get("extra", []), main_menu),
        ),
        failover=_parse_failover(data.get("failover", {})),
        dashboard=_parse_dashboard(data.get("dashboard", {})),
        features=_parse_features(data),
        update_check_minutes=_int(data.get("updates", {}).get("check_minutes", 30), "updates.check_minutes", minimum=0),
    )
