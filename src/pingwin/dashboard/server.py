"""Pingwin's local dashboard: settings and controls in a browser, on this PC only.

Security model
* Listens on 127.0.0.1 only, and rejects requests whose Host isn't localhost
  (blocks DNS-rebinding tricks from websites).
* Needs a secret key, stored in the project folder (.dashboard-key). Opening
  scripts\\open_dashboard.cmd signs you in with it and sets an HttpOnly,
  SameSite=Strict cookie.
* Changes (POST) also need an `X-Pingwin: 1` header, which other websites
  can't send to it, so they can't change settings behind your back.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import math
import secrets
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import discord
from aiohttp import web

from .. import REPO_URL, __version__, invite_url, menus, updater
from ..config import PROJECT_ROOT, ConfigError, MenuRole, config_path, data_dir
from ..configedit import save_failover, save_roles
from .setup_check import checklist, gather_facts, summarize

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.dashboard")

STATIC = Path(__file__).with_name("static")
STATIC_FILES = {"app.js": "text/javascript", "style.css": "text/css"}
KEY_FILE = PROJECT_ROOT / ".dashboard-key"
URL_FILE = PROJECT_ROOT / ".dashboard-url"
COOKIE = "pingwin_dashboard"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
LOG_FILE = PROJECT_ROOT / "logs" / "pingwin.log"

BOT: web.AppKey[Any] = web.AppKey("bot", object)
KEY: web.AppKey[str] = web.AppKey("key", str)

LOCKED_PAGE = """<!doctype html><meta charset="utf-8"><title>Pingwin</title>
<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem;line-height:1.5">
<h1>🐧 Pingwin</h1><p>To open the dashboard, double-click <code>scripts\\open_dashboard.cmd</code>
in the Pingwin folder. It signs you in on this PC.</p></body>"""

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://cdn.discordapp.com; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def load_or_create_key(path: Path = KEY_FILE) -> str:
    try:
        key = path.read_text(encoding="utf-8").strip()
        if len(key) >= 20:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_urlsafe(24)
    path.write_text(key + "\n", encoding="utf-8")
    return key


def _hostname(value: str | None) -> str:
    if not value:
        return ""
    return (urlparse(f"//{value}").hostname or "").lower()


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------- guard
@web.middleware
async def guard(request: web.Request, handler):
    if _hostname(request.host) not in LOCAL_HOSTS:
        return web.Response(status=403, text="Pingwin's dashboard only answers on this PC.")

    if request.path != "/login":
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), request.app[KEY]):
            if request.path.startswith("/api/"):
                return web.json_response({"error": "Not signed in. Open scripts\\open_dashboard.cmd."}, status=401)
            return web.Response(text=LOCKED_PAGE, content_type="text/html", status=401)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("Origin")
            if request.headers.get("X-Pingwin") != "1" or (
                origin and _hostname(urlparse(origin).netloc) not in LOCAL_HOSTS
            ):
                return web.json_response({"error": "Blocked: request didn't come from the dashboard."}, status=403)

    try:
        response = await handler(request)
    except ApiError as e:
        response = web.json_response({"error": str(e)}, status=e.status)
    except ConfigError as e:
        response = web.json_response({"error": str(e)}, status=400)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------- pages
async def login(request: web.Request) -> web.StreamResponse:
    if not hmac.compare_digest(request.query.get("key", ""), request.app[KEY]):
        return web.Response(text=LOCKED_PAGE, content_type="text/html", status=401)
    response = web.HTTPFound("/")
    response.set_cookie(COOKIE, request.app[KEY], httponly=True, samesite="Strict", path="/", max_age=365 * 86400)
    return response


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(STATIC / "index.html", headers={"Content-Type": "text/html; charset=utf-8"})


async def static(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if name not in STATIC_FILES:
        raise web.HTTPNotFound()
    return web.FileResponse(STATIC / name, headers={"Content-Type": f"{STATIC_FILES[name]}; charset=utf-8"})


# --------------------------------------------------------------------- state
def _guild(bot: Pingwin) -> discord.Guild | None:
    if bot.config.guild_id and (g := bot.get_guild(bot.config.guild_id)):
        return g
    return bot.guilds[0] if bot.guilds else None


def _guild_info(guild: discord.Guild | None) -> dict[str, Any]:
    if guild is None:
        return {"guild": None, "roles": [], "channels": []}
    me = guild.me
    roles = [
        {
            "id": str(r.id),
            "name": r.name,
            "color": str(r.color) if r.color.value else None,
            "mentionable": r.mentionable,
            "managed": r.managed,
            "has_permissions": bool(r.permissions.value),
            "assignable": r < me.top_role and not r.managed,
        }
        for r in sorted(guild.roles, key=lambda r: r.position, reverse=True)
        if not r.is_default()
    ]
    channels = [
        {"id": str(c.id), "name": c.name, "category": c.category.name if c.category else ""}
        for c in guild.text_channels
        if c.permissions_for(me).send_messages and c.permissions_for(me).view_channel
    ]
    return {
        "guild": {"id": str(guild.id), "name": guild.name, "icon": guild.icon.url if guild.icon else None},
        "bot_role": me.top_role.name,
        "can_manage_roles": me.guild_permissions.manage_roles,
        "roles": roles,
        "channels": channels,
    }


def build_state(bot: Pingwin) -> dict[str, Any]:
    cfg = bot.config
    coord = bot.coordinator
    lease = coord.last_lease
    now = discord.utils.utcnow()
    guild = _guild(bot)
    latency = bot.latency
    posted = []
    for m in menus.load_menus():
        ch = guild.get_channel(m.channel_id) if guild and guild.id == m.guild_id else None
        posted.append({"url": m.url, "channel": ch.name if ch else None})
    return {
        "version": __version__,
        "node": bot.node.name,
        "active": bot.is_active,
        "leader": coord.leader,
        "lease_node": lease.node if lease else None,
        "lease_age": round(lease.age(now)) if lease else None,
        "connected": bot.gateway_connected,
        "latency_ms": round(latency * 1000) if latency and math.isfinite(latency) else None,
        "started_at": bot.started_at.isoformat(),
        "settings_folder": str(data_dir()),
        "config_file": str(config_path()),
        "persona": cfg.persona,
        "update": getattr(bot, "update_status", None),
        "repo": REPO_URL,
        "invite": invite_url(bot.user.id) if bot.user else None,
        **_guild_info(guild),
        "config": {
            "roles": {
                "title": cfg.roles.menu_title,
                "description": cfg.roles.menu_description,
                "required_role": cfg.roles.required_role or "",
                "menu": [
                    {"name": m.name, "emoji": m.emoji or "", "description": m.description} for m in cfg.roles.menu
                ],
            },
            "failover": {
                "enabled": cfg.failover.enabled,
                "status_channel_id": str(cfg.failover.status_channel_id or ""),
                "pcs": list(cfg.failover.pcs),
                "heartbeat_seconds": cfg.failover.heartbeat_seconds,
                "takeover_after_seconds": cfg.failover.takeover_after_seconds,
                "this_pc_rank": cfg.failover.rank(bot.node.name),
                "this_pc_role": cfg.failover.describe(bot.node.name),
            },
        },
        "menus": posted,
    }


async def get_state(request: web.Request) -> web.Response:
    return web.json_response(build_state(request.app[BOT]))


async def get_setup(request: web.Request) -> web.Response:
    bot = request.app[BOT]
    facts = await gather_facts(bot, _guild(bot))
    return web.json_response(summarize(checklist(facts)))


async def get_logs(request: web.Request) -> web.Response:
    try:
        n = max(10, min(int(request.query.get("lines", "300")), 2000))
    except ValueError:
        n = 300
    lines: deque[str] = deque(maxlen=n)
    if LOG_FILE.exists():
        with LOG_FILE.open(encoding="utf-8", errors="replace") as f:
            lines.extend(line.rstrip("\n") for line in f)
    return web.json_response({"lines": list(lines), "file": str(LOG_FILE)})


# --------------------------------------------------------------------- saving
def _str(payload: dict, key: str, limit: int = 2000) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise ApiError(f"{key} must be text")
    return value.strip()[:limit]


async def post_roles(request: web.Request) -> web.Response:
    bot = request.app[BOT]
    payload = await request.json()
    raw_menu = payload.get("menu")
    if not isinstance(raw_menu, list):
        raise ApiError("menu must be a list")
    menu = []
    for row in raw_menu:
        if not isinstance(row, dict):
            raise ApiError("Each menu row must be an object")
        name = _str(row, "name", 100)
        if name:
            menu.append(
                MenuRole(name=name, emoji=_str(row, "emoji", 100) or None, description=_str(row, "description", 200))
            )
    save_roles(
        title=_str(payload, "title", 256) or "Pick your pings",
        description=_str(payload, "description", 2000),
        required_role=_str(payload, "required_role", 100) or None,
        menu=menu,
    )
    bot.reload_settings()
    log.info("Role menu settings saved from the dashboard")
    return web.json_response({"ok": True, "message": "Saved. Refresh posted menus to show the changes in Discord."})


async def post_failover(request: web.Request) -> web.Response:
    bot = request.app[BOT]
    payload = await request.json()
    channel = _str(payload, "status_channel_id", 30)
    if channel and not channel.isdigit():
        raise ApiError("Status channel must be a channel ID")
    pcs = payload.get("pcs", [])
    if not isinstance(pcs, list) or not all(isinstance(p, str) for p in pcs):
        raise ApiError("pcs must be a list of names")
    try:
        heartbeat = int(payload.get("heartbeat_seconds", 30))
        takeover = int(payload.get("takeover_after_seconds", 120))
    except (TypeError, ValueError) as e:
        raise ApiError("Check-in times must be whole numbers") from e
    save_failover(
        enabled=bool(payload.get("enabled")),
        status_channel_id=int(channel) if channel else None,
        pcs=[p.strip() for p in pcs if p.strip()],
        heartbeat_seconds=heartbeat,
        takeover_after_seconds=takeover,
    )
    bot.reload_settings()
    log.info("Failover settings saved from the dashboard")
    return web.json_response({"ok": True, "message": "Saved. Every PC picks this up within a minute."})


# --------------------------------------------------------------------- actions
async def post_action(request: web.Request) -> web.Response:
    from ..cogs.roles import MenuError, post_menu, refresh_menus, role_report

    bot = request.app[BOT]
    payload = await request.json()
    action = payload.get("action")
    guild = _guild(bot)

    def need_guild() -> discord.Guild:
        if guild is None:
            raise ApiError("Pingwin isn't connected to a Discord server yet.", 409)
        return guild

    try:
        if action == "reload":
            bot.reload_settings()
            return web.json_response({"message": "Settings reloaded."})
        if action == "restart":
            asyncio.get_running_loop().call_later(1, lambda: asyncio.ensure_future(bot.restart()))
            return web.json_response({"message": "Restarting… this page reconnects in about 30 seconds."})
        if action == "check_updates":
            available = await updater.update_available(PROJECT_ROOT)
            bot.update_status = {"available": available, "checked_at": discord.utils.utcnow().isoformat()}
            msg = "An update is ready. Restart to install it." if available else "Pingwin is up to date."
            return web.json_response({"message": msg, "available": available})
        if action in ("check_roles", "create_roles"):
            lines = await role_report(
                need_guild(), bot.config.roles, create_missing=action == "create_roles", actor="the dashboard"
            )
            return web.json_response({"lines": lines})
        if action == "post_menu":
            g = need_guild()
            channel_id = str(payload.get("channel_id", ""))
            channel = g.get_channel(int(channel_id)) if channel_id.isdigit() else None
            if not isinstance(channel, discord.TextChannel):
                raise ApiError("Pick a text channel first.")
            message = await post_menu(bot, channel, actor="the dashboard")
            return web.json_response({"message": f"Menu posted in #{channel.name}.", "url": message.jump_url})
        if action == "refresh_menus":
            return web.json_response({"lines": await refresh_menus(bot)})
    except MenuError as e:
        raise ApiError(str(e)) from e
    raise ApiError(f"Unknown action {action!r}")


# --------------------------------------------------------------------- app
def create_app(bot: Any, key: str) -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=256 * 1024)
    app[BOT] = bot
    app[KEY] = key
    app.router.add_get("/", index)
    app.router.add_get("/login", login)
    app.router.add_get("/static/{name}", static)
    app.router.add_get("/api/state", get_state)
    app.router.add_get("/api/logs", get_logs)
    app.router.add_get("/api/setup", get_setup)
    app.router.add_post("/api/roles", post_roles)
    app.router.add_post("/api/failover", post_failover)
    app.router.add_post("/api/action", post_action)
    return app


class Dashboard:
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.runner: web.AppRunner | None = None

    async def start(self) -> None:
        cfg = self.bot.config.dashboard
        if not cfg.enabled:
            return
        key = load_or_create_key()
        self.runner = web.AppRunner(create_app(self.bot, key), access_log=None)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, "127.0.0.1", cfg.port).start()
        except OSError as e:
            log.warning("Dashboard couldn't use port %d (%s). Change [dashboard] port in config.toml.", cfg.port, e)
            await self.runner.cleanup()
            self.runner = None
            return
        URL_FILE.write_text(f"http://127.0.0.1:{cfg.port}/login?key={key}\n", encoding="utf-8")
        log.info("Dashboard ready at http://127.0.0.1:%d (open it with scripts\\open_dashboard.cmd)", cfg.port)

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
