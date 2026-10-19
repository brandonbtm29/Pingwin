# Pingwin's features

Besides the ping menu, Pingwin has seven optional features. They're all **off** until you turn them on.

The easiest way to set them up is the dashboard's **Features** tab (`scripts\open_dashboard.cmd`). Each
feature is a section you can open, switch on, and fill in. Changes save to the shared `config.toml`, so
every PC gets them. You can also edit `config.toml` by hand: each feature is its own `[section]`.

| Feature | What it does | Needs |
|---|---|---|
| [Event reminders](#event-reminders) | Pings the right roles when a Discord Event is created, and before it starts | Nothing extra |
| [Birthdays](#birthdays) | Members save their birthday; Pingwin posts a shout-out on the day | Nothing extra |
| [Movie night](#movie-night) | A watchlist per series, turned into a Discord poll that announces the winner | Nothing extra |
| [Game day sign-ups](#game-day-sign-ups) | A "Who's in?" card with buttons, plus a reminder to everyone who's in | Nothing extra |
| [Stream alerts](#stream-alerts) | Posts once when a streamer goes live on Twitch or YouTube | Free Twitch and/or YouTube keys |
| [Welcome and approval](#welcome-and-approval) | Welcomes new members; mods approve them with a button | **Server Members** intent |
| [Mod log](#mod-log) | Records joins, leaves, role changes, edits and deletes in a private channel | **Server Members** intent (+ **Message Content** for edits and deletes) |

Before you start:

- **Restart Pingwin once** after updating to 0.7.0 so the new slash commands show up (`/pingwin restart`).
- **Pingwin has to be able to see and post in** every channel you pick. If a channel is private, add
  Pingwin (or its role) to it with *View Channel*, *Send Messages* and *Embed Links*.
- **Time zone:** under *General*, set your time zone (like `America/New_York`). Birthdays and game day
  times use it.
- "Mods" below means anyone with *Manage Roles*, *Manage Server* or *Administrator*.

## Event reminders

Create an event in Discord as usual (the **Events** button at the top of the channel list). Pingwin reads
its name and description and pings every role whose keywords appear in it.

1. Turn on **Event reminders** and pick the channel to post in (for example `#events`).
2. Under **Which roles to ping**, add a row per role with its keywords, for example:

   | Role | Keywords |
   |---|---|
   | Movie Night | movie, film, cinema |
   | Gamer | game, gaming, lan |

   Keywords match the start of a word, so `game` also matches "Games" and "gaming".
3. Choose whether to announce new events, how many minutes before to remind (0 = don't), whether to post
   when it starts, and whether to also ping people who clicked *Interested*.

Each event is announced, reminded and started only once, even when several PCs are running Pingwin.

## Birthdays

| Command | Who | What |
|---|---|---|
| `/birthday set month day` | Everyone | Save your birthday (month and day, never the year) |
| `/birthday remove` | Everyone | Remove yours |
| `/birthday upcoming` | Everyone | The next birthdays |
| `/birthday set_for member month day` | Mods | Set someone else's |
| `/birthday remove_for member` | Mods | Remove someone else's |

On the dashboard, pick the channel, the hour to post (in your time zone) and the message (`{mentions}`
becomes the birthday people). Optionally ping a role, or give a role for the day (Pingwin takes it back
the next day). Feb 29 birthdays are celebrated on Feb 28 in other years.

## Movie night

Each **series** (for example *MMM* and *FFF*) has its own watchlist, role and channel. Members nominate
movies, then a mod (or whoever you allow) turns the watchlist into a native Discord poll. When the poll
ends, Pingwin replies with the winner.

| Command | What |
|---|---|
| `/movienight nominate series title` | Add a movie to the watchlist |
| `/movienight list series` | Show the watchlist |
| `/movienight remove series movie` | Take one off (your own, or any for mods) |
| `/movienight watched series movie` | Mark watched, or not (mods) |
| `/movienight poll series [hours] [question]` | Start a poll and ping the series' role |
| `/movienight endpoll series` | End the running poll now |

What you control (all on the dashboard):

- **Who can nominate:** everyone, members with a certain role, or mods only. Plus a limit per person per
  series (0 = no limit).
- **Who can start polls:** mods only, a role, or everyone.
- **Poll options come from:** whoever starts the poll ticks the movies from a list, or the oldest
  nominations, or random ones. Plus how many movies go in a poll (2 to 10).
- **Poll length, single or multiple votes,** whether the winner is marked watched, and **what happens on a
  tie** (announce it so a mod decides, or pick one at random).

The **watchlist** panel on the Features tab lets you mark movies watched, unwatch them, or delete them.

## Game day sign-ups

`/gameday create title when [note]` posts a card with **I'm in**, **Maybe** and **Can't** buttons and
pings the game role. *when* can be things like `fri 8pm`, `tomorrow 7:30pm`, `10/31 9pm` or `in 2 hours`
(a bare `8` means 8 pm). Pingwin reminds everyone who's in before it starts (set the minutes on the
dashboard) and pings them when it starts. `/gameday cancel` cancels it (the host or a mod).

## Stream alerts

Pingwin checks every few minutes and posts once per stream, with a link.

1. Turn on **Stream alerts**, pick the channel and the role to ping (for example *Stream Squad*).
2. Add each streamer: a name, their Twitch login and/or their YouTube handle (like `@name`).
3. Add the keys for whichever sites you use. They're free and stored in `secrets.env`, never in `config.toml`.

**Twitch keys**

1. Go to [dev.twitch.tv/console](https://dev.twitch.tv/console) and sign in (turn on two-factor
   authentication if Twitch asks).
2. **Register Your Application**: any name, OAuth Redirect URL `http://localhost`, Category *Other*,
   Client Type *Confidential*.
3. Open it, copy the **Client ID**, click **New Secret** and copy the **Client Secret**. Paste both into
   the dashboard.

**YouTube key**

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project (any name).
2. **APIs & Services → Library**, search for **YouTube Data API v3**, and click **Enable**.
3. **APIs & Services → Credentials → Create credentials → API key**. Copy it into the dashboard.
   Optional: *Restrict key* to the YouTube Data API v3.

The free YouTube quota is plenty for a few streamers at the default check interval.

## Welcome and approval

When someone joins, Pingwin welcomes them and posts a card for mods with **Approve** and **Deny** buttons.
Approve gives them the member role (Friend by default), so they can see the server and use the ping menu.
Deny just marks the card, or kicks them if you choose that.

This needs the **Server Members** intent (see below). Pingwin also needs *Manage Roles* (it has it), and its
own role must be **above** the role it gives. To kick on Deny, give Pingwin *Kick Members* too.

## Mod log

Posts server activity to a private channel: joins, leaves, kicks and bans, role changes, and edited or
deleted messages (with what they said). Ping-menu roles are skipped by default because they change often.

This needs the **Server Members** intent, and the **Message Content** intent for edits and deletes. Make
the log channel mod-only: it shows message text from across the server.

## Turning on intents

Discord makes bots ask for two kinds of extra access in the Developer Portal. Only turn on what you use.

1. Open the [Developer Portal](https://discord.com/developers/applications) → your app → **Bot**.
2. Under **Privileged Gateway Intents**, turn on:
   - **Server Members Intent** for *Welcome and approval* or *Mod log*
   - **Message Content Intent** for the mod log's edited and deleted messages
3. Click **Save Changes**, then restart Pingwin (the dashboard shows a banner when a restart is needed).

If you turn a feature on before the intent, Pingwin stops at startup and says which intent to switch on.
Bots in fewer than 100 servers don't need Discord's approval for these.

## Several PCs

Features follow failover: only the active PC posts, reminds or answers buttons, and what each feature keeps
(birthdays, watchlists, sign-ups, what's been announced) lives in the shared settings folder under
`state\`, so a backup PC picks up where the main one left off.
