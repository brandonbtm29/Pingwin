"""Pair a backup PC with the main PC through SyncThing, using two short codes.

1. Main PC:   create_invite()  -> "PINGWIN-INVITE-..."  (its SyncThing device ID + settings folder ID)
2. Backup PC: join(invite)     -> "PINGWIN-REPLY-..."   (its device ID + PC name)
3. Main PC:   accept(reply)    -> shares the settings folder with the backup and adds it to the PC list

The codes hold only SyncThing device and folder IDs, which aren't secret. Nothing syncs until the
main PC's owner accepts the reply code.

Run from the command line:  python -m pingwin.pairing invite | accept CODE | join CODE --pc NAME --folder PATH
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INVITE_PREFIX = "PINGWIN-INVITE-"
REPLY_PREFIX = "PINGWIN-REPLY-"
DEFAULT_FOLDER_ID = "pingwin-settings"
DEFAULT_LABEL = "Pingwin"

_DEVICE_ID = re.compile(r"^[A-Z2-7]{7}(-[A-Z2-7]{7}){7}$")
_FOLDER_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PC_NAME = re.compile(r"^[A-Za-z0-9 ._-]{1,40}$")


class PairingError(Exception):
    """Something went wrong, worded for the person running it."""


# ------------------------------------------------------------------ codes
def _pack(prefix: str, data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return prefix + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unpack(prefix: str, code: str, kind: str) -> dict[str, Any]:
    code = "".join(code.split())  # tolerate line breaks/spaces from chat apps
    if not code.upper().startswith(prefix):
        raise PairingError(f"That doesn't look like a Pingwin {kind} code (it should start with {prefix}).")
    body = code[len(prefix) :]
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, json.JSONDecodeError) as e:
        raise PairingError(f"That {kind} code is incomplete or damaged. Copy the whole code and try again.") from e
    if not isinstance(data, dict):
        raise PairingError(f"That {kind} code is damaged.")
    return data


def _check_device(device_id: object) -> str:
    if not isinstance(device_id, str) or not _DEVICE_ID.match(device_id):
        raise PairingError("The code contains an invalid SyncThing device ID.")
    return device_id


@dataclass(frozen=True)
class Invite:
    device_id: str
    folder_id: str
    label: str = DEFAULT_LABEL

    def code(self) -> str:
        return _pack(INVITE_PREFIX, {"v": 1, "d": self.device_id, "f": self.folder_id, "l": self.label})

    @classmethod
    def parse(cls, code: str) -> Invite:
        data = _unpack(INVITE_PREFIX, code, "invite")
        folder = data.get("f")
        if not isinstance(folder, str) or not _FOLDER_ID.match(folder):
            raise PairingError("The invite code contains an invalid folder ID.")
        label = str(data.get("l") or DEFAULT_LABEL)[:64]
        return cls(_check_device(data.get("d")), folder, label)


@dataclass(frozen=True)
class Reply:
    device_id: str
    pc_name: str

    def code(self) -> str:
        return _pack(REPLY_PREFIX, {"v": 1, "d": self.device_id, "n": self.pc_name})

    @classmethod
    def parse(cls, code: str) -> Reply:
        data = _unpack(REPLY_PREFIX, code, "reply")
        name = data.get("n")
        if not isinstance(name, str) or not _PC_NAME.match(name):
            raise PairingError("The reply code contains an invalid PC name.")
        return cls(_check_device(data.get("d")), name)


# ------------------------------------------------------------------ SyncThing
def syncthing_config_candidates() -> list[Path]:
    override = os.environ.get("PINGWIN_SYNCTHING_CONFIG", "").strip()
    if override:
        return [Path(override)]
    home = Path.home()
    paths = []
    for var in ("LOCALAPPDATA", "APPDATA"):
        if os.environ.get(var):
            paths.append(Path(os.environ[var]) / "Syncthing" / "config.xml")
    paths += [
        home / ".local" / "state" / "syncthing" / "config.xml",
        home / ".config" / "syncthing" / "config.xml",
        home / "Library" / "Application Support" / "Syncthing" / "config.xml",
    ]
    return paths


def find_syncthing_config() -> Path | None:
    return next((p for p in syncthing_config_candidates() if p.exists()), None)


def _same_path(a: str | Path, b: str | Path) -> bool:
    def norm(p: str | Path) -> str:
        return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(str(p)))))

    return norm(a) == norm(b)


class Syncthing:
    """A tiny client for SyncThing's local REST API."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @classmethod
    def from_config(cls, path: Path | None = None) -> Syncthing:
        path = path or find_syncthing_config()
        if path is None:
            raise PairingError("SyncThing isn't set up on this PC (no config.xml found).")
        try:
            gui = ET.parse(path).getroot().find("gui")
        except (ET.ParseError, OSError) as e:
            raise PairingError(f"Couldn't read SyncThing's settings at {path}: {e}") from e
        if gui is None or not (gui.findtext("apikey") or "").strip():
            raise PairingError("SyncThing's settings have no API key. Open SyncThing once and try again.")
        address = (gui.findtext("address") or "127.0.0.1:8384").strip()
        if address.startswith("/") or address.startswith("unix"):
            raise PairingError("SyncThing listens on a unix socket here, which Pingwin can't use yet.")
        address = address.split("://")[-1]
        host, _, port = address.rpartition(":")
        if host in ("", "0.0.0.0", "[::]", "::"):
            host = "127.0.0.1"
        scheme = "https" if gui.get("tls", "false").lower() == "true" else "http"
        return cls(f"{scheme}://{host}:{port}", gui.findtext("apikey").strip())

    # -- plumbing
    def _request(self, method: str, path: str, body: Any = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
        )
        # SyncThing's local GUI uses a self-signed certificate; this only ever talks to this PC.
        context = ssl._create_unverified_context() if self.base_url.startswith("https") else None
        try:
            with urllib.request.urlopen(req, timeout=15, context=context) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise KeyError(path) from e
            detail = e.read().decode(errors="replace").strip()
            raise PairingError(f"SyncThing refused the change ({e.code}): {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            raise PairingError("SyncThing isn't running on this PC. Start it and try again.") from e
        return json.loads(raw) if raw.strip() else None

    def ping(self) -> bool:
        try:
            self._request("GET", "/rest/system/ping")
            return True
        except PairingError:
            return False

    def my_id(self) -> str:
        return self._request("GET", "/rest/system/status")["myID"]

    def folders(self) -> list[dict[str, Any]]:
        return self._request("GET", "/rest/config/folders")

    def folder(self, folder_id: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/rest/config/folders/{folder_id}")
        except KeyError:
            return None

    def device(self, device_id: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/rest/config/devices/{device_id}")
        except KeyError:
            return None

    # -- changes
    def ensure_device(self, device_id: str, name: str, addresses: list[str] | None = None) -> bool:
        """Add a remote device if it isn't known yet. Returns True if it was added."""
        if self.device(device_id) is not None:
            return False
        dev = self._request("GET", "/rest/config/defaults/device")
        dev.update({"deviceID": device_id, "name": name})
        if addresses:
            dev["addresses"] = addresses
        self._request("POST", "/rest/config/devices", dev)
        return True

    def ensure_folder(self, folder_id: str, label: str, path: str, device_ids: list[str]) -> bool:
        """Create the folder, or make sure it's shared with these devices. Returns True if created."""
        existing = self.folder(folder_id)
        if existing is not None:
            for d in device_ids:
                self.share_folder(folder_id, d)
            return False
        folder = self._request("GET", "/rest/config/defaults/folder")
        folder.update(
            {
                "id": folder_id,
                "label": label,
                "path": path,
                "type": "sendreceive",
                "devices": [{"deviceID": d, "introducedBy": "", "encryptionPassword": ""} for d in device_ids],
            }
        )
        self._request("POST", "/rest/config/folders", folder)
        return True

    def share_folder(self, folder_id: str, device_id: str) -> bool:
        folder = self.folder(folder_id)
        if folder is None:
            raise PairingError(f"SyncThing has no folder with ID {folder_id}.")
        devices = folder.get("devices") or []
        if any(d.get("deviceID") == device_id for d in devices):
            return False
        devices.append({"deviceID": device_id, "introducedBy": "", "encryptionPassword": ""})
        self._request("PATCH", f"/rest/config/folders/{folder_id}", {"devices": devices})
        return True

    def folder_for_path(self, path: str | Path) -> dict[str, Any] | None:
        return next((f for f in self.folders() if _same_path(f.get("path", ""), path)), None)


# ------------------------------------------------------------------ the three steps
def create_invite(st: Syncthing, settings_folder: Path) -> tuple[str, list[str]]:
    """Main PC: make sure the settings folder is in SyncThing, then build an invite code."""
    notes: list[str] = []
    if not settings_folder.exists():
        raise PairingError(f"The settings folder {settings_folder} doesn't exist.")
    me = st.my_id()
    folder = st.folder_for_path(settings_folder)
    if folder is None:
        folder_id = DEFAULT_FOLDER_ID
        n = 2
        while st.folder(folder_id) is not None:
            folder_id = f"{DEFAULT_FOLDER_ID}-{n}"
            n += 1
        st.ensure_folder(folder_id, DEFAULT_LABEL, str(settings_folder), [me])
        notes.append(f"Added your settings folder to SyncThing as '{DEFAULT_LABEL}' (folder ID {folder_id}).")
        label = DEFAULT_LABEL
    else:
        folder_id, label = folder["id"], folder.get("label") or DEFAULT_LABEL
        notes.append(f"Your settings folder is already in SyncThing as '{label}' (folder ID {folder_id}).")
    return Invite(me, folder_id, label).code(), notes


def join(
    st: Syncthing, invite_code: str, settings_folder: Path, pc_name: str, *, main_addresses: list[str] | None = None
) -> str:
    """Backup PC: trust the main PC and set up the settings folder. Returns the reply code."""
    invite = Invite.parse(invite_code)
    if not _PC_NAME.match(pc_name):
        raise PairingError("The PC name can only use letters, numbers, spaces, dots, dashes and underscores.")
    me = st.my_id()
    if me == invite.device_id:
        raise PairingError("That invite was made on this same PC. Run the installer on the backup PC instead.")
    st.ensure_device(invite.device_id, "Pingwin main PC", main_addresses)
    settings_folder.mkdir(parents=True, exist_ok=True)
    st.ensure_folder(invite.folder_id, invite.label, str(settings_folder), [me, invite.device_id])
    return Reply(me, pc_name).code()


def accept(
    st: Syncthing, reply_code: str, settings_folder: Path, *, backup_addresses: list[str] | None = None
) -> list[str]:
    """Main PC: share the settings folder with the backup and add it to the PC list."""
    from .config import ConfigError, load_config
    from .configedit import save_failover

    reply = Reply.parse(reply_code)
    if reply.device_id == st.my_id():
        raise PairingError("That reply code came from this same PC.")
    folder = st.folder_for_path(settings_folder)
    if folder is None:
        raise PairingError("Your settings folder isn't in SyncThing yet. Create an invite first.")
    notes = []
    if st.ensure_device(reply.device_id, f"Pingwin backup ({reply.pc_name})", backup_addresses):
        notes.append(f"Added {reply.pc_name} to SyncThing.")
    if st.share_folder(folder["id"], reply.device_id):
        notes.append(f"Shared the settings folder with {reply.pc_name}. It syncs within a minute or two.")
    else:
        notes.append(f"The settings folder was already shared with {reply.pc_name}.")
    try:
        cfg = load_config(require_token=False).failover
        if cfg.rank(reply.pc_name) > len(cfg.pcs):
            save_failover(
                enabled=cfg.enabled,
                status_channel_id=cfg.status_channel_id,
                pcs=[*cfg.pcs, reply.pc_name],
                heartbeat_seconds=cfg.heartbeat_seconds,
                takeover_after_seconds=cfg.takeover_after_seconds,
            )
            notes.append(f"Added {reply.pc_name} to the end of the PC list.")
        else:
            notes.append(f"{reply.pc_name} is already in the PC list.")
        if not cfg.active:
            notes.append("Failover is still off: pick a status channel and tick it on the PCs & failover tab.")
    except ConfigError as e:
        notes.append(f"Couldn't update the PC list automatically ({e}). Add {reply.pc_name} on the dashboard.")
    return notes


# ------------------------------------------------------------------ command line
def main(argv: list[str] | None = None) -> int:
    from .__main__ import _load_env
    from .config import data_dir

    parser = argparse.ArgumentParser(prog="python -m pingwin.pairing", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("invite", help="main PC: make an invite code for a backup PC")
    p_accept = sub.add_parser("accept", help="main PC: accept a backup PC's reply code")
    p_accept.add_argument("code")
    p_join = sub.add_parser("join", help="backup PC: use an invite code; prints the reply code")
    p_join.add_argument("code")
    p_join.add_argument("--pc", required=True, help="this PC's name, as it should appear in the PC list")
    p_join.add_argument("--folder", required=True, help="where to keep the synced settings")
    sub.add_parser("check", help="check that SyncThing is reachable")
    args = parser.parse_args(argv)

    _load_env()
    try:
        st = Syncthing.from_config()
        if args.cmd == "check":
            print(f"SyncThing is running. This PC's device ID: {st.my_id()}")
        elif args.cmd == "invite":
            code, notes = create_invite(st, data_dir())
            print("\n".join(notes))
            print(f"\nInvite code:\n{code}")
        elif args.cmd == "accept":
            print("\n".join(accept(st, args.code, data_dir())))
        elif args.cmd == "join":
            code = join(st, args.code, Path(args.folder), args.pc)
            print(f"REPLY {code}")
    except PairingError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
