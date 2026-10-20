"""Who's using the dashboard.

Two ways in:

* **This PC's owner:** scripts\\open_dashboard.cmd opens /login?key=... with the secret key from the
  project folder. Only works on the PC itself (127.0.0.1), never through Funnel.
* **Co-admins, from anywhere:** when the dashboard is on the internet (Tailscale Funnel), people sign
  in with Discord. Pingwin only lets them in if they're in the server and have Manage Server (or
  the dashboard admin role). That's checked again every few minutes, so taking someone's role away
  also takes away their dashboard access.

Sessions are kept per PC in .dashboard-sessions.json (only a hash of each session token is stored).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import aiohttp
import discord

from ..config import PROJECT_ROOT

if TYPE_CHECKING:
    from ..bot import Pingwin

log = logging.getLogger("pingwin.dashboard.auth")

SESSIONS_FILE = PROJECT_ROOT / ".dashboard-sessions.json"
SESSION_COOKIE = "pingwin_session"
STATE_COOKIE = "pingwin_oauth"
SESSION_SECONDS = 14 * 86400
RECHECK_SECONDS = 300
DISCORD_API = "https://discord.com/api/v10"
SECRET_NAME = "DISCORD_CLIENT_SECRET"


@dataclass
class Viewer:
    kind: str  # "owner" (signed in on this PC with the key) or "discord"
    name: str
    user_id: str | None = None
    avatar: str | None = None
    sid: str = ""  # a short, stable id for this sign-in (for "who else is here")
    full_access: bool = False  # may manage backup PCs and remote access

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "user_id": self.user_id,
            "avatar": self.avatar,
            "sid": self.sid,
            "full_access": self.full_access,
        }


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Sessions:
    def __init__(self, path: Path = SESSIONS_FILE) -> None:
        self.path = path
        self.items: dict[str, dict[str, Any]] = {}
        self._saved_at = 0.0
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            now = time.time()
            self.items = {k: v for k, v in raw.items() if v.get("expires", 0) > now}
        except (OSError, ValueError, AttributeError):
            self.items = {}

    def _save(self, force: bool = True) -> None:
        if not force and time.time() - self._saved_at < 300:
            return
        tmp = self.path.with_name(self.path.name + ".pingwin-tmp")
        try:
            tmp.write_text(json.dumps(self.items, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            self._saved_at = time.time()
        except OSError as e:
            log.warning("Couldn't save dashboard sign-ins: %s", e)

    def create(self, user: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.items[_hash(token)] = {
            "sid": secrets.token_hex(4),
            "user_id": str(user["id"]),
            "name": user.get("name") or "Someone",
            "avatar": user.get("avatar"),
            "created": now,
            "expires": now + SESSION_SECONDS,
            "checked": now,
            "last_seen": now,
        }
        self._save()
        return token

    def get(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        item = self.items.get(_hash(token))
        if item is None:
            return None
        if item["expires"] < time.time():
            self.items.pop(_hash(token), None)
            self._save()
            return None
        return item

    def touch(self, item: dict[str, Any]) -> None:
        item["last_seen"] = time.time()
        self._save(force=False)

    def end(self, token: str | None) -> None:
        if token and self.items.pop(_hash(token), None) is not None:
            self._save()

    def revoke(self, sid: str | None = None, user_id: str | None = None) -> int:
        """Sign out one session (sid), everything for a user, or everyone (no arguments)."""
        before = len(self.items)
        self.items = {
            k: v
            for k, v in self.items.items()
            if not ((sid is None and user_id is None) or v["sid"] == sid or (user_id and v["user_id"] == user_id))
        }
        removed = before - len(self.items)
        if removed:
            self._save()
        return removed

    def listing(self) -> list[dict[str, Any]]:
        return sorted(
            (
                {k: v[k] for k in ("sid", "user_id", "name", "avatar", "created", "last_seen")}
                for v in self.items.values()
            ),
            key=lambda x: -x["last_seen"],
        )


# --------------------------------------------------------------------- Discord sign-in
def client_secret() -> str:
    return os.environ.get(SECRET_NAME, "").strip()


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "scope": "identify",
            "state": state,
            "redirect_uri": redirect_uri,
            "prompt": "none",
        }
    )
    return f"https://discord.com/oauth2/authorize?{query}"


class SignInError(Exception):
    """Shown on the sign-in page."""


async def exchange_code(client_id: str, code: str, redirect_uri: str) -> dict[str, Any]:
    """Trade the code from Discord for the user's id, name and avatar."""
    secret = client_secret()
    if not secret:
        raise SignInError("Discord sign-in isn't finished being set up (the client secret is missing).")
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        async with http.post(
            f"{DISCORD_API}/oauth2/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            auth=aiohttp.BasicAuth(client_id, secret),
        ) as r:
            body = await r.json(content_type=None)
            if r.status != 200:
                log.warning("Discord sign-in failed: %s %s", r.status, body)
                if body.get("error") == "invalid_client":
                    raise SignInError("Discord didn't accept Pingwin's client secret. An admin needs to update it.")
                if "redirect_uri" in json.dumps(body):
                    raise SignInError(
                        "Discord doesn't know this dashboard's address yet. An admin needs to add it under "
                        "OAuth2 → Redirects in the Developer Portal."
                    )
                raise SignInError("Discord didn't finish the sign-in. Try again.")
            access = body["access_token"]
        async with http.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {access}"}) as r:
            if r.status != 200:
                raise SignInError("Couldn't read your Discord account. Try again.")
            me = await r.json()
        try:  # we only needed it once
            await http.post(
                f"{DISCORD_API}/oauth2/token/revoke",
                data={"token": access, "token_type_hint": "access_token"},
                auth=aiohttp.BasicAuth(client_id, secret),
            )
        except aiohttp.ClientError:
            pass
    avatar = None
    if me.get("avatar"):
        avatar = f"https://cdn.discordapp.com/avatars/{me['id']}/{me['avatar']}.png?size=64"
    return {"id": me["id"], "name": me.get("global_name") or me.get("username") or "Someone", "avatar": avatar}


async def check_member(bot: Pingwin, user_id: int) -> tuple[bool, str, discord.Member | None]:
    """Is this person allowed to use the dashboard? Returns (ok, reason if not, member)."""
    guild = bot.home_guild
    if guild is None:
        return False, "Pingwin isn't connected to the server right now. Try again in a minute.", None
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return False, f"You're not a member of {guild.name}.", None
        except discord.HTTPException:
            return False, "Couldn't check your roles with Discord. Try again in a minute.", None
    perms = member.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True, "", member
    role = bot.config.dashboard.admin_role
    if role and any(r.name.casefold() == role.casefold() for r in member.roles):
        return True, "", member
    need = "Manage Server" + (f" or the {role} role" if role else "")
    return False, f"You're signed in as {member.display_name}, but the dashboard needs {need} in {guild.name}.", member
