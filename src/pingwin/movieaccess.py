"""Easy ways into the movie pages, without Discord's sign-in screen.

- A shared **view-only link** (one secret for everyone). Anyone with it can browse the history,
  reviews and watchlists, but can't write anything. Admins can reset it, which cuts off the old link.
- **Personal sign-in links** from /pingwin movie page: single-use, short-lived codes that sign one
  member in (the same as Discord sign-in), so they can write reviews and suggest movies.

Kept in the shared state folder, so every PC agrees on the current link.
"""

from __future__ import annotations

import hmac
import secrets
import time
from typing import Any

from . import store

STATE = "movie_access"
CODE_SECONDS = 15 * 60


def _load() -> dict[str, Any]:
    data = store.load(STATE)
    data.setdefault("codes", {})
    return data


def share_token(create: bool = True) -> str | None:
    """The current view-only secret (made on first use)."""
    data = _load()
    if not data.get("view") and create:
        data["view"] = secrets.token_urlsafe(18)
        data["view_set"] = time.time()
        store.save(STATE, data)
    return data.get("view")


def reset_share() -> str:
    data = _load()
    data["view"] = secrets.token_urlsafe(18)
    data["view_set"] = time.time()
    store.save(STATE, data)
    return data["view"]


def is_share_token(token: str | None) -> bool:
    current = share_token(create=False)
    return bool(token and current and hmac.compare_digest(token, current))


def posted() -> tuple[int, int] | None:
    """(channel id, message id) of the pinned view link post, if there is one."""
    p = _load().get("posted")
    return (int(p[0]), int(p[1])) if p else None


def remember_post(channel_id: int | None, message_id: int | None) -> None:
    data = _load()
    data["posted"] = [str(channel_id), str(message_id)] if channel_id and message_id else None
    store.save(STATE, data)


def new_login_code(user_id: int | str, name: str, avatar: str | None = None) -> str:
    data = _load()
    now = time.time()
    data["codes"] = {c: v for c, v in data["codes"].items() if v.get("expires", 0) > now}
    code = secrets.token_urlsafe(24)
    data["codes"][code] = {"id": str(user_id), "name": name, "avatar": avatar, "expires": now + CODE_SECONDS}
    store.save(STATE, data)
    return code


def peek_login_code(code: str | None) -> dict[str, Any] | None:
    """Who a sign-in code is for, without using it up (link previews open links too)."""
    found = _load()["codes"].get(code or "")
    return found if found and found.get("expires", 0) >= time.time() else None


def take_login_code(code: str | None) -> dict[str, Any] | None:
    """The member a sign-in code belongs to. Each code works once, until it expires."""
    if not code:
        return None
    data = _load()
    found = data["codes"].pop(code, None)
    now = time.time()
    data["codes"] = {c: v for c, v in data["codes"].items() if v.get("expires", 0) > now}
    store.save(STATE, data)
    if not found or found.get("expires", 0) < now:
        return None
    return found
