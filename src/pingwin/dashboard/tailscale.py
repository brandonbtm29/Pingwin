"""Put the dashboard on the internet with Tailscale Funnel, so co-admins can open it from anywhere.

    tailscale funnel --bg --https=10000 http://127.0.0.1:<dashboard port>

Funnel only offers ports 443, 8443 and 10000. Pingwin uses 10000 so it doesn't collide with
anything else on 443 (another app on the same PC may already use it). Visitors don't need
Tailscale: they sign in with Discord.

Requests that arrive through Funnel carry a `Tailscale-Funnel-Request` header, and Tailscale
replaces any Tailscale-* and X-Forwarded-* headers a visitor tries to send, so those can be trusted.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

FUNNEL_PORT = 10000
TIMEOUT = 25  # seconds; Tailscale may wait for approval in the browser the first time
_URL = re.compile(r"https://login\.tailscale\.com/\S+|https://\S*tailscale\.com/\S+")

_CANDIDATES = {
    "win32": [r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe"],
    "darwin": [
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        "/usr/local/bin/tailscale",
        "/opt/homebrew/bin/tailscale",
    ],
}


def find_exe() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    for path in _CANDIDATES.get(sys.platform, []):
        if Path(path).exists():
            return path
    return None


@dataclass
class Result:
    ok: bool
    output: str = ""
    approve_url: str | None = None


def _run(args: list[str]) -> Result:
    exe = find_exe()
    if exe is None:
        return Result(False, "Tailscale isn't installed on this PC.")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
    try:
        p = subprocess.run(
            [exe, *args], capture_output=True, text=True, timeout=TIMEOUT, creationflags=flags, encoding="utf-8"
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "") if isinstance(e.stdout, str) else ""
        m = _URL.search(out)
        return Result(False, "Tailscale is waiting for you to approve something in the browser.", m[0] if m else None)
    except OSError as e:
        return Result(False, f"Couldn't run Tailscale: {e}")
    out = (p.stdout + p.stderr).strip()
    m = _URL.search(out)
    return Result(p.returncode == 0, out, m[0] if m else None)


@dataclass
class Status:
    installed: bool = False
    running: bool = False
    dns_name: str = ""  # e.g. my-pc.tail1234.ts.net
    funnel_on: bool = False  # port 10000 is on the internet and points at this dashboard
    port_taken: str = ""  # port 10000 points somewhere else
    funnel_allowed: bool = True
    error: str = ""

    @property
    def public_url(self) -> str | None:
        if self.funnel_on and self.dns_name:
            return f"https://{self.dns_name}:{FUNNEL_PORT}"
        return None

    def as_json(self) -> dict:
        return {**asdict(self), "public_url": self.public_url, "funnel_port": FUNNEL_PORT}


def status(local_port: int) -> Status:
    st = Status(installed=find_exe() is not None)
    if not st.installed:
        return st
    res = _run(["status", "--json"])
    try:
        data = json.loads(res.output) if res.output.startswith("{") else {}
    except ValueError:
        data = {}
    st.running = data.get("BackendState") == "Running"
    self_ = data.get("Self") or {}
    st.dns_name = str(self_.get("DNSName") or "").rstrip(".")
    caps = " ".join(self_.get("Capabilities") or [])
    if st.running and caps and "funnel" not in caps:
        st.funnel_allowed = False
    if not st.running:
        st.error = "Tailscale isn't signed in on this PC." if data else (res.output or "Tailscale didn't answer.")
        return st
    serve = _run(["serve", "status", "--json"])
    try:
        cfg = json.loads(serve.output) if serve.output.startswith("{") else {}
    except ValueError:
        cfg = {}
    host = f"{st.dns_name}:{FUNNEL_PORT}"
    handlers = ((cfg.get("Web") or {}).get(host) or {}).get("Handlers") or {}
    target = (handlers.get("/") or {}).get("Proxy", "")
    funnel = bool((cfg.get("AllowFunnel") or {}).get(host))
    ours = target.rstrip("/") in (f"http://127.0.0.1:{local_port}", f"http://localhost:{local_port}")
    st.funnel_on = ours and funnel
    if target and not ours:
        st.port_taken = target
    return st


def publish(local_port: int) -> Result:
    args = ["funnel", "--bg", f"--https={FUNNEL_PORT}", f"http://127.0.0.1:{local_port}"]
    res = _run([args[0], "--yes", *args[1:]])
    if not res.ok and "flag provided but not defined" in res.output:
        res = _run(args)
    return res


def unpublish() -> Result:
    return _run(["funnel", f"--https={FUNNEL_PORT}", "off"])
