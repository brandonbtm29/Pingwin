"""Updates install only when GitHub's newest commit is signed with a trusted key (.github/allowed_signers)."""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from pingwin import updater

pytestmark = pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="needs OpenSSH's ssh-keygen")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def key(tmp: Path, name: str) -> tuple[Path, str]:
    path = tmp / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)], check=True)
    return path, (tmp / f"{name}.pub").read_text().split(" ", 2)[:2]


def commit(repo: Path, text: str, signing_key: Path | None = None) -> None:
    (repo / "file.txt").write_text(text)
    git(repo, "add", "-A")
    sign = ["-c", "commit.gpgsign=false", "commit"]  # (some machines sign every commit by default)
    if signing_key:
        program = shutil.which("ssh-keygen")
        sign = ["-c", "gpg.format=ssh", "-c", f"gpg.ssh.program={program}", "-c", f"user.signingkey={signing_key}"]
        sign += ["commit", "-S"]
    try:
        git(repo, "-c", "user.name=T", "-c", "user.email=t@example.com", *sign, "-q", "-m", text)
    except subprocess.CalledProcessError as e:  # e.g. OpenSSH refusing a key file other users can read
        if not signing_key:
            raise
        pytest.skip(f"can't sign commits here: {e.stderr.strip()[:200]}")


@pytest.fixture
def repos(tmp_path):
    """GitHub (a bare repo), the PC's copy of it, and a trusted and an unknown key."""
    trusted, trusted_pub = key(tmp_path, "trusted")
    other, _ = key(tmp_path, "other")
    origin, work, pc = tmp_path / "origin.git", tmp_path / "work", tmp_path / "pc"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(work))
    (work / ".github").mkdir()
    (work / ".github" / "allowed_signers").write_text(f't@example.com namespaces="git" {" ".join(trusted_pub)}\n')
    commit(work, "one", trusted)
    git(work, "push", "-q", "origin", "HEAD:main")
    git(tmp_path, "clone", "-q", str(origin), str(pc))
    return work, pc, trusted, other


def push(work: Path, text: str, signing_key: Path | None) -> None:
    commit(work, text, signing_key)
    git(work, "push", "-q", "origin", "HEAD:main")


def test_only_updates_signed_with_a_trusted_key(repos):
    work, pc, trusted, other = repos
    assert asyncio.run(updater.update_available(pc)) is False and updater.problem == ""  # nothing new
    push(work, "unsigned", None)
    assert asyncio.run(updater.update_available(pc)) is False and "isn't signed" in updater.problem
    push(work, "someone else's", other)
    assert asyncio.run(updater.update_available(pc)) is False and "isn't on the trusted list" in updater.problem
    push(work, "mine", trusted)
    assert asyncio.run(updater.update_available(pc)) is True and updater.problem == ""


def test_a_new_commit_cant_trust_its_own_key(repos):
    work, pc, trusted, other = repos
    other_pub = (other.parent / "other.pub").read_text().split(" ", 2)[:2]
    with (work / ".github" / "allowed_signers").open("a") as f:
        f.write(f'intruder namespaces="git" {" ".join(other_pub)}\n')
    push(work, "adds its own key", other)
    # The PC checks against the list it already has, so the new key doesn't count.
    assert asyncio.run(updater.update_available(pc)) is False and "isn't on the trusted list" in updater.problem


def test_the_start_script_check(repos, monkeypatch):
    work, pc, trusted, _ = repos
    monkeypatch.chdir(pc)
    assert updater.main(["x", "--check-signature"]) == 0  # nothing new
    push(work, "unsigned", None)
    git(pc, "fetch", "-q")
    assert updater.main(["x", "--check-signature"]) == 1
    push(work, "signed", trusted)
    git(pc, "fetch", "-q")
    assert updater.main(["x", "--check-signature"]) == 0
