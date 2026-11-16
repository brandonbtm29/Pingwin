# Pingwin (code)

Public repo (MIT): a self-hosted Discord bot for a friends' server (opt-in ping roles, movie nights, birthdays, event reminders, game days, stream alerts, attendance, welcome / approval, a mod log), with a browser dashboard. Several PCs can run it at once; one is active and the others stand by. It updates itself from GitHub. Read `CONTRIBUTING.md` (the rules below in full), `docs/SETUP.md`, `docs/FEATURES.md`, `docs/BACKUP-PC.md` and `docs/REMOTE-DASHBOARD.md`.

## Never commit secrets or real server settings
- The bot token lives in `secrets.env` in the shared settings folder, never in the repo. `.env`, `secrets.env` and `config.toml` are gitignored and blocked by the pre-commit hook; `config.example.toml`, `.env.example` and `secrets.env.example` stay generic.
- No real member names, server, channel or role IDs, dashboard links or invite codes in code, tests, docs or commit messages. Gitleaks runs in pre-commit and CI (`.gitleaks.toml`).
- If a token ever lands in a commit, it must be reset in the Discord Developer Portal; removing the commit isn't enough.

## Keep standby PCs quiet
- Only the active PC acts (`failover.py`; `syncthing.py` checks the shared settings folder is current before a PC takes the job back). Slash commands and buttons are already gated.
- Anything on a timer (reminders, polls, stream checks) must check `bot.is_active` first.
- Acknowledge an interaction (`defer` / `send_message`) before doing the work, so if two PCs ever overlap only one acts.

## Layout
- `src/pingwin/bot.py` loads the features (`EXTENSIONS`), syncs slash commands and runs housekeeping (failover check-ins, settings reload, update checks). Each feature is a cog in `src/pingwin/cogs/` with its own slash commands.
- `config.py` (where settings live; `config.toml` + token), `features.py` (the settings schema, shared by loading, validation and the dashboard forms), `configedit.py` (edits `config.toml` keeping comments), `store.py` / `menus.py` / `movies.py` (JSON state in the shared settings folder's `state/`), `persona.py` + `personas/*.toml` (all user-facing wording), `updater.py` (self-update), `pairing.py` (pairing a backup PC through SyncThing).
- `dashboard/`: the local web dashboard (`server.py`, `auth.py`, `access.py`, `editing.py`, `setup_check.py`, `tailscale.py` for publishing it with Tailscale Funnel, `static/`).
- `scripts/`: the Windows launch, install, autostart and stop scripts (`run_pingwin.cmd` pulls updates before starting).

## Adding a feature
A new cog in `src/pingwin/cogs/`, added to `EXTENSIONS` in `bot.py`; user-facing text goes in the persona files with a fallback in `DEFAULT_MESSAGES` (`persona.py`); its settings go in the `features.py` schema; a line in `CHANGELOG.md`; tests in `tests/`.

## Run / test
```bash
pip install -e ".[dev]"             # Python 3.11+; ".[voice]" adds speech-to-text for voice ratings
pip install pre-commit && pre-commit install
pytest
ruff check src tests && ruff format src tests     # line length 120
```
CI runs the tests on Linux and Windows with Python 3.11 to 3.13, plus a gitleaks scan. Don't start the bot or the dashboard without saying so: a second running copy joins the failover group.

## Commits and releases
- Every commit on `main` must be SSH-signed with a key listed in `.github/allowed_signers`: PCs only install updates whose newest commit is signed that way, and GitHub refuses unsigned commits on `main`. Don't disable signing. Merge pull requests locally with a signed merge commit (see `CONTRIBUTING.md`), never on GitHub's website.
- Never rewrite or force-push `main` (`.github/rewritten-commits.txt` records the one past rewrite).
- Release: bump the version in `pyproject.toml` and `src/pingwin/__init__.py`, update `CHANGELOG.md`, push to `main` (signed). Running PCs pick it up within `[updates] check_minutes` and restart.
