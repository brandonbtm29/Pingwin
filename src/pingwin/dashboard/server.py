"""Pingwin's dashboard: settings and controls in a browser.

Security model
* Listens on 127.0.0.1 only. Requests must be for localhost, or for this PC's Tailscale Funnel
  address when the dashboard has been put online (anything else is refused, which also blocks
  DNS-rebinding tricks from websites).
* On this PC: a secret key from the project folder (.dashboard-key). Opening
  scripts\\open_dashboard.cmd signs you in with it (HttpOnly, SameSite=Strict cookie). The key
  never works through Funnel.
* Through Funnel: Sign in with Discord, for members with Manage Server (or the dashboard admin
  role). See auth.py.
* Changes (POST) also need an `X-Pingwin: 1` header and a matching Origin, which other websites
  can't send, so they can't change settings behind your back.
"""

from __future__ import annotations

import asyncio
import csv
import hmac
import html
import json
import logging
import math
import os
import re
import secrets
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import aiohttp
import discord
from aiohttp import web

from .. import REPO_URL, __version__, invite_url, menus, store, updater
from .. import zones as _zones
from ..config import MENU_MODES, PROJECT_ROOT, ConfigError, MenuConfig, MenuRole, config_path, data_dir
from ..configedit import save_dashboard, save_failover, save_roles, save_secrets
from . import access, editing, tailscale
from .auth import (
    SECRET_NAME,
    SESSION_COOKIE,
    SESSION_SECONDS,
    STATE_COOKIE,
    Sessions,
    SignInError,
    Viewer,
    authorize_url,
    check_member,
    client_secret,
    exchange_code,
)
from .setup_check import checklist, gather_facts, summarize

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.dashboard")

STATIC = Path(__file__).with_name("static")
STATIC_FILES = {
    "app.js": "text/javascript",
    "movies.js": "text/javascript",
    "style.css": "text/css",
    "icon.svg": "image/svg+xml",
}
KEY_FILE = PROJECT_ROOT / ".dashboard-key"
URL_FILE = PROJECT_ROOT / ".dashboard-url"
COOKIE = "pingwin_dashboard"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
LOG_FILE = PROJECT_ROOT / "logs" / "pingwin.log"

BOT: web.AppKey[Any] = web.AppKey("bot", object)
KEY: web.AppKey[str] = web.AppKey("key", str)
REMOTE: web.AppKey[Any] = web.AppKey("remote", object)
SESSIONS: web.AppKey[Any] = web.AppKey("sessions", object)
PRESENCE: web.AppKey[Any] = web.AppKey("presence", object)
OPEN_PATHS = {"/login", "/auth/login", "/auth/callback", "/movies/login"}
VIEW_COOKIE = "pingwin_view"  # the shared view-only movie link
VIEW_PATHS = {"/movies", "/api/movies"}  # all a view-only visitor can open
MEMBER_PATHS = {
    "/movies",
    "/api/movies",
    "/api/movies/suggest",
    "/api/movies/search",
    "/api/movies/review",
    "/api/signout",
    "/api/presence",
}
DASHBOARDS = "dashboards"  # shared: each PC's public dashboard link, for /pingwin dashboard
PUBLIC: web.RequestKey[bool] = web.RequestKey("public", bool)
VIEWER: web.RequestKey[Any] = web.RequestKey("viewer", object)

LOCKED_PAGE = """<!doctype html><meta charset="utf-8"><title>Pingwin</title>
<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem;line-height:1.5">
<h1>🐧 Pingwin</h1><p>To open the dashboard, double-click <code>scripts\\open_dashboard.cmd</code>
in the Pingwin folder. It signs you in on this PC.</p></body>"""

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; "
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


def _pc(bot: Any) -> str:
    node = getattr(bot, "node", None)
    return node.name if node else "this PC"


class Remote:
    """What Tailscale says about this PC, refreshed now and then (the guard needs it on every request)."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.status = tailscale.Status()
        self.checked = 0.0

    @property
    def host(self) -> str | None:
        """The public hostname requests may use, when the dashboard is online."""
        return self.status.dns_name.lower() if self.status.funnel_on and self.status.dns_name else None

    @property
    def url(self) -> str | None:
        return self.status.public_url

    async def refresh(self) -> tailscale.Status:
        self.status = await asyncio.to_thread(tailscale.status, self.port)
        self.checked = time.time()
        return self.status


def is_public(request: web.Request) -> bool:
    """Did this request come through Tailscale Funnel (or any proxy) rather than from this PC?"""
    return (
        "Tailscale-Funnel-Request" in request.headers
        or "X-Forwarded-For" in request.headers
        or _hostname(request.host) not in LOCAL_HOSTS
    )


async def identify(request: web.Request) -> Viewer | None:
    app = request.app
    bot = app[BOT]
    if not request[PUBLIC] and hmac.compare_digest(request.cookies.get(COOKIE, ""), app[KEY]):
        return Viewer("owner", f"{_pc(bot)} (this PC)", sid=f"pc-{_pc(bot)}", full_access=True)
    token = request.cookies.get(SESSION_COOKIE)
    item = app[SESSIONS].get(token)
    if item is None:
        return _view_only(request)
    if time.time() - item["checked"] > 300:
        level, reason, member = await check_member(bot, int(item["user_id"]))
        if level is None:
            log.info("Signed %s out of the dashboard: %s", item["name"], reason)
            app[SESSIONS].end(token)
            return None
        item["checked"] = time.time()
        item["level"] = level
        if member is not None:
            item["name"] = member.display_name
    app[SESSIONS].touch(item)
    cfg = getattr(getattr(bot, "config", None), "dashboard", None)
    return Viewer(
        "discord",
        item["name"],
        user_id=item["user_id"],
        avatar=item.get("avatar"),
        sid=item["sid"],
        level=item.get("level", "admin"),
        full_access=bool(cfg and cfg.remote_full_access) and item.get("level", "admin") == "admin",
    )


def _view_only(request: web.Request) -> Viewer | None:
    """Someone with the shared view-only movie link (in the address or remembered in a cookie)."""
    from .. import movieaccess

    if request.path not in VIEW_PATHS | MEMBER_PATHS:
        return None  # (the guard then limits them to VIEW_PATHS, with a friendly message)
    token = request.query.get("view") or request.cookies.get(VIEW_COOKIE)
    if not movieaccess.is_share_token(token):
        return None
    return Viewer("guest", "Guest", sid=f"view-{_hash_short(token or '')}", level="viewer")


def _hash_short(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:8]


# --------------------------------------------------------------------- guard
@web.middleware
async def guard(request: web.Request, handler):
    public = is_public(request)
    request[PUBLIC] = public
    remote = request.app[REMOTE]
    host = _hostname(request.host)
    if public:
        if not remote.host or host != remote.host:
            return web.Response(status=403, text="Pingwin's dashboard isn't available here.")
    elif host not in LOCAL_HOSTS:
        return web.Response(status=403, text="Pingwin's dashboard only answers on this PC.")

    if not (request.path in OPEN_PATHS or request.path.startswith("/static/")):
        viewer = await identify(request)
        if viewer is None:
            if request.path.startswith("/api/"):
                msg = (
                    "Not signed in. Reload the page." if public else "Not signed in. Open scripts\\open_dashboard.cmd."
                )
                return web.json_response({"error": msg}, status=401)
            if public:
                return _secure(request, signin_page(request, next_path=request.path))
            return web.Response(text=LOCKED_PAGE, content_type="text/html", status=401)
        request[VIEWER] = viewer
        if viewer.level == "viewer":
            if request.method not in ("GET", "HEAD") or request.path not in VIEW_PATHS:
                return _secure(
                    request,
                    web.json_response(
                        {
                            "error": "You're viewing with the shared link. Sign in to do that "
                            "(or use /pingwin movie page)."
                        },
                        status=403,
                    ),
                )
            if request.path == "/movies" and "view" in request.query:
                # Remember the link in a cookie and take the secret out of the address bar.
                response = web.HTTPFound("/movies")
                response.set_cookie(
                    VIEW_COOKIE,
                    request.query["view"],
                    httponly=True,
                    secure=bool(request[PUBLIC]),
                    samesite="Lax",
                    path="/",
                    max_age=180 * 86400,
                )
                return _secure(request, response)
        elif not viewer.is_admin and request.path not in MEMBER_PATHS:
            if request.path.startswith("/api/"):
                return _secure(request, web.json_response({"error": "That's for admins."}, status=403))
            raise web.HTTPFound("/movies")
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("Origin")
            allowed = {remote.host} if public else LOCAL_HOSTS
            if request.headers.get("X-Pingwin") != "1" or (
                (origin or public) and _hostname(urlparse(origin or "").netloc) not in allowed
            ):
                return web.json_response({"error": "Blocked: request didn't come from the dashboard."}, status=403)

    try:
        response = await handler(request)
    except ApiError as e:
        response = web.json_response({"error": str(e)}, status=e.status)
    except editing.StaleEdit as e:
        response = web.json_response({"error": str(e), "stale": True}, status=409)
    except ConfigError as e:
        response = web.json_response({"error": str(e)}, status=400)
    return _secure(request, response)


def _secure(request: web.Request, response: web.StreamResponse) -> web.StreamResponse:
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    if request.get(PUBLIC):
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def viewer_of(request: web.Request) -> Viewer:
    return request.get(VIEWER) or Viewer("owner", "this PC", sid="pc", full_access=True)


def need_full_access(request: web.Request) -> Viewer:
    viewer = viewer_of(request)
    if not viewer.full_access:
        raise ApiError(
            "Only the PC's owner can do that (or turn on full access for signed-in admins under Remote access).", 403
        )
    return viewer


# --------------------------------------------------------------------- pages
async def login(request: web.Request) -> web.StreamResponse:
    if request[PUBLIC] or not hmac.compare_digest(request.query.get("key", ""), request.app[KEY]):
        return web.Response(text=LOCKED_PAGE, content_type="text/html", status=401)
    response = web.HTTPFound("/")
    response.set_cookie(COOKIE, request.app[KEY], httponly=True, samesite="Strict", path="/", max_age=365 * 86400)
    return response


def _page(body: str, status: int = 200) -> web.Response:
    doc = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pingwin</title>'
        '<link rel="icon" href="/static/icon.svg"><link rel="stylesheet" href="/static/style.css"></head>'
        '<body><main class="signin"><img src="/static/icon.svg" alt="" width="72" height="72">'
        f"{body}</main></body></html>"
    )
    return web.Response(text=doc, content_type="text/html", status=status)


def signin_page(request: web.Request, error: str = "", status: int = 401, next_path: str = "/") -> web.Response:
    bot = request.app[BOT]
    guild = getattr(bot, "home_guild", None)
    server = html.escape(guild.name) if guild else "your server"
    err = f'<p class="signin-error">{html.escape(error)}</p>' if error else ""
    ready = bool(client_secret())
    movies_page = next_path.startswith("/movies")
    target = "/auth/login" + ("?next=/movies" if movies_page else "")
    button = (
        f'<a class="btn primary big" href="{target}">Sign in with Discord</a>'
        if ready
        else "<p class=\"hint\">Discord sign-in isn't set up yet. The PC's owner can finish it on the Status tab.</p>"
    )
    gate = getattr(getattr(getattr(bot, "config", None), "roles", None), "required_role", None)
    if movies_page:
        who = f"For members of {server}" + (f" with the {html.escape(gate)} role" if gate else "") + "."
        intro = "See our movie nights, ratings and watchlists."
    else:
        who = f"The dashboard is for members with Manage Server in {server}; everyone else gets the movie pages."
        intro = f"Manage Pingwin for <strong>{server}</strong>."
    return _page(
        f"<h1>Pingwin</h1><p>{intro}</p>{err}{button}"
        f'<p class="hint">{who} Pingwin only sees your Discord name and picture, and can\'t read your messages '
        "or servers.</p>",
        status,
    )


async def auth_login(request: web.Request) -> web.StreamResponse:
    bot = request.app[BOT]
    remote = request.app[REMOTE]
    if not request[PUBLIC] or not remote.url:
        raise web.HTTPFound("/")
    if not client_secret() or not getattr(bot, "application_id", None):
        return _secure(request, signin_page(request, "Discord sign-in isn't set up yet."))
    state = secrets.token_urlsafe(24)
    nxt = "/movies" if request.query.get("next") == "/movies" else "/"
    response = web.HTTPFound(authorize_url(str(bot.application_id), f"{remote.url}/auth/callback", state))
    response.set_cookie(
        STATE_COOKIE, f"{state}|{nxt}", httponly=True, secure=True, samesite="Lax", path="/auth", max_age=600
    )
    return response


async def auth_callback(request: web.Request) -> web.StreamResponse:
    bot = request.app[BOT]
    remote = request.app[REMOTE]
    if not request[PUBLIC] or not remote.url:
        raise web.HTTPFound("/")
    q = request.query
    if q.get("error"):
        return signin_page(request, "Sign-in was cancelled." if q["error"] == "access_denied" else "Discord said no.")
    expected, _, nxt = request.cookies.get(STATE_COOKIE, "").partition("|")
    nxt = "/movies" if nxt == "/movies" else "/"
    if not expected or not hmac.compare_digest(expected, q.get("state", "")) or not q.get("code"):
        return signin_page(request, "That sign-in link expired. Try again.")
    try:
        user = await exchange_code(str(bot.application_id), q["code"], f"{remote.url}/auth/callback")
    except SignInError as e:
        return signin_page(request, str(e))
    except Exception:  # network trouble etc.
        log.exception("Discord sign-in failed")
        return signin_page(request, "Couldn't reach Discord. Try again in a minute.")
    level, reason, member = await check_member(bot, int(user["id"]))
    if level is None:
        log.warning("Dashboard sign-in refused for %s (%s): %s", user["name"], user["id"], reason)
        return signin_page(request, reason, 403, next_path=nxt)
    if member is not None:
        user["name"] = member.display_name
    user["level"] = level
    token = request.app[SESSIONS].create(user)
    log.info("%s (%s) signed in from outside this PC (%s)", user["name"], user["id"], level)
    if level != "admin":
        nxt = "/movies"
    # A same-site hop so the browser treats the new cookie as first-party.
    response = _page(f'<p>Signed in. Opening…</p><meta http-equiv="refresh" content="0;url={nxt}">')
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=True, samesite="Lax", path="/", max_age=SESSION_SECONDS
    )
    response.del_cookie(STATE_COOKIE, path="/auth")
    return response


async def movies_login(request: web.Request) -> web.StreamResponse:
    """A personal sign-in link from /pingwin movie page: signs one member in, no Discord screen.

    Opening the link only shows a button; pressing it (a POST) uses the code. Discord, chat apps and
    link scanners open links to make previews, and used to spend the single-use code first."""
    from .. import movieaccess

    bot = request.app[BOT]
    expired = "That sign-in link was already used or has expired. Run /pingwin movie page again."
    if request.method == "GET":
        code = request.query.get("code", "")[:64]
        peek = movieaccess.peek_login_code(code)
        if peek is None:
            return signin_page(request, expired, 403)
        return _page(
            f"<h1>Hi, {html.escape(str(peek.get('name') or 'there'))}!</h1>"
            "<p>Press the button to sign in to the movie pages, so you can write reviews and suggest movies.</p>"
            '<form method="post" action="/movies/login">'
            f'<input type="hidden" name="code" value="{html.escape(code, quote=True)}">'
            '<button class="btn primary big" type="submit">Open the movie pages</button></form>'
            '<p class="hint">This link is just for you and works once.</p>'
        )
    origin = request.headers.get("Origin")
    if origin and _hostname(origin.split("://", 1)[-1]) != _hostname(request.host):
        return signin_page(request, "That sign-in didn't come from this page.", 403)
    form = await request.post()
    found = movieaccess.take_login_code(str(form.get("code", ""))[:64])
    if found is None:
        return signin_page(
            request, "That sign-in link was already used or has expired. Run /pingwin movie page again.", 403
        )
    level, reason, member = await check_member(bot, int(found["id"]))
    if level is None:
        return signin_page(request, reason, 403, next_path="/movies")
    user = {
        "id": found["id"],
        "name": member.display_name if member else found["name"],
        "avatar": found.get("avatar"),
        "level": level,
    }
    token = request.app[SESSIONS].create(user)
    log.info("%s signed in to the movie pages with a personal link (%s)", user["name"], level)
    response = web.HTTPSeeOther("/movies")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=bool(request[PUBLIC]),
        samesite="Lax",
        path="/",
        max_age=SESSION_SECONDS,
    )
    return response


async def signout(request: web.Request) -> web.Response:
    viewer = viewer_of(request)
    request.app[SESSIONS].end(request.cookies.get(SESSION_COOKIE))
    request.app[PRESENCE].leave(viewer.sid)
    response = web.json_response({"ok": True, "public": request[PUBLIC]})
    response.del_cookie(SESSION_COOKIE, path="/")
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
        menu = cfg.roles.get_menu(m.menu)
        posted.append(
            {
                "url": m.url,
                "message_id": str(m.message_id),
                "channel": ch.name if ch else None,
                "menu": m.menu,
                "menu_title": menu.title if menu else None,
            }
        )
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
                "required_role": cfg.roles.required_role or "",
                "menus": [
                    {
                        "key": menu.key,
                        "title": menu.title,
                        "description": menu.description,
                        "mode": menu.mode,
                        "timezone": menu.is_timezone,
                        "roles": [
                            {"name": m.name, "emoji": m.emoji or "", "description": m.description, "zone": m.zone or ""}
                            for m in menu.roles
                        ],
                    }
                    for menu in cfg.roles.menus
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
                # Other PCs' latest backup check-ins (seconds ago), as seen by the answering PC.
                "checkins": {
                    name: round((now - at).total_seconds()) for name, at in getattr(coord, "checkins", {}).items()
                },
                "quiet": list(getattr(coord, "quiet", [])),
            },
        },
        "menus": posted,
        "zones": [{"label": label, "zone": zone} for label, zone in _zones.COMMON],
        "tz_preset": [{"name": n, "emoji": e, "zone": z} for n, e, z in _zones.MENU_PRESET],
    }


async def get_state(request: web.Request) -> web.Response:
    state = build_state(request.app[BOT])
    state["viewer"] = viewer_of(request).as_json()
    state["public"] = request[PUBLIC]
    state["versions"] = editing.versions()
    state["last_edits"] = editing.last_edits()
    return web.json_response(state)


async def get_features(request: web.Request) -> web.Response:
    from ..features import SECTIONS

    bot = request.app[BOT]
    f = bot.config.features
    return web.json_response(
        {
            "sections": SECTIONS,
            "values": f.as_json(),
            "intents": {"members": f.needs_members, "message_content": f.needs_message_content},
            "restart_needed": getattr(bot, "restart_needed", False),
            "missing_intents": getattr(bot, "missing_intents", []),
            "app_id": str(getattr(bot, "application_id", "") or ""),
            "versions": editing.versions(),
        }
    )


async def post_features(request: web.Request) -> web.Response:
    from ..configedit import save_section

    bot = request.app[BOT]
    payload = await request.json()
    key, values = payload.get("section"), payload.get("values")
    if not isinstance(key, str) or not isinstance(values, dict):
        raise ApiError("section and values are required")
    viewer = viewer_of(request)
    editing.check(key, payload.get("base_version"), editing.versions().get(key, ""))
    save_section(key, values)
    editing.record(key, viewer.name, _pc(bot))
    bot.reload_settings()
    log.info("Saved %s settings from the dashboard (%s)", key, viewer.name)
    restart = getattr(bot, "restart_needed", False)
    msg = "Saved." + (" Restart Pingwin to apply the Discord intent change." if restart else "")
    return web.json_response({"ok": True, "message": msg, "restart_needed": restart})


async def get_watchlist(request: web.Request) -> web.Response:
    from .. import store
    from ..features import slug

    bot = request.app[BOT]
    state = store.load("movienight")
    series = []
    for s in bot.config.features.movienight.series:
        if not s.name:
            continue
        data = state.get(slug(s.name), {})
        series.append(
            {
                "name": s.name,
                "key": slug(s.name),
                "nominations": data.get("nominations", []),
                "open_poll": any(p.get("status") == "open" for p in data.get("polls", [])),
            }
        )
    return web.json_response({"series": series})


async def post_watchlist(request: web.Request) -> web.Response:
    from .. import store

    payload = await request.json()
    key, nom_id, action = payload.get("series"), payload.get("id"), payload.get("action")
    state = store.load("movienight")
    noms = state.get(key, {}).get("nominations", [])
    n = next((x for x in noms if x.get("id") == nom_id), None)
    if n is None:
        raise ApiError("That movie isn't on the list anymore.", 404)
    if action == "remove":
        noms.remove(n)
    elif action == "watched":
        n["watched"] = None if n.get("watched") else discord.utils.utcnow().isoformat()
    else:
        raise ApiError("Unknown action")
    store.save("movienight", state)
    return web.json_response({"ok": True})


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


# --------------------------------------------------------------------- working together
async def post_presence(request: web.Request) -> web.Response:
    payload = await request.json()
    viewer = viewer_of(request)
    presence = request.app[PRESENCE]
    if payload.get("leaving"):
        presence.leave(viewer.sid)
        return web.json_response({"ok": True})
    presence.ping(viewer, str(payload.get("tab", "")), bool(payload.get("dirty")))
    return web.json_response(
        {
            "others": presence.others(viewer.sid),
            "conflicts": len(await asyncio.to_thread(editing.find_conflicts)),
            "versions": editing.versions(),
        }
    )


async def get_conflicts(request: web.Request) -> web.Response:
    return web.json_response({"conflicts": await asyncio.to_thread(editing.find_conflicts)})


async def post_conflicts(request: web.Request) -> web.Response:
    payload = await request.json()
    viewer = viewer_of(request)
    msg = editing.resolve_conflict(str(payload.get("file", "")), str(payload.get("action", "")))
    log.info("Sync conflict %s: %s (%s)", payload.get("file"), msg, viewer.name)
    request.app[BOT].reload_settings()
    return web.json_response({"ok": True, "message": msg})


# --------------------------------------------------------------------- remote access
async def get_remote(request: web.Request) -> web.Response:
    bot = request.app[BOT]
    remote = request.app[REMOTE]
    viewer = viewer_of(request)
    if request.query.get("refresh") or time.time() - remote.checked > 30:
        await remote.refresh()
    cfg = bot.config.dashboard
    redirect = (
        f"{remote.url}/auth/callback"
        if remote.url
        else (
            f"https://{remote.status.dns_name}:{tailscale.FUNNEL_PORT}/auth/callback"
            if remote.status.dns_name
            else None
        )
    )
    return web.json_response(
        {
            "tailscale": remote.status.as_json(),
            "secret_set": bool(client_secret()),
            "redirect_uri": redirect,
            "app_id": str(getattr(bot, "application_id", "") or ""),
            "admin_role": cfg.admin_role or "",
            "remote_full_access": cfg.remote_full_access,
            "can_manage": viewer.full_access,
            "sessions": request.app[SESSIONS].listing() if viewer.full_access else [],
            "dashboards": store.load(DASHBOARDS),
        }
    )


async def post_remote(request: web.Request) -> web.Response:
    bot = request.app[BOT]
    remote = request.app[REMOTE]
    viewer = need_full_access(request)
    payload = await request.json()
    action = payload.get("action")
    if action == "funnel_on":
        res = await asyncio.to_thread(tailscale.publish, remote.port)
        await remote.refresh()
        publish_link(bot, remote)
        if remote.url:
            log.info("Dashboard put online at %s (%s)", remote.url, viewer.name)
            return web.json_response({"ok": True, "message": f"The dashboard is online at {remote.url}"})
        if res.approve_url:
            return web.json_response(
                {
                    "ok": False,
                    "approve_url": res.approve_url,
                    "message": "Tailscale needs you to allow Funnel first. Open the link, approve, "
                    "then press the button again.",
                }
            )
        raise ApiError(res.output or "Tailscale couldn't put the dashboard online.")
    if action == "funnel_off":
        res = await asyncio.to_thread(tailscale.unpublish)
        await remote.refresh()
        publish_link(bot, remote)
        log.info("Dashboard taken offline (%s)", viewer.name)
        return web.json_response({"ok": res.ok, "message": "The dashboard is only on this PC again."})
    if action == "save":
        role = _str(payload, "admin_role", 100) or None
        base = payload.get("base_version")
        editing.check("dashboard", base, editing.versions().get("dashboard", ""))
        save_dashboard(admin_role=role, remote_full_access=bool(payload.get("remote_full_access")))
        secret = payload.get("client_secret")
        if isinstance(secret, str) and secret.strip():
            save_secrets({SECRET_NAME: secret.strip()})
        editing.record("dashboard", viewer.name, _pc(bot))
        bot.reload_settings()
        log.info("Remote access settings saved (%s)", viewer.name)
        return web.json_response({"ok": True, "message": "Saved."})
    if action == "revoke":
        n = (
            request.app[SESSIONS].revoke(sid=str(payload.get("sid") or ""))
            if payload.get("sid")
            else (request.app[SESSIONS].revoke())
        )
        log.info("Signed out %d dashboard session(s) (%s)", n, viewer.name)
        return web.json_response({"ok": True, "message": f"Signed out {n} session(s)."})
    raise ApiError(f"Unknown action {action!r}")


def publish_link(bot: Any, remote: Remote) -> None:
    """Keep the shared list of dashboard links (for /pingwin dashboard) up to date for this PC."""
    try:
        links = store.load(DASHBOARDS)
        pc = _pc(bot)
        current = links.get(pc, {}).get("url")
        if remote.url and current != remote.url:
            links[pc] = {"url": remote.url, "at": time.time()}
        elif not remote.url and pc in links:
            links.pop(pc)
        else:
            return
        store.save(DASHBOARDS, links)
    except OSError as e:
        log.warning("Couldn't update the shared dashboard links: %s", e)


# --------------------------------------------------------------------- birthdays & members
_names: dict[int, tuple[str, str | None]] = {}


async def _member_name(guild: discord.Guild | None, user_id: int) -> tuple[str, str | None]:
    if user_id in _names:
        return _names[user_id]
    member = guild.get_member(user_id) if guild else None
    if member is None and guild is not None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            member = None
    found = (
        (member.display_name, member.display_avatar.with_size(64).url)
        if member
        else (f"Left the server ({user_id})", None)
    )
    _names[user_id] = found
    return found


async def get_birthdays(request: web.Request) -> web.Response:
    bot = request.app[BOT]
    guild = _guild(bot)
    people = store.load("birthdays").get("people", {})
    rows = []
    for uid, p in people.items():
        name, avatar = await _member_name(guild, int(uid))
        rows.append({"user_id": uid, "name": name, "avatar": avatar, "month": p["m"], "day": p["d"]})
    rows.sort(key=lambda r: (r["month"], r["day"], r["name"].casefold()))
    return web.json_response({"people": rows, "version": editing.state_version("birthdays")})


async def post_birthdays(request: web.Request) -> web.Response:
    from ..cogs.birthdays import valid_date

    bot = request.app[BOT]
    viewer = viewer_of(request)
    payload = await request.json()
    uid = str(payload.get("user_id", ""))
    if not uid.isdigit():
        raise ApiError("Pick someone first.")
    state = store.load("birthdays")
    people = state.setdefault("people", {})
    if payload.get("action") == "remove":
        people.pop(uid, None)
        verb = "Removed"
    else:
        try:
            month, day = int(payload.get("month")), int(payload.get("day"))
        except (TypeError, ValueError) as e:
            raise ApiError("Pick a month and day.") from e
        if not valid_date(month, day):
            raise ApiError("That date doesn't exist.")
        people[uid] = {"m": month, "d": day}
        verb = "Saved"
    store.save("birthdays", state)
    _names.pop(int(uid), None)
    name, _ = await _member_name(_guild(bot), int(uid))
    editing.record("birthday-list", viewer.name, _pc(bot))
    log.info("%s %s's birthday from the dashboard (%s)", verb, name, viewer.name)
    return web.json_response({"ok": True, "message": f"{verb} {name}'s birthday."})


async def get_members(request: web.Request) -> web.Response:
    """Find members by name for pickers (works without the Server Members intent)."""
    guild = _guild(request.app[BOT])
    q = request.query.get("q", "").strip()
    if guild is None or len(q) < 1:
        return web.json_response({"members": []})
    found: dict[int, discord.Member] = {}
    key = q.casefold()
    for m in guild.members:  # whatever's cached (everyone, with the Server Members intent)
        if not m.bot and (key in m.display_name.casefold() or key in m.name.casefold()):
            found[m.id] = m
        if len(found) >= 10:
            break
    if len(found) < 10:
        try:
            for m in await guild.query_members(q, limit=10):
                if not m.bot:
                    found.setdefault(m.id, m)
        except (TimeoutError, discord.HTTPException, discord.ClientException):
            pass
    return web.json_response(
        {
            "members": [
                {
                    "id": str(m.id),
                    "name": m.display_name,
                    "username": m.name,
                    "avatar": m.display_avatar.with_size(64).url,
                }
                for m in list(found.values())[:10]
            ]
        }
    )


async def get_attendance(request: web.Request) -> web.Response:
    import time as _time

    from ..cogs.attendance import STATE, seconds_present

    bot = request.app[BOT]
    guild = _guild(bot)
    sessions = store.load(STATE).get("sessions", {})
    cutoff = _time.time() - 45 * 86400
    rows = []
    for key, s in sorted(sessions.items(), key=lambda kv: -kv[1]["started_at"]):
        if s["started_at"] < cutoff:
            continue
        now = s.get("ended_at") or _time.time()
        ids = (
            s.get("attendees")
            if s.get("finalized")
            else sorted(set(s["seconds"]) | set(s["open"]) | set(s["checked_in"]) | set(s["added"]))
        )
        people = []
        for uid in ids or []:
            name, avatar = await _member_name(guild, int(uid))
            award = s.get("awards", {}).get(uid, {})
            people.append(
                {
                    "user_id": uid,
                    "name": name,
                    "avatar": avatar,
                    "minutes": round(seconds_present(s, uid, now) / 60),
                    "checked_in": uid in s["checked_in"],
                    "added": uid in s["added"],
                    "removed": uid in s["removed"],
                    "award": award.get("amount"),
                    "status": award.get("status"),
                    "error": award.get("error"),
                }
            )
        rows.append(
            {
                "key": key,
                "name": s["name"],
                "started_at": s["started_at"],
                "ended_at": s.get("ended_at"),
                "finalized": s.get("finalized", False),
                "kind": s.get("kind"),
                "code": s.get("code"),
                "people": people,
                "failed": sum(1 for a in s.get("awards", {}).values() if a.get("status") == "failed"),
            }
        )
    return web.json_response({"events": rows[:20]})


async def post_attendance(request: web.Request) -> web.Response:
    bot = request.app[BOT]
    viewer = viewer_of(request)
    payload = await request.json()
    action = payload.get("action")
    if action not in ("add", "remove", "pay"):
        raise ApiError("Unknown action")
    cog = bot.get_cog("Attendance") if hasattr(bot, "get_cog") else None
    if cog is None:
        raise ApiError("Attendance isn't loaded. Restart Pingwin.", 409)
    uid = str(payload.get("user_id", ""))
    if action != "pay" and not uid.isdigit():
        raise ApiError("Pick someone first.")
    msg = await cog.change(str(payload.get("key", "")), uid, action)
    log.info("Attendance %s %s for %s (%s)", action, uid or "", payload.get("key"), viewer.name)
    return web.json_response({"ok": True, "message": msg})


# --------------------------------------------------------------------- movie pages
async def movies_page(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(STATIC / "movies.html", headers={"Content-Type": "text/html; charset=utf-8"})


def _movie_cog(bot: Any):
    return bot.get_cog("MovieNight") if hasattr(bot, "get_cog") else None


def _series_names(bot: Any) -> list[str]:
    cog = _movie_cog(bot)
    if cog is not None and hasattr(cog, "all_series"):
        return [s.name for s in cog.all_series]
    from ..cogs.movienight import GENERAL

    mine = [s.name for s in bot.config.features.movienight.series if s.name] if hasattr(bot, "config") else []
    return [*mine, GENERAL.name]


async def get_movies(request: web.Request) -> web.Response:
    from .. import movies
    from ..cogs.movienight import STATE as WATCH

    bot = request.app[BOT]
    viewer = viewer_of(request)
    guild = _guild(bot) if hasattr(bot, "guilds") else None
    lib = movies.load()
    members = {}
    if guild is not None:
        members = {str(m.id): m.display_name for m in guild.members if not m.bot}
    names = movies.rater_names(lib, members)
    links = movies.rater_links(lib)
    history = []
    for e in reversed(lib["history"]):
        m = lib["movies"].get(e["movie_id"], {"title": "?"})
        ratings = sorted(e.get("ratings", {}).items(), key=lambda kv: kv[1].get("at", 0))
        chain = movies.question_chain(e, names)
        history.append(
            {
                "id": e["id"],
                "date": e.get("date"),
                "series": e.get("series"),
                "movie": m,
                "avg": movies.ratings_avg(e),
                "ratings": [
                    {
                        **{f: r.get(f) for f in ("name", "score", "comment", "answered", "answer", "ask")},
                        "key": k,
                        "who": names.get(k) or r.get("name"),
                        "who_id": links.get(k),
                        "discord": bool(r.get("user_id")),
                        "asked_by": r.get("asked_by") or None,
                        "ask_to": chain[k]["to"],
                        "answered_from": chain[k]["from"],
                    }
                    for k, r in ratings
                ],
                "open": bool((e.get("prompt") or {}).get("message")) and not (e.get("prompt") or {}).get("closed"),
            }
        )
    state = store.load(WATCH)
    cog = _movie_cog(bot)
    if cog is not None:
        all_series = list(cog.all_series)
    else:
        from ..cogs.movienight import GENERAL

        mine = [s for s in bot.config.features.movienight.series if s.name] if hasattr(bot, "config") else []
        all_series = [*mine, GENERAL]
    series = [s.name for s in all_series]
    series_full = {s.name: (getattr(s, "full_name", "") or s.name) for s in all_series}
    watchlists = []
    from ..features import slug

    by_cache: dict[Any, str] = {}
    for name in series:
        items = []
        for n in state.get(slug(name), {}).get("nominations", []):
            if n["by"] not in by_cache:
                by_cache[n["by"]] = (await _member_name(guild, int(n["by"])))[0] if guild else str(n["by"])
            by_name = by_cache[n["by"]]
            m = lib["movies"].get(n.get("movie_id") or "", {})
            items.append(
                {
                    **{k: n.get(k) for k in ("id", "title", "year", "added", "watched", "list", "note")},
                    "by": by_name,
                    "runtime": m.get("runtime"),
                    "imdb_rating": m.get("imdb_rating"),
                    "poster": m.get("poster"),
                    "imdb_id": m.get("imdb_id"),
                    "movie_id": n.get("movie_id"),
                }
            )
        watchlists.append({"series": name, "items": items, "queue": _queue_json(state.get(slug(name), {}), lib)})
    from .. import imdblists

    noms = {name: state.get(slug(name), {}).get("nominations", []) for name in series}
    imdb = [
        {
            "id": lid,
            **{k: lst.get(k) for k in ("name", "url", "imported")},
            **imdblists.compare(lib, lst, noms),
        }
        for lid, lst in sorted(lib.get("imdb_lists", {}).items(), key=lambda kv: kv[1].get("name", "").casefold())
    ]
    pending = [
        {k: r.get(k) for k in ("id", "kind", "series", "data", "by_name", "at")}
        for r in lib["pending"].values()
        if r.get("status") == "pending" and (viewer.is_admin or str(r.get("by")) == str(viewer.user_id))
    ]
    return web.json_response(
        {
            "viewer": viewer.as_json(),
            "series": series,
            "series_full": series_full,
            "franchise_series": [s.name for s in all_series if getattr(s, "franchises", False)],
            "history": history,
            "watchlists": watchlists,
            "imdb_lists": imdb,
            "unmatched": movies.unmatched(lib, state) if viewer.is_admin else [],
            "backups": store.backups(movies.STATE) if viewer.is_admin else [],
            "search_budget": cog.search_budget() if cog is not None and hasattr(cog, "search_budget") else None,
            "pending": pending,
            "people": lib.get("people", {}),
            "members": sorted(
                (
                    {"id": str(m.id), "name": m.display_name, "username": m.name, "global": m.global_name or ""}
                    for m in (guild.members if guild is not None and viewer.is_admin else [])
                    if not m.bot
                ),
                key=lambda x: x["name"].casefold(),
            ),
            "omdb": bool(os.environ.get(movies.SECRET)),
            "details": {
                **movies.details_progress(lib, state),
                "auto": bool(getattr(getattr(bot, "config", None), "features", None))
                and bool(getattr(bot.config.features.movienight, "auto_details", True)),
                "used_today": lib.get("omdb", {}).get("used", 0)
                if lib.get("omdb", {}).get("day") == time.strftime("%Y-%m-%d", time.gmtime())
                else 0,
                "daily": getattr(getattr(getattr(bot, "config", None), "features", None), "movienight", None)
                and getattr(bot.config.features.movienight, "omdb_daily", 800),
                "paused": lib.get("omdb", {}).get("status")
                if lib.get("omdb", {}).get("pause_until", 0) > time.time()
                else None,
            },
            "approvals": bool(
                getattr(getattr(bot, "config", None), "features", None) and bot.config.features.movienight.approvals
            ),
        }
    )


async def _who(request: web.Request) -> Any:
    """The Discord member behind this request (for changes that may need approval)."""
    viewer = viewer_of(request)
    guild = _guild(request.app[BOT])
    if viewer.user_id and guild is not None:
        member = guild.get_member(int(viewer.user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(viewer.user_id))
            except discord.HTTPException:
                member = None
        if member is not None:
            return member
    return None


async def get_movie_export(request: web.Request) -> web.Response:
    """Admins: download the reviews as a spreadsheet, or the whole movie library (everything Pingwin keeps)."""
    from .. import movies

    bot = request.app[BOT]
    lib = movies.load()
    day = time.strftime("%Y-%m-%d")
    if request.query.get("what") == "library":
        body = json.dumps(lib, indent=2, ensure_ascii=False, default=str)
        name, ctype = f"pingwin-movie-library-{day}.json", "application/json"
    else:
        guild = _guild(bot) if hasattr(bot, "guilds") else None
        members = {str(m.id): m.display_name for m in guild.members if not m.bot} if guild is not None else {}
        body = "\ufeff" + movies.reviews_csv(lib, movies.rater_names(lib, members))  # BOM: Excel reads UTF-8
        name, ctype = f"pingwin-movie-reviews-{day}.csv", "text/csv"
    return web.Response(
        text=body,
        content_type=ctype,
        charset="utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


async def get_movie_search(request: web.Request) -> web.Response:
    """Title suggestions for the Suggest a movie box: our library, then OMDb's catalog."""
    cog = _movie_cog(request.app[BOT])
    q = request.query.get("q", "")[:80]
    y = request.query.get("y", "")
    if cog is None or len(q.strip()) < 2:
        return web.json_response({"library": [], "omdb": [], "note": ""})
    return web.json_response(await cog.search(q, int(y) if y.isdigit() and len(y) == 4 else None))


async def post_movie_suggest(request: web.Request) -> web.Response:
    """Members: suggest a movie or a history fix (goes to the mods). Admins: applied straight away."""
    bot = request.app[BOT]
    viewer = viewer_of(request)
    cog = _movie_cog(bot)
    if cog is None:
        raise ApiError("Movie night isn't loaded. Restart Pingwin.", 409)
    payload = await request.json()
    kind = payload.get("kind")
    if kind == "nominate":
        s = cog.series(_str(payload, "series", 50))
        if s is None:
            raise ApiError("Pick a series.")
        title = _str(payload, "title", 100)
        if not title:
            raise ApiError("Type a title.")
        year_raw = _str(payload, "year", 4)
        year = int(year_raw) if year_raw.isdigit() else None
        imdb_id = _str(payload, "imdb_id", 12) or None
        who = await _who(request)
        if who is None:  # this PC's owner
            m = await cog.details(title, year, imdb_id)
            data = {
                "title": m["title"] if m else title,
                "year": (m or {}).get("year") or year,
                "movie_id": (m or {}).get("id"),
            }
            return web.json_response({"ok": True, "message": await cog.apply("nominate", s.name, data, 0)})
        return web.json_response({"ok": True, "message": await cog.nominate_text(who, s, title, year, imdb_id)})
    if kind == "edit":
        from .. import movies

        hid = _str(payload, "hid", 20)
        lib = movies.load()
        e = next((x for x in lib["history"] if x["id"] == hid), None)
        if e is None:
            raise ApiError("That movie night isn't in the history anymore.", 404)
        changes = {}
        for k in ("date", "title", "series"):
            if k in payload and isinstance(payload[k], str):
                changes[k] = payload[k].strip()[:100]
        if "year" in payload:
            y = str(payload["year"]).strip()
            changes["year"] = int(y) if y.isdigit() else None
        if "poster" in payload:
            poster = str(payload["poster"] or "").strip()
            if poster and not re.fullmatch(r"https://\S{8,500}", poster):
                raise ApiError("A poster has to be an https:// image link.")
            changes["poster"] = poster
        if changes.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", changes["date"]):
            raise ApiError("Dates look like 2026-10-23.")
        if not changes:
            raise ApiError("Nothing to change.")
        m = lib["movies"].get(e["movie_id"], {})
        data = {"hid": hid, "changes": changes, "label": f"{m.get('title', '?')} ({e.get('date') or 'no date'})"}
        if viewer.is_admin:
            log.info("History fix from the movie pages (%s): %s", viewer.name, data)
            return web.json_response({"ok": True, "message": cog._apply_edit(data)})
        who = await _who(request)
        if who is None:
            raise ApiError("Couldn't find you in the server.", 403)
        return web.json_response({"ok": True, "message": await cog.submit("edit", e.get("series") or "", data, who)})
    raise ApiError("Unknown suggestion.")


async def _queue_action(cog: Any, payload: dict[str, Any], viewer: Any) -> dict[str, Any]:
    """Mods manage a series' franchise queue from the movie pages."""
    from datetime import datetime as _dt

    from .. import franchise
    from ..cogs.movienight import STATE as WATCH
    from ..timeparse import next_weekly

    if cog is None:
        raise ApiError("Movie night isn't loaded. Restart Pingwin.", 409)
    s = cog.series(_str(payload, "series", 50))
    if s is None:
        raise ApiError("Pick a series.")
    op = payload.get("op")
    if op == "suggest":  # a starting list from OMDb (by title search, oldest first)
        name = _str(payload, "name", 100)
        if not name:
            raise ApiError("Type the franchise's name first.")
        if not os.environ.get(movies_secret()):
            raise ApiError("Add an OMDb API key under Features → Movie night to get suggestions.")
        found = await cog.suggest_franchise(name)
        if not found:
            raise ApiError(f"OMDb didn't find movies for {name!r}. Type them in instead.")
        return {"ok": True, "text": "\n".join(f"{t} ({y})" if y else t for t, y in found)}
    state = store.load(WATCH)
    data = cog._data(state, s)
    q = data.get("queue")
    msg = "Saved."
    if op == "set":
        items = franchise.parse_items(str(payload.get("text") or ""))
        name = _str(payload, "name", 100)
        if not name:
            raise ApiError("Type the franchise's name.")
        if not items:
            raise ApiError("List at least one movie, one per line.")
        old = {i["title"].casefold(): i for i in (q or {}).get("items", [])}
        for i in items:  # keep what's already watched when a list is edited
            before = old.get(i["title"].casefold())
            if before and before.get("done") and not i.get("done"):
                i["done"] = before["done"]
        keep = {k: q[k] for k in ("announced_for", "logged_for", "postponed_for", "paused") if q and k in q}
        data["queue"] = {**franchise.new_queue(name, items), **keep}
        msg = f"Saved {name} ({len(items)} movies)."
    elif q is None:
        raise ApiError("There's no franchise queue for that series yet.")
    elif op == "pause":
        q["paused"] = bool(payload.get("paused"))
        msg = "Paused: no announcements until you resume." if q["paused"] else "Resumed."
    elif op == "postpone":
        show = next_weekly(s.showtime, _dt.now(cog.bot.features.tz)) if getattr(s, "showtime", "") else None
        if show is None:
            raise ApiError("This series has no showtime.")
        day = show.date().isoformat()
        q["postponed_for"] = None if q.get("postponed_for") == day else day
        msg = (
            "Skipping this week's showing; the same part is next time."
            if q["postponed_for"]
            else "Back on for this week."
        )
    elif op in ("skip", "done", "up", "down"):
        item = next((i for i in q["items"] if i["id"] == payload.get("item")), None)
        if item is None:
            raise ApiError("That movie isn't in the queue anymore.")
        if op == "skip":
            item["skipped"] = not item.get("skipped")
        elif op == "done":
            item["done"] = None if item.get("done") else "earlier"
        else:
            franchise.move(q, item["id"], -1 if op == "up" else 1)
    elif op == "end":
        data.pop("queue", None)
        msg = "Franchise queue cleared."
    else:
        raise ApiError("Unknown queue change.")
    store.save(WATCH, state)
    log.info("Franchise queue %s for %s (%s)", op, s.name, viewer.name)
    return {"ok": True, "message": msg}


def movies_secret() -> str:
    from .. import movies

    return movies.SECRET


async def post_movie_review(request: web.Request) -> web.Response:
    """Add or change a review from the movie pages. Members review as themselves; admins can also
    enter one for a member (for someone who told them their score), or for a guest without Discord
    (saved under their name, like imported ratings, so People can group it)."""
    from .. import movies

    bot = request.app[BOT]
    viewer = viewer_of(request)
    payload = await request.json()
    guild = _guild(bot)
    uid = str(viewer.user_id) if viewer.user_id else ""
    as_user = str(payload.get("as_user") or "").strip()
    guest = re.sub(r"\s+", " ", str(payload.get("guest") or "")).strip()[:40]
    if guest and not viewer.is_admin:
        raise ApiError("Only mods can enter a review for someone else.", 403)
    if guest and (len(guest) < 2 or guest.startswith("@")):
        raise ApiError("Type the guest's name.")
    if as_user and as_user != uid and not guest:
        if not viewer.is_admin:
            raise ApiError("You can only review as yourself.", 403)
        if not as_user.isdigit():
            raise ApiError("Pick who the review is from.")
        uid = as_user
    if guest:
        key, user_id, name = f"name:{guest.casefold()}", None, guest
    else:
        if not uid:
            raise ApiError("Pick who the review is from (or sign in with Discord to review as yourself).")
        member = guild.get_member(int(uid)) if guild is not None else None
        name = member.display_name if member else (viewer.name if uid == str(viewer.user_id) else "")
        if not name:
            raise ApiError("That person isn't in the server.", 404)
        key, user_id = uid, uid
    score = movies.parse_score(str(payload.get("score", "")))
    if score is None:
        raise ApiError("The score needs to be a number from 0 to 10.")
    lib = movies.load()
    e = next((x for x in lib["history"] if x["id"] == payload.get("hid")), None)
    if e is None:
        raise ApiError("That movie night isn't in the history anymore.", 404)
    old = e.get("ratings", {}).get(key)
    question = old.get("answered", "") if old else movies.open_question(e)
    asker = old.get("answered_from") if old else movies.open_asker(e, skip=key)
    answer = _str(payload, "answer", 500)
    movies.add_rating(
        e,
        key,
        name=name,
        user_id=user_id,
        score=score,
        comment=_str(payload, "comment", 1500),
        answered=question if answer else "",
        answered_from=asker,
        answer=answer,
        ask=_str(payload, "ask", 300),
    )
    if old:
        e["ratings"][key]["at"] = old.get("at", time.time())  # keep its place in the question chain
    movies.save(lib)
    log.info("Movie review %s on the movie pages (%s for %s)", "changed" if old else "added", viewer.name, name)
    cog = _movie_cog(bot)
    if cog is not None and (e.get("prompt") or {}).get("message"):
        try:
            await cog.refresh_prompt(e, lib)
        except discord.HTTPException:
            pass
    return web.json_response({"ok": True, "message": "Review updated." if old else "Thanks! Review added. ⭐"})


def _queue_json(data: dict[str, Any], lib: dict[str, Any]) -> dict[str, Any] | None:
    """A series' franchise queue for the movie pages, with posters and runtimes where we have them."""
    from .. import franchise

    q = data.get("queue")
    if not q:
        return None
    nxt = franchise.next_part(q)
    items = []
    for i in q.get("items", []):
        m = movies_find(lib, i["title"], i.get("year"))
        part = franchise.part_number(q, i) if not i.get("skipped") else (None, None)
        items.append(
            {
                **{k: i.get(k) for k in ("id", "title", "year", "done", "skipped")},
                "part": part[0],
                "next": i is nxt,
                "poster": (m or {}).get("poster"),
                "runtime": (m or {}).get("runtime"),
                "imdb_rating": (m or {}).get("imdb_rating"),
            }
        )
    return {
        "name": q.get("name"),
        "paused": bool(q.get("paused")),
        "postponed_for": q.get("postponed_for"),
        "total": len(franchise.counted(q)),
        "done": sum(1 for i in franchise.counted(q) if i.get("done")),
        "finished": franchise.finished(q),
        "items": items,
    }


def movies_find(lib: dict[str, Any], title: str, year: Any) -> dict[str, Any] | None:
    from .. import movies

    return movies.find_movie(lib, title, year)


def _share_info(cog: Any) -> dict[str, Any]:
    from .. import movieaccess

    url = cog.view_url() if cog is not None else None
    post = movieaccess.posted()
    return {"view_url": url, "posted_channel": str(post[0]) if post else None}


def _export_url(url: str, kind: str) -> str:
    m = re.search(r"/(spreadsheets|document)/d/([A-Za-z0-9_-]+)", url)
    if not m:
        raise ApiError(f"That doesn't look like a Google {'Sheets' if kind == 'sheet' else 'Docs'} link.")
    if kind == "sheet":
        gid = re.search(r"[#&?]gid=(\d+)", url)
        return f"https://docs.google.com/spreadsheets/d/{m[2]}/export?format=csv" + (f"&gid={gid[1]}" if gid else "")
    return f"https://docs.google.com/document/d/{m[2]}/export?format=txt"


async def _fetch_text(url: str) -> str:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
        async with http.get(url) as r:
            text = await r.text(encoding="utf-8", errors="replace")
            if r.status != 200 or "text/html" in r.headers.get("Content-Type", ""):
                raise ApiError(
                    "Google wouldn't share that file with Pingwin. Set it to 'Anyone with the link can view' "
                    "(Share → General access), or download it (File → Download) and upload it here instead."
                )
            return text


async def post_movie_admin(request: web.Request) -> web.Response:
    from .. import movies

    bot = request.app[BOT]
    viewer = viewer_of(request)
    if not viewer.is_admin:
        raise ApiError("That's for admins.", 403)
    payload = await request.json()
    action = payload.get("action")
    cog = _movie_cog(bot)
    if action == "import":
        sheet_text = payload.get("sheet_csv") or ""
        doc_text = payload.get("doc_text") or ""
        if not sheet_text and payload.get("sheet_url"):
            sheet_text = await _fetch_text(_export_url(str(payload["sheet_url"]), "sheet"))
        if not doc_text and payload.get("doc_url"):
            doc_text = await _fetch_text(_export_url(str(payload["doc_url"]), "doc"))
        if not sheet_text and not doc_text:
            raise ApiError("Give a spreadsheet and/or a ratings document.")
        sheet = movies.parse_sheet(sheet_text) if sheet_text else []
        lib = movies.load()
        titles = {movies.norm(r["title"]) for r in sheet} | {movies.norm(m["title"]) for m in lib["movies"].values()}
        doc = movies.parse_ratings_doc(doc_text, titles) if doc_text else []
        guild = _guild(bot)
        members = {
            str(m.id): f"{m.display_name} {m.name} {m.global_name or ''}" for m in (guild.members if guild else [])
        }
        mine = [s for s in bot.config.features.movienight.series if s.name]
        stats = movies.import_history(
            lib, sheet, doc, [s.name for s in mine], members, {s.name: getattr(s, "full_name", "") for s in mine}
        )
        movies.save(lib)
        log.info("Movie history imported (%s): %s", viewer.name, stats)
        return web.json_response(
            {
                "ok": True,
                "message": f"Imported {stats['entries']} movie nights, {stats['movies']} new movies and "
                f"{stats['ratings']} ratings ({stats['skipped']} already there or not matched).",
                "stats": stats,
            }
        )
    if action == "lookup":
        if cog is None or not os.environ.get(movies.SECRET):
            raise ApiError("Add an OMDb API key under Features → Movie night first.")
        res = await cog.fill_details(int(payload.get("requests", 120)))
        return web.json_response({"ok": True, "message": res["message"]})
    if action == "add_night":
        if cog is None:
            raise ApiError("Movie night isn't loaded. Restart Pingwin.", 409)
        title = _str(payload, "title", 100)
        if not title:
            raise ApiError("Type the movie's title.")
        day = _str(payload, "date", 10)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ApiError("Pick the date you watched it.")
        s = cog.series(_str(payload, "series", 50))
        if s is None:
            raise ApiError("Pick the event.")
        y = _str(payload, "year", 4)
        e, created = await cog.add_night(
            s,
            title,
            int(y) if y.isdigit() else None,
            movies.imdb_id_in(_str(payload, "imdb_id", 20)),
            day,
            rate=bool(payload.get("rate")),
        )
        m = movies.load()["movies"].get(e["movie_id"], {"title": title})
        label = f"{m['title']} ({m['year']})" if m.get("year") else m["title"]
        log.info("Movie night added from the movie pages (%s): %s on %s", viewer.name, label, day)
        msg = f"Added {label} on {day}." if created else f"{label} on {day} was already in the history."
        return web.json_response({"ok": True, "message": msg, "hid": e["id"]})
    if action in ("imdb_find", "imdb_match", "imdb_retry"):
        if cog is None or not os.environ.get(movies.SECRET):
            raise ApiError("Add an OMDb API key under Features → Movie night first.")
        from ..cogs.movienight import STATE as WATCH

        if action == "imdb_retry":
            lib, watch = movies.load(), store.load(WATCH)
            n = movies.retry_unmatched(lib, watch)
            movies.save(lib)
            store.save(WATCH, watch)
            res = await cog.fill_details(min(200, 4 * n)) if n else {"found": 0}
            msg = f"Looked {n} movies up again: found {res['found']}." if n else "Nothing to retry."
            return web.json_response({"ok": True, "message": msg})
        try:
            if action == "imdb_find":
                q = _str(payload, "q", 80)
                pasted = movies.imdb_id_in(q)
                if pasted:  # a pasted IMDb link: just that movie
                    info = await cog._ask_omdb("", None, pasted)
                    hits = [{k: info.get(k) for k in ("title", "year", "imdb_id", "poster")}] if info else []
                else:
                    y = _str(payload, "year", 4)
                    hits = await cog.find_matches(q, int(y) if y.isdigit() else None)
                return web.json_response({"ok": True, "results": hits, "budget": cog.search_budget()})
            msg = await cog.match_imdb(
                _str(payload, "kind", 10),
                _str(payload, "id", 80),
                movies.imdb_id_in(_str(payload, "imdb", 200)) or "",
                _str(payload, "series_key", 60),
            )
        except movies.OmdbLimit:
            raise ApiError("OMDb's daily limit is used up. Try again tomorrow.", 429) from None
        except (ValueError, aiohttp.ClientError, TimeoutError) as e:
            raise ApiError(str(e) or "OMDb didn't answer. Try again.") from None
        log.info("IMDb match set by hand (%s): %s", viewer.name, msg)
        return web.json_response({"ok": True, "message": msg})
    if action in ("delete_entry", "delete_rating"):
        lib = movies.load()
        e = next((x for x in lib["history"] if x["id"] == payload.get("hid")), None)
        if e is None:
            raise ApiError("That movie night isn't in the history anymore.", 404)
        if action == "delete_entry":
            lib["history"].remove(e)
            msg = "Removed from the history."
        else:
            gone = [
                k
                for k, r in e.get("ratings", {}).items()
                if (payload.get("key") and k == payload.get("key"))
                or (not payload.get("key") and r.get("name") == payload.get("name"))
            ]
            for k in gone:
                e["ratings"].pop(k)
            msg = f"Removed {len(gone)} rating(s)."
        movies.save(lib)
        log.info("Movie history %s (%s)", action, viewer.name)
        return web.json_response({"ok": True, "message": msg})
    if action == "queue":
        return web.json_response(await _queue_action(cog, payload, viewer))
    if action in ("share_info", "share_reset", "share_post"):
        from .. import movieaccess

        if action == "share_reset":
            movieaccess.reset_share()
            log.info("Movie pages view link reset (%s)", viewer.name)
        if action == "share_post":
            if cog is None:
                raise ApiError("Movie night isn't loaded. Restart Pingwin.", 409)
            cid = str(payload.get("channel_id") or "")
            if not cid.isdigit():
                raise ApiError("Pick a channel.")
            msg = await cog.post_share_link(int(cid))
            return web.json_response({"ok": True, "message": msg, **_share_info(cog)})
        if action == "share_reset" and cog is not None:
            await cog.refresh_share_post()
        return web.json_response(
            {"ok": True, "message": "New view link made." if action == "share_reset" else "", **_share_info(cog)}
        )
    if action == "import_list":
        if cog is None:
            raise ApiError("Movie night isn't loaded. Restart Pingwin.", 409)
        text, by = str(payload.get("text") or ""), 0
        link = str(payload.get("link") or "").strip()
        if link:
            m = re.search(r"channels/(\d+)/(\d+)/(\d+)", link)
            if not m:
                raise ApiError(
                    "That doesn't look like a Discord message link (right-click the message → Copy Message Link)."
                )
            try:
                channel = bot.get_channel(int(m[2])) or await bot.fetch_channel(int(m[2]))
                msg = await channel.fetch_message(int(m[3]))
            except (discord.HTTPException, AttributeError) as e:
                raise ApiError(f"Pingwin couldn't read that message ({e}). Can it see that channel?") from e
            text, by = msg.content, msg.author.id
        rows = movies.parse_list_message(text)
        if not rows:
            raise ApiError("No movies found in that message.")
        try:
            st = cog.import_list(_str(payload, "series", 50), rows, by, _str(payload, "label", 40))
        except ValueError as e:
            raise ApiError(str(e)) from e
        log.info("Watchlist imported from a list (%s): %s", viewer.name, st)
        return web.json_response(
            {
                "ok": True,
                "message": f"Added {st['added']} movies. Skipped {st['watched']} already watched and "
                f"{st['already']} already on the list.",
            }
        )
    if action == "imdb_list":
        from .. import imdblists

        lib = movies.load()
        if payload.get("op") == "delete":
            gone = lib.get("imdb_lists", {}).pop(_str(payload, "id", 60), None)
            movies.save(lib)
            return web.json_response({"ok": True, "message": f"Removed {gone['name']}." if gone else "Already gone."})
        text = payload.get("csv")
        if not isinstance(text, str) or not text.strip():
            raise ApiError("Pick the CSV file IMDb made (on the list: ⋯ → Export).")
        try:
            items = imdblists.parse_csv(text)
        except (ValueError, csv.Error) as e:
            raise ApiError(str(e)) from None
        if not items:
            raise ApiError("No movies found in that file.")
        if payload.get("op") == "preview":  # nothing saved yet: the admin reviews the rows first
            from ..cogs.movienight import STATE as WATCH
            from ..features import slug

            state = store.load(WATCH)
            noms = {name: state.get(slug(name), {}).get("nominations", []) for name in _series_names(bot)}
            return web.json_response({"ok": True, "rows": imdblists.preview(lib, items, noms)})
        skip = payload.get("skip") or []
        if isinstance(skip, list):
            drop = {int(i) for i in skip if isinstance(i, int) or str(i).isdigit()}
            items = [it for i, it in enumerate(items) if i not in drop]
        if not items:
            raise ApiError("Every movie was left out, so there's nothing to save.")
        res = imdblists.save_list(lib, _str(payload, "name", 60), _str(payload, "url", 220), items)
        movies.save(lib)
        log.info("IMDb list %s imported (%s): %s movies", res["name"], viewer.name, res["items"])
        msg = f"Saved {res['name']}: {res['items']} movies."
        if res["filled"]:
            msg += f" Filled in details for {res['filled']} movies in our library."
        return web.json_response({"ok": True, "message": msg})
    if action == "people":
        mapping = payload.get("people")
        if not isinstance(mapping, dict) or len(mapping) > 500:
            raise ApiError("Nothing to save.")
        lib = movies.load()
        changed = movies.set_people(lib, mapping)
        movies.save(lib)
        log.info("Movie raters renamed or merged (%s): %s change(s)", viewer.name, changed)
        return web.json_response({"ok": True, "message": f"Saved {changed} change(s)." if changed else "No changes."})
    if action == "decide":
        if cog is None:
            raise ApiError("Movie night isn't loaded.", 409)

        class _Mod:
            display_name = name = viewer.name

        return web.json_response(
            {"ok": True, "message": await cog.decide(str(payload.get("id")), bool(payload.get("approve")), _Mod())}
        )
    raise ApiError("Unknown action")


async def get_access(request: web.Request) -> web.Response:
    return web.json_response(access.report(request.app[BOT]))


# --------------------------------------------------------------------- saving
def _str(payload: dict, key: str, limit: int = 2000) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise ApiError(f"{key} must be text")
    return value.strip()[:limit]


def _menu_roles(raw: Any, where: str) -> list[MenuRole]:
    if not isinstance(raw, list):
        raise ApiError(f"{where} must be a list")
    out = []
    for row in raw:
        if not isinstance(row, dict):
            raise ApiError("Each menu row must be an object")
        name = _str(row, "name", 100)
        if name:
            out.append(
                MenuRole(
                    name=name,
                    emoji=_str(row, "emoji", 100) or None,
                    description=_str(row, "description", 200),
                    zone=_str(row, "zone", 64) or None,
                )
            )
    return out


async def post_roles(request: web.Request) -> web.Response:
    bot = request.app[BOT]
    payload = await request.json()
    viewer = viewer_of(request)
    editing.check("roles", payload.get("base_version"), editing.versions().get("roles", ""))
    required = _str(payload, "required_role", 100) or None
    if "menus" in payload:  # every menu
        raw = payload["menus"]
        if not isinstance(raw, list) or not raw or not isinstance(raw[0], dict):
            raise ApiError("menus must be a list, starting with the ping menu")
        main = raw[0]
        extra = []
        for m in raw[1:]:
            if not isinstance(m, dict):
                raise ApiError("Each menu must be an object")
            mode = _str(m, "mode", 10) or "many"
            if mode not in MENU_MODES:
                raise ApiError("mode must be 'many' or 'one'")
            title = _str(m, "title", 256) or "Menu"
            extra.append(
                MenuConfig(
                    key=_str(m, "key", 40) or title,
                    title=title,
                    description=_str(m, "description", 2000),
                    mode=mode,
                    roles=tuple(_menu_roles(m.get("roles", []), "roles")),
                )
            )
        save_roles(
            title=_str(main, "title", 256) or "Pick your pings",
            description=_str(main, "description", 2000),
            required_role=required,
            menu=_menu_roles(main.get("roles", []), "roles"),
            extra=extra,
        )
    else:  # older pages: just the ping menu
        save_roles(
            title=_str(payload, "title", 256) or "Pick your pings",
            description=_str(payload, "description", 2000),
            required_role=required,
            menu=_menu_roles(payload.get("menu", []), "menu"),
        )
    editing.record("roles", viewer.name, _pc(bot))
    bot.reload_settings()
    log.info("Role menu settings saved from the dashboard (%s)", viewer.name)
    return web.json_response({"ok": True, "message": "Saved. Update posted menus to show the changes in Discord."})


async def post_failover(request: web.Request) -> web.Response:
    bot = request.app[BOT]
    payload = await request.json()
    viewer = viewer_of(request)
    editing.check("failover", payload.get("base_version"), editing.versions().get("failover", ""))
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
    editing.record("failover", viewer.name, _pc(bot))
    bot.reload_settings()
    log.info("Failover settings saved from the dashboard (%s)", viewer.name)
    return web.json_response({"ok": True, "message": "Saved. Every PC picks this up within a minute."})


# --------------------------------------------------------------------- actions
async def post_action(request: web.Request) -> web.Response:
    from ..cogs.roles import MenuError, post_menu, refresh_menus, remove_menu, role_report
    from ..pairing import PairingError

    bot = request.app[BOT]
    payload = await request.json()
    action = payload.get("action")
    viewer = viewer_of(request)
    if str(action).startswith("pair_"):
        need_full_access(request)
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
            log.info("Restart requested from the dashboard (%s)", viewer.name)
            asyncio.get_running_loop().call_later(1, lambda: asyncio.ensure_future(bot.restart()))
            return web.json_response({"message": "Restarting… this page reconnects in about 30 seconds."})
        if action == "stop":
            need_full_access(request)
            log.info("Stop requested from the dashboard (%s)", viewer.name)
            asyncio.get_running_loop().call_later(1, lambda: asyncio.ensure_future(bot.stop()))
            return web.json_response(
                {"message": "Stopping Pingwin on this PC. Start it again with the Pingwin Dashboard shortcut."}
            )
        if action == "check_updates":
            available = await updater.update_available(PROJECT_ROOT)
            bot.update_status = {"available": available, "checked_at": discord.utils.utcnow().isoformat()}
            msg = "An update is ready. Restart to install it." if available else "Pingwin is up to date."
            return web.json_response({"message": msg, "available": available})
        if action in ("check_roles", "create_roles"):
            lines = await role_report(
                need_guild(),
                bot.config.roles,
                create_missing=action == "create_roles",
                actor=f"{viewer.name} (dashboard)",
            )
            return web.json_response({"lines": lines})
        if action == "post_menu":
            g = need_guild()
            channel_id = str(payload.get("channel_id", ""))
            channel = g.get_channel(int(channel_id)) if channel_id.isdigit() else None
            if not isinstance(channel, discord.TextChannel):
                raise ApiError("Pick a text channel first.")
            message = await post_menu(
                bot, channel, actor=f"{viewer.name} on the dashboard", menu_key=str(payload.get("menu") or "main")
            )
            return web.json_response({"message": f"Menu posted in #{channel.name}.", "url": message.jump_url})
        if action == "refresh_menus":
            return web.json_response({"lines": await refresh_menus(bot)})
        if action == "create_channel":
            g = need_guild()
            name = re.sub(r"[^a-z0-9-]+", "-", _str(payload, "name", 40).casefold()).strip("-") or "mod-log"
            if not g.me.guild_permissions.manage_channels:
                raise ApiError("Pingwin needs the Manage Channels permission to make channels.")
            existing = discord.utils.get(g.text_channels, name=name)
            if existing is not None:
                return web.json_response(
                    {"message": f"#{name} already exists, so I picked it.", "id": str(existing.id)}
                )
            # Private: hidden from everyone except admins/mods (Manage Server) and Pingwin.
            overwrites: dict[Any, discord.PermissionOverwrite] = {
                g.default_role: discord.PermissionOverwrite(view_channel=False),
                g.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, embed_links=True, read_message_history=True
                ),
            }
            for role in g.roles:
                if not role.managed and (role.permissions.manage_guild or role.permissions.administrator):
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
            status = g.get_channel(bot.config.failover.status_channel_id or 0)
            category = getattr(status, "category", None)
            channel = await g.create_text_channel(
                name,
                overwrites=overwrites,
                category=category,
                reason=f"Pingwin: made by {viewer.name} on the dashboard",
            )
            log.info("Created #%s for %s (%s)", name, _str(payload, "purpose", 40) or "the dashboard", viewer.name)
            return web.json_response({"message": f"Made a private #{name}.", "id": str(channel.id)})
        if action == "remove_menu":
            mid = str(payload.get("message_id", ""))
            if not mid.isdigit():
                raise ApiError("Which menu?")
            return web.json_response({"message": await remove_menu(bot, int(mid))})
        if action == "pair_invite":
            from ..pairing import Syncthing, create_invite

            def make() -> tuple[str, list[str]]:
                return create_invite(Syncthing.from_config(), data_dir())

            code, notes = await asyncio.to_thread(make)
            log.info("Created a backup PC invite code")
            return web.json_response({"code": code, "lines": notes})
        if action == "pair_shared":
            from ..pairing import Syncthing, shared_devices

            def listing() -> list[dict[str, str]]:
                return shared_devices(Syncthing.from_config(), data_dir())

            return web.json_response({"devices": await asyncio.to_thread(listing)})
        if action == "pair_forget":
            from ..pairing import Syncthing, forget

            device_id = str(payload.get("device_id", ""))

            def run_forget() -> list[str]:
                return forget(Syncthing.from_config(), data_dir(), device_id)

            notes = await asyncio.to_thread(run_forget)
            bot.reload_settings()
            log.info("Stopped sharing settings with a backup PC")
            return web.json_response({"lines": notes})
        if action == "pair_accept":
            from ..pairing import Syncthing, accept

            code = str(payload.get("code", ""))

            def run() -> list[str]:
                return accept(Syncthing.from_config(), code, data_dir())

            notes = await asyncio.to_thread(run)
            bot.reload_settings()
            log.info("Accepted a backup PC reply code")
            return web.json_response({"lines": notes})
    except MenuError as e:
        raise ApiError(str(e)) from e
    except PairingError as e:
        raise ApiError(str(e)) from e
    raise ApiError(f"Unknown action {action!r}")


# --------------------------------------------------------------------- app
def create_app(
    bot: Any, key: str, *, remote: Remote | None = None, sessions: Sessions | None = None
) -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=2 * 1024 * 1024)  # (IMDb list exports)
    app[BOT] = bot
    app[KEY] = key
    port = getattr(getattr(getattr(bot, "config", None), "dashboard", None), "port", 8787)
    app[REMOTE] = remote or Remote(port)
    app[SESSIONS] = sessions or Sessions()
    app[PRESENCE] = editing.Presence(_pc(bot))
    app.router.add_get("/", index)
    app.router.add_get("/movies", movies_page)
    app.router.add_get("/movies/login", movies_login)
    app.router.add_post("/movies/login", movies_login)
    app.router.add_get("/api/movies", get_movies)
    app.router.add_post("/api/movies/suggest", post_movie_suggest)
    app.router.add_get("/api/movies/search", get_movie_search)
    app.router.add_get("/api/movies/export", get_movie_export)
    app.router.add_post("/api/movies/review", post_movie_review)
    app.router.add_post("/api/movies/admin", post_movie_admin)
    app.router.add_get("/login", login)
    app.router.add_get("/auth/login", auth_login)
    app.router.add_get("/auth/callback", auth_callback)
    app.router.add_get("/static/{name}", static)
    app.router.add_get("/api/state", get_state)
    app.router.add_get("/api/logs", get_logs)
    app.router.add_get("/api/setup", get_setup)
    app.router.add_get("/api/access", get_access)
    app.router.add_get("/api/features", get_features)
    app.router.add_post("/api/features", post_features)
    app.router.add_get("/api/watchlist", get_watchlist)
    app.router.add_post("/api/watchlist", post_watchlist)
    app.router.add_get("/api/birthdays", get_birthdays)
    app.router.add_post("/api/birthdays", post_birthdays)
    app.router.add_get("/api/members", get_members)
    app.router.add_get("/api/attendance", get_attendance)
    app.router.add_post("/api/attendance", post_attendance)
    app.router.add_post("/api/roles", post_roles)
    app.router.add_post("/api/failover", post_failover)
    app.router.add_post("/api/action", post_action)
    app.router.add_post("/api/presence", post_presence)
    app.router.add_get("/api/conflicts", get_conflicts)
    app.router.add_post("/api/conflicts", post_conflicts)
    app.router.add_get("/api/remote", get_remote)
    app.router.add_post("/api/remote", post_remote)
    app.router.add_post("/api/signout", signout)
    return app


class Dashboard:
    def __init__(self, bot: Pingwin) -> None:
        self.bot = bot
        self.runner: web.AppRunner | None = None
        self.remote: Remote | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        cfg = self.bot.config.dashboard
        if not cfg.enabled:
            return
        key = load_or_create_key()
        self.remote = Remote(cfg.port)
        try:
            await self.remote.refresh()
        except Exception as e:  # Tailscale trouble must never stop the dashboard
            log.warning("Couldn't ask Tailscale about remote access: %s", e)
        self.runner = web.AppRunner(create_app(self.bot, key, remote=self.remote), access_log=None)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, "127.0.0.1", cfg.port).start()
        except OSError as e:
            log.warning("Dashboard couldn't use port %d (%s). Change [dashboard] port in config.toml.", cfg.port, e)
            await self.runner.cleanup()
            self.runner = None
            return
        URL_FILE.write_text(f"http://127.0.0.1:{cfg.port}/login?key={key}\n", encoding="utf-8")
        log.info("Dashboard ready at http://127.0.0.1:%d (open it with the Pingwin Dashboard shortcut)", cfg.port)
        if self.remote.url:
            log.info("Dashboard is also online at %s (Sign in with Discord)", self.remote.url)
        publish_link(self.bot, self.remote)
        self._task = asyncio.get_running_loop().create_task(self._watch_remote())

    async def _watch_remote(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.remote.refresh()
                publish_link(self.bot, self.remote)
            except Exception as e:
                log.debug("Tailscale check failed: %s", e)

    @property
    def public_url(self) -> str | None:
        return self.remote.url if self.remote else None

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
