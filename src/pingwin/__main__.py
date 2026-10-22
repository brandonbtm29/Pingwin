"""Run Pingwin:  python -m pingwin   (or just `pingwin` once installed)."""

from __future__ import annotations

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

import discord
from dotenv import load_dotenv

from .config import PROJECT_ROOT, ConfigError, MissingSettings, data_dir, load_config

EXIT_CONFIG_ERROR = 2  # scripts/run_pingwin.cmd stops instead of restarting on this
EXIT_ALREADY_RUNNING = 4
LOCK_FILE = PROJECT_ROOT / ".pingwin.lock"


def _single_instance():
    """Hold a lock for as long as Pingwin runs, so it can't start twice on the same PC.

    Returns the open lock file (keep a reference!), or None if another copy holds it.
    """
    handle = open(LOCK_FILE, "a+")  # noqa: SIM115 - must stay open while running
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")  # per-PC: PINGWIN_DATA, PINGWIN_NODE
    load_dotenv(data_dir() / "secrets.env")  # shared: DISCORD_TOKEN (doesn't override the above)


def _setup_logging() -> None:
    discord.utils.setup_logging(level=logging.INFO, root=True)
    logs = PROJECT_ROOT / "logs"  # per-PC, not synced
    logs.mkdir(exist_ok=True)
    handler = RotatingFileHandler(logs / "pingwin.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def _load_config_waiting(log: logging.Logger):
    """Load settings. On a backup PC whose shared folder hasn't synced yet, wait for it instead of quitting."""
    shared = os.environ.get("PINGWIN_DATA", "").strip() and data_dir() != PROJECT_ROOT
    announced = False
    while True:
        try:
            return load_config()
        except MissingSettings as e:
            if not shared:
                raise
            if not announced:
                log.warning("%s", e)
                log.info("Waiting for the shared settings to arrive from the main PC (checking every 20 s)...")
                announced = True
            time.sleep(20)
            _load_env()


def main() -> int:
    _load_env()
    _setup_logging()
    log = logging.getLogger("pingwin")

    lock = _single_instance()
    if lock is None:
        log.error("Pingwin is already running on this PC (look for its other window). Not starting a second copy.")
        return EXIT_ALREADY_RUNNING

    from .bot import Pingwin  # after logging, so import-time messages are captured

    try:
        config = _load_config_waiting(log)
        bot = Pingwin(config)
    except ConfigError as e:
        log.error("%s", e)
        return EXIT_CONFIG_ERROR

    try:
        bot.run(config.token, log_handler=None)  # reconnects on its own after internet drops
    except discord.LoginFailure:
        log.error("Discord rejected the token. Reset it in the Developer Portal and update secrets.env.")
        return EXIT_CONFIG_ERROR
    except discord.PrivilegedIntentsRequired:
        # Don't let one feature keep the whole bot offline: start without those intents and say what's paused.
        bot = Pingwin(config, without_privileged=True)
        log.warning(
            "Discord hasn't allowed %s for Pingwin yet, so the features that need it (welcome, mod log) are paused. "
            "Turn it on in the Discord Developer Portal (your app -> Bot -> Privileged Gateway Intents), then "
            "restart Pingwin. Starting without it for now.",
            " and ".join(bot.missing_intents) or "a privileged intent",
        )
        try:
            bot.run(config.token, log_handler=None)
        except discord.LoginFailure:
            log.error("Discord rejected the token. Reset it in the Developer Portal and update secrets.env.")
            return EXIT_CONFIG_ERROR
    finally:
        lock.close()
    return bot.exit_code


if __name__ == "__main__":
    sys.exit(main())
