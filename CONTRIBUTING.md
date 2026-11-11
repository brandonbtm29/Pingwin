# Contributing to Pingwin

Thanks for helping! A few ground rules:

## Never commit secrets or real server settings

- The bot token lives in `secrets.env` in the shared settings folder, never in this repo.
- `.env`, `secrets.env` and `config.toml` are git-ignored, and the pre-commit hook blocks them.
- Keep examples generic: no real member names, server IDs or channel IDs.
- If a token ever lands in a commit, reset it in the Discord Developer Portal right away.
  Removing the commit isn't enough.

## Keep standby PCs quiet

Several PCs may run Pingwin at once, and only the active one should act.

- Slash commands and buttons are already gated: standby PCs ignore them.
- Anything that runs on a timer (reminders, polls, stream checks) must check `bot.is_active` first.
- Acknowledge interactions (`defer` or `send_message`) **before** doing the work, so if two PCs
  ever overlap, only the one that acknowledged acts.

## Adding a feature

Each feature is a cog in `src/pingwin/cogs/` with its own slash commands. Add it to
`EXTENSIONS` in `bot.py`, put any user-facing text in the persona files (with a fallback in
`DEFAULT_MESSAGES`), and add a line to `CHANGELOG.md`.

## Workflow

```bash
pip install -e ".[dev]"
pip install pre-commit && pre-commit install
pytest
ruff check src tests && ruff format src tests
```

CI runs the tests on Linux and Windows with Python 3.11 to 3.13, plus a gitleaks secret scan.

## Signing commits

Every PC installs an update only if the newest commit on `main` is signed with a key in
`.github/allowed_signers`, and GitHub refuses unsigned commits on `main`. To sign from a computer:

1. Use an SSH key you already have (`~/.ssh/id_ed25519.pub`), or make one: `ssh-keygen -t ed25519`.
2. Tell git to sign every commit with it:
   ```bash
   git config --global gpg.format ssh
   git config --global user.signingkey ~/.ssh/id_ed25519.pub
   git config --global commit.gpgsign true
   ```
3. Add it to GitHub as a **signing key**: `gh ssh-key add ~/.ssh/id_ed25519.pub --type signing --title "My PC"`
   (or Settings → SSH and GPG keys → New SSH key, Key type: Signing Key). Then GitHub shows your commits
   as *Verified*.
4. Add the key to `.github/allowed_signers` (one line: your commit email, `namespaces="git"`, then the key)
   in a commit signed with a key that's already listed. The PCs trust it from the update after that.

To check signatures yourself: `git config --global gpg.ssh.allowedSignersFile .github/allowed_signers`, then
`git log --show-signature`. Commits made on GitHub's website (like merging a pull request there) are signed
by GitHub, not by a listed key, so the PCs won't install them until a commit signed with your key comes
after. Merge pull requests (like Dependabot's) on your PC with a merge commit instead, so your signed commit
is the newest: `git fetch origin pull/12/head:pr-12`, `git merge --no-ff pr-12`, `git push`.

## Releasing

Bump the version in `pyproject.toml` and `src/pingwin/__init__.py`, update `CHANGELOG.md`, and
push to `main` (signed). Every running Pingwin picks it up within `[updates] check_minutes`.
