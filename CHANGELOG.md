# Changelog

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
