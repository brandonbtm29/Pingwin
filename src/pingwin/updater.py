"""Self-updates from GitHub: if the repo has new commits, Pingwin restarts and
scripts/run_pingwin.cmd pulls them before starting again."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger("pingwin.updater")


async def _git(root: Path, *args: str, timeout: float = 60) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=root, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
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
    log.info("%d new commit(s) on GitHub", behind)
    return True
