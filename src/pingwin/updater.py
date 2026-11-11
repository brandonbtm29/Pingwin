"""Self-updates from GitHub: if the repo has new commits, Pingwin restarts and
scripts/run_pingwin.cmd pulls them before starting again.

Only commits signed with a trusted key are installed: GitHub's newest commit must carry a signature from
one of the keys in .github/allowed_signers, as this PC already has it (the copy from the version it's
running, so a new commit can't add its own key). Someone who got into the GitHub account can push code,
but not sign it, so the PCs won't run it.

Run as `python -m pingwin.updater --check-signature` (scripts/run_pingwin.cmd does, before pulling) to
check GitHub's newest commit: exit code 0 if it's fine to install, 1 (and why) if not."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger("pingwin.updater")


async def _git(root: Path, *args: str, timeout: float = 60) -> tuple[int, str]:
    try:
        # GIT_* settings (like GIT_CONFIG_*, which can run commands) don't come along.
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=root, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError:
        return 127, "git is not installed"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return 1, "git timed out"
    return proc.returncode or 0, out.decode(errors="replace").strip()


REWRITTEN_LIST = ".github/rewritten-commits.txt"
SIGNERS = Path(".github") / "allowed_signers"
SIGNATURE_STATUS = {  # git's %G? letters, for a commit that isn't signed by a trusted key
    "N": "isn't signed",
    "U": "is signed with a key that isn't on the trusted list",
    "B": "has a bad signature",
    "E": "couldn't be checked",
    "X": "has an expired signature",
    "Y": "is signed with an expired key",
    "R": "is signed with a revoked key",
}
problem = ""  # why the last check found an update it won't install (shown on the dashboard), or ""
_logged = ""


def _ssh_keygen() -> str | None:
    """OpenSSH's ssh-keygen, which checks SSH signatures (Windows has it in System32, Git also bundles one)."""
    found = shutil.which("ssh-keygen")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "ssh-keygen.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "ssh-keygen.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return None


async def upstream_signed(root: Path, ref: str = "@{u}") -> tuple[bool, str]:
    """Whether a commit (GitHub's newest) is signed with a key in this PC's own .github/allowed_signers.
    Returns (fine to install, why not)."""
    signers = root / SIGNERS
    if not signers.exists():
        return True, ""  # a version from before signed updates: nothing to check against yet
    program = _ssh_keygen()
    if program is None:
        return False, "Pingwin can't check the update's signature: OpenSSH's ssh-keygen isn't installed."
    code, out = await _git(
        root,
        "-c",
        f"gpg.ssh.allowedSignersFile={signers}",
        "-c",
        f"gpg.ssh.program={program}",
        "log",
        "-1",
        "--format=%G?",
        ref,
    )
    status = out.strip()[-1:] if not code and out.strip() else "E"
    if status == "G":
        return True, ""
    why = SIGNATURE_STATUS.get(status, "couldn't be checked")
    return False, f"GitHub's newest commit {why}, so Pingwin won't install it (see .github/allowed_signers)."


def _note(why: str) -> None:
    """Remember why an update isn't installed, and log it once (not on every check)."""
    global problem, _logged
    problem = why
    if why and why != _logged:
        log.warning("%s", why)
    _logged = why


async def history_rewritten(root: Path) -> bool:
    """GitHub's history was rewritten (e.g. to remove names) and this PC is on one of the old commits.

    GitHub lists every replaced commit in REWRITTEN_LIST; scripts/run_pingwin.cmd then switches over,
    but only when nothing was changed on this PC."""
    code, head = await _git(root, "rev-parse", "HEAD")
    if code:
        return False
    code, listed = await _git(root, "show", f"@{{u}}:{REWRITTEN_LIST}")
    if code or head.strip() not in {line.strip() for line in listed.splitlines()}:
        return False
    log.info("GitHub's history was tidied up; this PC will switch to it")
    return True


async def update_available(root: Path) -> bool:
    """True when GitHub has newer commits that can be fast-forwarded cleanly."""
    if not (root / ".git").exists():
        return False
    code, out = await _git(root, "fetch", "--quiet")
    if code:
        log.debug("git fetch failed: %s", out)
        return False
    code, out = await _git(root, "rev-list", "--count", "HEAD..@{u}")
    if code or not out.isdigit() or int(out) == 0:
        _note("")
        return False  # no upstream branch yet, or already current
    behind = int(out)
    code, _ = await _git(root, "merge-base", "--is-ancestor", "HEAD", "@{u}")
    if code and not await history_rewritten(root):
        log.warning("Local commits differ from GitHub; skipping auto-update until that's sorted out")
        return False
    code, out = await _git(root, "status", "--porcelain", "--untracked-files=no")
    if code or out:
        log.warning("Uncommitted code changes on this PC; skipping auto-update")
        return False
    signed, why = await upstream_signed(root)
    if not signed:
        _note(why)
        return False
    _note("")
    log.info("%d new commit(s) on GitHub", behind)
    return True


async def _check_for_script(root: Path) -> tuple[bool, str]:
    """For run_pingwin.cmd, after `git fetch`: is GitHub's newest commit fine to install?"""
    code, head = await _git(root, "rev-parse", "HEAD")
    code2, up = await _git(root, "rev-parse", "@{u}")
    if code or code2 or head.strip() == up.strip():
        return True, ""  # nothing new (or no upstream): nothing to check
    return await upstream_signed(root)


def main(argv: list[str]) -> int:
    if argv[1:2] != ["--check-signature"]:
        print("Usage: python -m pingwin.updater --check-signature", file=sys.stderr)
        return 2
    ok, why = asyncio.run(_check_for_script(Path.cwd()))
    if not ok:
        print(why)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
