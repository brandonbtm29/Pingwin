# Changelog

## 0.10.0 (2026-10-23)
- **All commands under /pingwin:** `/pingwin movie ...`, `/pingwin birthday ...`, `/pingwin timezone ...` and
  so on, so Pingwin's commands stay together next to other bots. Press Ctrl+R in Discord if the old ones linger.
- **Typed commands:** optionally answer `-pingwin movie nominate MMM The Thing 1982`, `-pingwin movie
  history`, `-pingwin birthday set 9 3` and more (General → *Also answer typed -pingwin commands*; needs the
  Message Content intent).
- **Movie approvals:** members' nominations, removals, watched marks and history fixes go to the mods as
  Approve/Deny cards. Mods' own changes go straight through.
- **Rate it:** after each movie night, Pingwin posts a Rate it button (score, comment, and a question
  passed to the next person), with a live average next to IMDb's. New `/pingwin movie tonight`, `history`,
  `rate` and `page`.
- **Movie details from OMDb:** runtime, director, writers, stars, poster and IMDb rating, looked up once per
  movie and saved (add a free OMDb key under Features → Movie night).
- **Movie pages:** members with the Friend role sign in with Discord at `/movies` on the dashboard's link
  to browse the history, ratings, watchlists and stats, and suggest movies or fixes. The rest of the
  dashboard stays admin-only.
- **Import movie history** from a spreadsheet log and a ratings document (Google links or uploaded files),
  safe to run more than once.

## 0.9.3 (2026-10-23)
- **Movie night showtimes:** each series can have a weekly showtime (like `Mon 8:30pm`). Polls then close
  right when the movie starts, and the poll message says when voting closes.

## 0.9.2 (2026-10-23)
- **Stream alerts for Discord Go Live:** when a listed streamer starts streaming (screen share) in a voice
  channel, Pingwin posts right away with the channel to join. No keys needed; add their Discord username to
  their row. Twitch and YouTube work as before.

## 0.9.1 (2026-10-23)
- Dashboard: every feature on the Features tab links to its setup guide, and the About section links to the
  features and remote access guides. The stream alerts guide explains how to find Twitch and YouTube handles.

## 0.9.0 (2026-10-22)
- **Event attendance:** Pingwin notes who's in a Discord Event's voice channel (or taps **I'm here** at
  in-person events, optionally with a code from the host). When the event ends, everyone who stayed long
  enough gets a reward added to their **UnbelievaBoat** balance (like Frumples), plus an optional host bonus,
  and Pingwin posts a recap. `/attendance add | remove | code | history`, and an Event attendance panel on
  the Features tab to fix attendance and retry payments.

## 0.8.2 (2026-10-22)
- If Discord hasn't allowed a privileged intent that a feature needs, Pingwin now starts anyway and pauses
  just that feature (welcome, mod log), instead of staying offline. The Features and Setup tabs say how to fix it.

## 0.8.1 (2026-10-20)
- Pingwin starts **in the background** with no window (`scripts\start_hidden.vbs`, used at sign-in). If a
  settings problem stops it, a window opens to say what's wrong.
- **Pingwin Dashboard** shortcut on the desktop and in the Start menu, with Pingwin's icon: opens the
  dashboard, starting Pingwin first if needed. `scripts\install_autostart.cmd` adds it (or
  `scripts\create_shortcuts.cmd` on its own). The backup PC installer adds both too.
- **Stop Pingwin** button on the Status tab (owner only), and `scripts\stop_pingwin.cmd`.

## 0.8.0 (2026-10-19)
- **Co-admins can use the dashboard from anywhere:** put it online with Tailscale Funnel (port 10000) from
  **Status → Remote access**, and admins sign in with Discord. Only people with Manage Server (or a role
  you pick) get in, rechecked every few minutes. Backup PCs and remote access stay owner-only unless you
  allow more. `/pingwin dashboard` gives admins the link(s). Guide: [docs/REMOTE-DASHBOARD.md](docs/REMOTE-DASHBOARD.md).
- **Editing together:** the dashboard shows who else has it open (on any PC), which tab, and whether they
  have unsaved changes; a save that would overwrite someone's newer change is stopped with a note saying who
  changed what; and SyncThing conflict copies are listed with a diff so you can keep one.
- **More than one role menu:** each its own message, with "pick any" or "pick one" behaviour.
  `/rolemenu post` takes a `menu`.
- **Time zone menu:** one click on the Role menu tab. Picking a time zone role sets your `/timezone`, and
  `/timezone set` gives the matching role.
- **Birthday list** on the Features tab: add, change or remove anyone's birthday, with a member search.
- **Can Pingwin reach everything?** on the Setup tab: checks every channel in use, the role order, and how
  many channels Pingwin can see, with how to fix each problem.

## 0.7.1 (2026-10-19)
- **Time zones per member:** `/timezone set | show | remove`. Times typed into `/gameday create` mean the
  creator's own time, or add a zone (`sat 7pm CT`, `8pm MT`); Pingwin replies with how it read the time.
- Birthday shout-outs go out at the chosen hour in each member's own time zone.

## 0.7.0 (2026-10-19)
Seven optional features, all off by default. Set them up on the dashboard's new **Features** tab, or see
[docs/FEATURES.md](docs/FEATURES.md).
- **Event reminders:** pings roles whose keywords match a new Discord Event, reminds before it starts, and
  posts when it starts.
- **Birthdays:** `/birthday set | remove | upcoming` (and `set_for` / `remove_for` for mods), a daily
  shout-out at the hour you choose, and an optional role for the day.
- **Movie night:** a watchlist per series, `/movienight nominate | list | poll | endpoll | watched | remove`,
  native Discord polls that ping the series' role and announce the winner. Admins choose who can nominate
  and start polls, how options are picked (hand-picked, oldest or random), poll length, single or multiple
  votes, and tie handling.
- **Game day sign-ups:** `/gameday create | cancel`, with I'm in / Maybe / Can't buttons and reminders.
- **Stream alerts:** Twitch and YouTube, one post per stream.
- **Welcome and approval:** a welcome message plus Approve / Deny buttons for mods; Approve gives the member role.
- **Mod log:** joins, leaves, kicks, bans, role changes, and edited or deleted messages.
- Pingwin explains which privileged intent to turn on if a feature needs one, and the dashboard says when
  a restart is needed.
- Feature data (birthdays, watchlists, sign-ups) is kept in the shared settings folder under `state/`, so
  backup PCs pick up where the main PC left off.

## 0.6.0 (2026-10-19)
- **Uninstaller** (`Uninstall-Pingwin.cmd`): stops Pingwin, removes startup items, takes the PC off the list,
  stops syncing before offering to delete settings (so deletes can't spread), optionally removes SyncThing,
  Python or Git if the installer added them, and deletes the Pingwin folder.
- The backup installer records what it changed (`install-record.json`) so the uninstaller undoes exactly that.
- Dashboard: **Stop sharing** with a backup PC, which also takes it off the PC list.
- `python -m pingwin.pairing leave | unsync | shared | forget`.

## 0.5.0 (2026-10-19)
- **Backup PC installer** (`scripts/install-backup.ps1`, `Install-Pingwin-Backup.cmd`): checks what's already
  installed, asks before installing Python, Git or SyncThing with winget, downloads Pingwin, connects
  SyncThing to the main PC, sets up startup, and shows a reply code.
- **Add a backup PC** on the dashboard: create an invite message, then paste the reply code to share the
  settings folder and add the PC to the list. Also available as `python -m pingwin.pairing`.
- A backup PC now waits for its shared settings to arrive instead of stopping.

## 0.4.1 (2026-10-19)
- Backup PC guide: a full walkthrough of the PCs & failover tab, with a screenshot and the channel permissions it needs.
- Pingwin won't start twice on the same PC; the second window says so and stops.
- Slash commands are only re-registered when they change, so quick restarts don't hit Discord's
  rate limits (which made startup look stuck for up to a minute).

## 0.4.0 (2026-10-19)
- Backup PC guide (`docs/BACKUP-PC.md`): both sides of the setup, testing, removing a PC, troubleshooting.
- Privacy notice (`PRIVACY.md`), linked from the README, the setup guide and the dashboard.
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
