# Changelog

## 0.4.0 (2026-10-19)
- Pingwin has an icon (`assets/`), used on the dashboard and README too.
- **Setup guide** (`docs/SETUP.md`): step by step from creating the bot to posting the first menu, with troubleshooting.
- **Setup tab** on the dashboard: a live checklist of what's done and what's left, with Go buttons, plus an About section
  (version, links, invite link).
- `setup.cmd` now asks for the bot token and server ID, so new installs need no file editing.
- `scripts\install_autostart.cmd` / `remove_autostart.cmd`: start Pingwin when you sign in (no admin needed).

## 0.3.0 (2026-10-19)
- **Dashboard**: a settings page in your browser (`scripts\open_dashboard.cmd`), on each PC running Pingwin.
  - Status and controls: which PC is answering, connection, version; reload, restart, check for updates.
  - Role menu editor: add, remove, reorder roles; emoji, descriptions, gate role; live "ready / not pingable" checks.
  - PCs & failover: order of PCs, status channel, timing.
  - Recent log with a warnings filter.
  - Only answers on 127.0.0.1, needs a sign-in key, and blocks requests from other websites.
- Posted menus are remembered (`menus.json` in the settings folder) and can be updated in place:
  `/rolemenu refresh` or "Update posted menus" on the dashboard.
- Settings edits keep the comments in `config.toml` and are checked before they're saved.

## 0.2.0 (2026-10-18)
- Shared settings folder (`PINGWIN_DATA`), meant for SyncThing: `config.toml`, `secrets.env`, `personas\`.
- Failover: several PCs can run Pingwin; one answers, backups take over if it goes quiet.
- Self-updating from GitHub; `run_pingwin.cmd` pulls and reinstalls before each start.
- Settings reload automatically when the files change.
- New commands: `/pingwin status`, `/pingwin restart`.
- `[failover] pcs` lists PCs in order of preference; unlisted PCs only step in when all listed ones are offline.
- `setup.cmd` now asks for the settings folder and this PC's name.

## 0.1.0 (2026-10-18)
- Opt-in ping role menu with buttons, gated by the Friend role.
- `/rolemenu setup | check | post`, `/pingwin about | reload`.
- Swappable personas (`pingwin`, `deadpan`).
