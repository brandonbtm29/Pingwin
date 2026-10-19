# Adding a backup PC

A backup PC keeps Pingwin running when the main PC is off or its internet is down. Every PC runs
Pingwin, but only one answers at a time. If the answering PC stops checking in for 2 minutes, the next
PC in the list takes over. When the main PC comes back, it takes the job back.

A backup helps most when it's in **a different place on a different internet connection**, like a
friend's house.

> **Trust first.** A backup PC holds your bot's token, and installs whatever is pushed to the GitHub
> repo. Only use PCs belonging to people you trust.

You need two things on every PC:
- **The code**: from GitHub, so it updates itself.
- **The shared settings folder**: synced between PCs (this guide uses [SyncThing](https://syncthing.net)).
  It holds `config.toml`, `secrets.env` (the token) and `menus.json`.

---

## Part 1: On the main PC (once)

1. **Create a status channel.** In Discord, make a channel such as `#pingwin-status`. Hide it from
   everyone except mods, and let the Pingwin role view and send messages there. Pingwin posts a
   check-in message there that the PCs use to agree on who's answering.
2. **Turn on failover.** Open the dashboard (`scripts\open_dashboard.cmd`) and go to **PCs & failover**:
   - Tick **Let backup PCs take over**.
   - Pick the status channel.
   - Make sure the PCs are listed in order of preference. Main PC first; add the backup's name
     (e.g. `BACKUP-PC`).
   - Click **Save**.
3. **Share the settings folder.** In SyncThing, add your Pingwin settings folder (e.g.
   `C:\Users\you\SyncThing\Pingwin`) as a folder. Add the backup PC as a device using its **Device ID**,
   then share only that folder with it.

## Part 2: On the backup PC

1. **Install the tools:**
   - [Python 3.11+](https://www.python.org/downloads/). In the installer, tick **Add python.exe to PATH**.
   - [Git](https://git-scm.com/download/win). The default options are fine.
   - [SyncThing](https://syncthing.net/downloads/) (for Windows, *SyncTrayzor* or the official installer).
2. **Accept the shared folder.** In SyncThing, send your **Device ID** to the main PC's owner
   (*Actions → Show ID*). Accept the device and the *Pingwin* folder when they appear. Save it somewhere
   like `C:\Users\you\SyncThing\Pingwin`, and wait until it shows **Up to Date**.
3. **Get the code.** Open a Command Prompt where you want it (e.g. `Documents`) and run:
   ```
   git clone https://github.com/brandonbtm29/Pingwin.git
   ```
4. **Run `scripts\setup.cmd`** in the new `Pingwin` folder and answer:
   - **Settings folder:** the synced folder from step 2 (e.g. `C:\Users\you\SyncThing\Pingwin`)
   - **PC name:** exactly the name in the list on the PCs & failover tab (e.g. `BACKUP-PC`)

   It won't ask for the token, because it's already in the synced folder.
5. **Start it with Windows.** Run `scripts\install_autostart.cmd`. Then start it now with
   `scripts\run_pingwin.cmd`. The window should say `STANDBY on BACKUP-PC: BTM-NOVA is active`.
6. **Keep the PC awake.** **Settings → System → Power** → set *Sleep* to *Never* while plugged in.

## Part 3: Test it

1. On the main PC, close the Pingwin window.
2. Wait about 2 minutes. The backup's window should say `ACTIVE on BACKUP-PC: took over…`, and the
   status message in `#pingwin-status` should say it's answering. Try a button in `#roles`.
3. Start Pingwin on the main PC again. Within about 30 seconds it takes the job back, and the backup
   goes back to standby.

## Good to know

- **Settings sync on their own.** Change things on any PC's dashboard. SyncThing copies the file and every
  Pingwin reloads it within a minute. Edit on one PC at a time to avoid SyncThing conflict copies.
- **Each PC has its own dashboard and log.** The Status tab shows which PC is answering.
- **Updates reach every PC** automatically from GitHub.
- **More PCs:** add more names to the end of the list. A PC that isn't listed still helps, but only when
  every listed PC is offline.
- **Removing a backup:** take it off the list, stop sharing the folder with it, and run
  `scripts\remove_autostart.cmd` on it. If the PC isn't coming back, **reset the bot token** in the
  Developer Portal and put the new one in `secrets.env`.

## Troubleshooting

| Problem | Fix |
|---|---|
| Both PCs answer (double replies) | The names don't match. Each PC's `PINGWIN_NODE` (in the Pingwin folder's `.env`) must be unique and match the list. |
| The backup never takes over | Check failover is on, a status channel is set, and Pingwin can post there. Also check the backup window says STANDBY, not an error. |
| The backup says the token is missing | SyncThing hasn't finished, or `.env` points at the wrong folder (`PINGWIN_DATA`). |
| Settings changes don't reach the backup | Check the SyncThing folder shows *Up to Date* on both PCs. |
