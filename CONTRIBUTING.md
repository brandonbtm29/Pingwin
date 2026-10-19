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

## Releasing

Bump the version in `pyproject.toml` and `src/pingwin/__init__.py`, update `CHANGELOG.md`, and
push to `main`. Every running Pingwin picks it up within `[updates] check_minutes`.
