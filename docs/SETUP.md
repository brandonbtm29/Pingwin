# Setting up Pingwin

This guide takes you from nothing to a working Pingwin in about 20 minutes. You don't need to know
how to code. You'll create the bot in Discord, install it on a Windows PC that stays on, and finish
the setup in Pingwin's dashboard.

> **Mac or Linux?** Pingwin runs there too, but the helper scripts are for Windows. See
> [Running without the scripts](#running-without-the-scripts).

**Contents**
1. [What you need](#1-what-you-need)
2. [Create the bot in Discord](#2-create-the-bot-in-discord)
3. [Install Pingwin](#3-install-pingwin)
4. [Start it](#4-start-it)
5. [Finish in the dashboard](#5-finish-in-the-dashboard)
6. [Set up your server](#6-set-up-your-server)
7. [Optional extras](#7-optional-extras)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. What you need

- **A Windows PC that's usually on.** Pingwin only works while it's running somewhere. It uses very little
  power or internet.
- **Python 3.11 or newer.** Get it from [python.org](https://www.python.org/downloads/). In the
  installer, tick **Add python.exe to PATH**.
- **Git** (recommended) from [git-scm.com](https://git-scm.com/download/win). The default options are fine.
  With Git, Pingwin can update itself.
- **"Manage Server" permission** on the Discord server you're adding it to.

## 2. Create the bot in Discord

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and sign in.
2. Click **New Application**, name it (for example *Pingwin*), and accept the terms.
3. Optional: on **General Information**, add an app icon (there's one in `assets/`) and a description.
   Members see these. For **Privacy Policy URL** you can use
   `https://github.com/brandonbtm29/Pingwin/blob/main/PRIVACY.md`, which describes what Pingwin stores.
   Leave **Interactions Endpoint URL** blank: filling it in stops Pingwin's buttons and commands from working.
4. Open the **Bot** tab:
   - Click **Reset Token**, then **Copy**. Keep it somewhere safe for step 3.
     **Treat the token like a password:** anyone who has it controls your bot.
   - Leave all three **Privileged Gateway Intents** turned **off** for now. The ping menu doesn't need them.
     (Welcome and approval and the mod log do; see [Features](FEATURES.md#turning-on-intents).)
5. Open **General Information** again and copy the **Application ID**.
6. Invite the bot to your server. Paste this link into your browser, replacing `APP_ID` with the
   Application ID:
   ```
   https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot+applications.commands&permissions=335629312
   ```
   Pick your server and click **Authorize**. The link asks only for what Pingwin uses:
   Manage Roles, Change Nickname, View Channels, Send Messages, Embed Links and Read Message History.

**Your server ID:** you'll be asked for it in the next step. In Discord, go to **User Settings → Advanced**
and turn on **Developer Mode**. Then right-click your server's icon and choose **Copy Server ID**.

## 3. Install Pingwin

### Get the files

**With Git (recommended).** Open a Command Prompt in the folder you want (e.g. `Documents`) and run:
```
git clone https://github.com/brandonbtm29/Pingwin.git
```
**Without Git.** On the [GitHub page](https://github.com/brandonbtm29/Pingwin), click **Code → Download ZIP**,
and unzip it. This works, but Pingwin won't update itself.

### Run the setup script

Open the `Pingwin` folder and double-click **`scripts\setup.cmd`**. It installs what Pingwin needs
(a minute or two the first time), then asks you four things:

| Question | What to type |
|---|---|
| **Settings folder** | Press **Enter** to keep settings in the Pingwin folder. If you'll run Pingwin on more than one PC, use a folder you sync between them (e.g. a SyncThing folder). |
| **PC name** | Press **Enter** to use this computer's name. |
| **Bot token** | Paste the token from step 2 (right-click pastes). |
| **Server ID** | Paste your server ID. |

It creates three files you can edit later in any text editor:

- `config.toml` holds Pingwin's settings. The dashboard edits this for you.
- `secrets.env` holds the token. Never share it or post it.
- `.env` holds this PC's name and where the settings folder is.

## 4. Start it

Double-click **`scripts\run_pingwin.cmd`**. A window opens and, after a few seconds, shows lines like:

```
Logged in as Pingwin#1234 in 1 server(s)
Dashboard ready at http://127.0.0.1:8787
```

Leave this window open: closing it stops Pingwin. If it crashes, the window restarts it after 15 seconds.

In Discord, the bot now shows as online, and typing `/pingwin about` should get a reply. If you skipped
the server ID, slash commands can take up to an hour to appear.

## 5. Finish in the dashboard

Double-click **`scripts\open_dashboard.cmd`**. Your browser opens Pingwin's dashboard, already signed in.
It only works on the PC that runs Pingwin.

The **Setup** tab shows a checklist of what's done and what's left, with a **Go** button for each step.
Most of the remaining work is on the **Role menu** tab:

1. **Add your roles.** Each row is one button in the menu: an emoji, a role name, and a short note about
   what the pings are for. Up to 25.
2. **Choose who can use the menu.** *Everyone in the server*, or only members with a certain role.
3. Click **Save**.
4. Click **Create missing roles.** Pingwin creates each role with no permissions and turns on
   "Allow anyone to @mention this role", so the pings work.
5. Pick your `#roles` channel and click **Post a new menu**.

If you later change the roles or wording, click **Update posted menus** and the existing message updates in place.

The labels next to each role tell you if something needs fixing. Hover over them for details:

| Label | Meaning |
|---|---|
| **ready** | All good. |
| **will be created** | The role doesn't exist yet. Click *Create missing roles*. |
| **not pingable** | Turn on *Allow anyone to @mention this role* for it in Server Settings → Roles. |
| **above Pingwin** | Drag Pingwin's role above it in Server Settings → Roles (see step 6). |
| **has permissions** | Ping roles normally shouldn't grant anything; check it's the role you meant. |

## 6. Set up your server

A few things only you can do in Discord's own settings:

1. **Role order.** In **Server Settings → Roles**, drag the **Pingwin** role *above* every role in the
   menu. Discord only lets a bot hand out roles below its own. It can stay below your mod roles.
2. **The `#roles` channel.** Let people see it but not chat:
   - `@everyone`: allow *View Channel*, deny *Send Messages*.
   - If you use a gate role like **Friend**, instead deny *View Channel* for `@everyone` and allow it for **Friend**.
3. **Tell people.** For example: *"Head to #roles and tap the pings you want. Tap again to stop them."*
4. **Ping the role instead of @everyone.** For movie night, type `@Movie Night Maniac` and only the
   people who opted in get notified.

## 7. Optional extras

### Start Pingwin with Windows
Double-click **`scripts\install_autostart.cmd`**. Pingwin then starts in the background, with no window,
whenever you sign in, and you get a **Pingwin Dashboard** shortcut on your desktop that opens the dashboard
(starting Pingwin first if it isn't running). Its log is on the dashboard's **Logs** tab.

- **Stop it:** dashboard → **Status → Stop Pingwin**, or `scripts\stop_pingwin.cmd`.
- **Just the shortcut:** `scripts\create_shortcuts.cmd`.
- **Undo:** `scripts\remove_autostart.cmd`.

If Pingwin can't start because of a settings problem, a window opens to show what's wrong.

To keep Pingwin running, also stop the PC from sleeping: **Settings → System → Power** → set *Sleep* to *Never*.

### Updates
If you installed with Git, Pingwin checks GitHub every 30 minutes and updates itself. It restarts for
about 20 seconds when it does. To update right away, click **Restart Pingwin** on the dashboard, or run
`/pingwin restart` in Discord. You can see what changed in [CHANGELOG.md](../CHANGELOG.md).

### A backup PC
A second PC can take over if yours goes offline. It works best at a friend's house, on different internet.
See **[Adding a backup PC](BACKUP-PC.md)** for both sides of the setup and how to test it.

### A different personality
Pingwin's messages come from `personas\pingwin.toml`. To change them, copy that file into a `personas`
folder inside your settings folder (create it if needed), rename it (e.g. `pirate.toml`), and rewrite the lines. Then set
`persona = "pirate"` in `config.toml`. Try the bundled `deadpan` persona to see the idea.

### Running without the scripts
On any OS with Python 3.11+:
```
python -m venv .venv
.venv/bin/pip install -e .          # Windows: .venv\Scripts\pip
cp config.example.toml config.toml  # then edit it
cp secrets.env.example secrets.env  # then add your token
.venv/bin/python -m pingwin
```
The dashboard link is written to `.dashboard-url` in the Pingwin folder when it starts.

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| The window says **DISCORD_TOKEN is not set** | Put `DISCORD_TOKEN=your-token` in `secrets.env` (the path is in the message). |
| **Discord rejected the token** | Reset the token in the Developer Portal (Bot tab) and paste the new one into `secrets.env`. |
| **Slash commands don't show up** | Set your server ID (`guild_id` in `config.toml`), then restart Pingwin. Without it, Discord can take up to an hour. |
| Pingwin says **"My flippers can't reach…"** | Its role is below that role. Fix the role order (step 6). |
| Members get **"You'll need the Friend role first"** | They don't have the gate role yet, or change *Who can use it* on the Role menu tab. |
| The **dashboard won't open** | Pingwin has to be running. If another program uses port 8787, set a different `port` under `[dashboard]` in `config.toml`. |
| **Buttons do nothing** ("This interaction failed") | Pingwin is offline, or every PC is on standby. Check the window and the Status tab. |
| **Two PCs both answer** | Each PC needs a different name in `.env` (`PINGWIN_NODE`), matching the list on the PCs & failover tab. |
| **It isn't updating** | Updates need a Git install with no local code edits. Check the Logs tab for "skipping auto-update". |
| Something else | Check the **Logs** tab or `logs\pingwin.log`, then [open an issue](https://github.com/brandonbtm29/Pingwin/issues). Please leave your token out of anything you post. |

## Uninstalling

Double-click **`Uninstall-Pingwin.cmd`** in the Pingwin folder. It shows what it will do and asks first, then:

1. Stops Pingwin and removes it from Windows startup.
2. If the settings are shared with other PCs: takes this PC off the PC list, and stops syncing the
   settings folder here. Your other PCs keep their settings and keep running.
3. Asks whether to delete this PC's copy of the settings. It only offers this once syncing has stopped,
   so the delete can't spread to your other PCs.
4. If the backup installer set up SyncThing and nothing else uses it, asks whether to remove SyncThing too.
   It also asks about Python or Git, if the installer installed them. The default is to keep them.
5. Deletes the Pingwin folder.

Afterwards:
- **On the main PC**, if this was a backup: dashboard → **PCs & failover → Add a backup PC → Stop sharing**
  next to that PC.
- **If a PC won't be coming back:** reset the bot token in the Developer Portal and put the new one in
  `secrets.env`, since that PC had a copy.
- **To remove the bot from Discord entirely:** kick it from your server, or delete the application in the
  Developer Portal.
