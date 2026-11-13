"""Asks SyncThing on this PC whether its copy of the shared settings folder is up to date. Read-only.

Failover uses this so a PC higher in the list can take the job back as soon as what the backup wrote to state/
has arrived, instead of waiting a flat few minutes. SyncThing's own settings file (config.xml) says where its
local API listens and holds the API key for it. Pingwin only reads from that API (on this PC), never changes
SyncThing, and never logs the key.

If SyncThing isn't installed or running here, or the folder isn't in it, folder_sync() returns None and
failover waits the old way.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import ssl
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiohttp

log = logging.getLogger("pingwin.syncthing")

TIMEOUT = 5  # seconds for SyncThing's API to answer (it's on this PC)
_NEED = ("needFiles", "needDirectories", "needSymlinks", "needDeletes", "needBytes")
_warned = ""


@dataclass(frozen=True)
class FolderSync:
    idle: bool  # not scanning or syncing, and nothing left to download
    waiting_for: str  # what's still to come, in words ("" when idle)
    uptime: float  # seconds SyncThing has been running
    peers: tuple[float, ...]  # for each other device sharing the folder that's connected: seconds since it connected


@dataclass(frozen=True)
class _Api:
    base: str
    key: str
    context: ssl.SSLContext | bool  # how to check SyncThing's certificate (False: plain http)
    folder: str  # SyncThing's folder ID
    devices: tuple[str, ...]  # device IDs the folder is shared with (this PC's own included)


def config_dirs() -> list[Path]:
    """Where SyncThing keeps config.xml on this PC (the usual places; SyncTrayzor uses the first one too)."""
    home = Path.home()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return [Path(local) / "Syncthing"] if local else []
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Syncthing"]
    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state")
    conf = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return [state / "syncthing", conf / "syncthing"]


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(p)))).rstrip("\\/")


def find_api(folder_path: Path, dirs: list[Path] | None = None) -> _Api | None:
    """SyncThing's local API and the SyncThing folder that holds folder_path, from config.xml."""
    want = _norm(folder_path)
    for d in config_dirs() if dirs is None else dirs:
        try:
            root = ET.parse(d / "config.xml").getroot()
        except (OSError, ET.ParseError):
            continue
        gui = root.find("gui")
        if gui is None:
            continue
        key = (gui.findtext("apikey") or "").strip()
        address = (gui.findtext("address") or "").strip()
        if not key or not address or "://" in address or address.startswith("/"):
            continue  # no API key, or the API is on a unix socket
        host, _, port = address.rpartition(":")
        if host in ("", "0.0.0.0", "::", "[::]"):
            host = "127.0.0.1"
        if gui.get("tls", "").lower() == "true":
            cert = d / "https-cert.pem"  # SyncThing's own certificate: trust exactly that one
            try:
                context: ssl.SSLContext | bool = ssl.create_default_context(cafile=str(cert))
            except (OSError, ssl.SSLError):
                continue
            context.check_hostname = False  # the certificate is for "syncthing", not the address
            base = f"https://{host}:{port}"
        else:
            context, base = False, f"http://{host}:{port}"
        best = None
        for f in root.findall("folder"):
            path = _norm(f.get("path") or "")
            if path and (want == path or want.startswith(path + os.sep)) and (best is None or len(path) > best[0]):
                best = (len(path), f)
        if best is None:
            continue
        devices = tuple(dev.get("id") for dev in best[1].findall("device") if dev.get("id"))
        return _Api(base, key, context, best[1].get("id") or "", devices)
    return None


def _since(stamp: str) -> float | None:
    """Seconds since an RFC 3339 time from SyncThing (which has up to 9 decimal places)."""
    stamp = re.sub(r"(\.\d{6})\d+", r"\1", stamp.strip()).replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if when.year < 2000:  # "0001-01-01T00:00:00Z": never
        return None
    return max(0.0, time.time() - when.timestamp())


def summarize(status: dict, db: dict, connections: dict, devices: tuple[str, ...]) -> FolderSync:
    """FolderSync from SyncThing's /rest/system/status, /rest/db/status and /rest/system/connections."""
    state = str(db.get("state", ""))
    need = {k: int(db.get(k) or 0) for k in _NEED}
    idle = state == "idle" and not any(need.values())
    files = need["needFiles"] + need["needDeletes"] + need["needDirectories"] + need["needSymlinks"]
    if idle:
        waiting = ""
    elif files:
        waiting = f"SyncThing has {files} file{'s' if files != 1 else ''} to bring over"
    elif state in ("scanning", "scan-waiting"):
        waiting = "SyncThing is checking the folder"
    else:
        waiting = f"SyncThing is {state.replace('-', ' ') or 'busy'}"
    me = status.get("myID")
    peers = []
    for dev in devices:
        c = connections.get(dev) or {}
        if dev == me or not c.get("connected") or c.get("paused"):
            continue
        age = _since(str(c.get("startedAt", "")))
        if age is not None:
            peers.append(age)
    return FolderSync(idle, waiting, float(status.get("uptime") or 0), tuple(sorted(peers)))


async def folder_sync(folder_path: Path) -> FolderSync | None:
    """How this PC's copy of the SyncThing folder holding folder_path is doing, or None if SyncThing can't tell."""
    global _warned
    api = await asyncio.to_thread(find_api, folder_path)
    if api is None:
        return None
    try:
        async with aiohttp.ClientSession(
            api.base, headers={"X-API-Key": api.key}, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
        ) as http:

            async def get(path: str, **params: str) -> dict:
                async with http.get(path, params=params, ssl=api.context) as r:
                    r.raise_for_status()
                    return await r.json()

            status = await get("/rest/system/status")
            db = await get("/rest/db/status", folder=api.folder)
            connections = (await get("/rest/system/connections")).get("connections") or {}
    except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError) as e:
        why = type(e).__name__
        if why != _warned:  # once per kind of problem, not every few seconds
            _warned = why
            log.warning("Couldn't ask SyncThing on this PC whether shared files are in sync (%s)", why)
        return None
    _warned = ""
    return summarize(status, db, connections, api.devices)
