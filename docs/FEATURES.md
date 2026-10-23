# Pingwin's features

Besides the ping menu, Pingwin has eight optional features. They're all **off** until you turn them on.

The easiest way to set them up is the dashboard's **Features** tab (`scripts\open_dashboard.cmd`). Each
feature is a section you can open, switch on, and fill in. Changes save to the shared `config.toml`, so
every PC gets them. You can also edit `config.toml` by hand: each feature is its own `[section]`.

| Feature | What it does | Needs |
|---|---|---|
| [Event reminders](#event-reminders) | Pings the right roles when a Discord Event is created, and before it starts | Nothing extra |
| [Birthdays](#birthdays) | Members save their birthday; Pingwin posts a shout-out on the day | Nothing extra |
| [Movie night](#movie-night) | Watchlists, polls, a Rate it button after each movie, and movie pages with your history | Optional free OMDb key |
| [Game day sign-ups](#game-day-sign-ups) | A "Who's in?" card with buttons, plus a reminder to everyone who's in | Nothing extra |
| [Event attendance](#event-attendance-and-frumples) | Takes attendance at Discord Events and adds a reward (like Frumples) in UnbelievaBoat | A free UnbelievaBoat API token |
| [Stream alerts](#stream-alerts) | Posts once when a streamer goes live on Twitch or YouTube | Free Twitch and/or YouTube keys |
| [Welcome and approval](#welcome-and-approval) | Welcomes new members; mods approve them with a button | **Server Members** intent |
| [Mod log](#mod-log) | Records joins, leaves, role changes, edits and deletes in a private channel | **Server Members** intent (+ **Message Content** for edits and deletes) |

Before you start:

- **Restart Pingwin once** after updating to 0.7.0 so the new slash commands show up (`/pingwin restart`).
- **Pingwin has to be able to see and post in** every channel you pick. If a channel is private, add
  Pingwin (or its role) to it with *View Channel*, *Send Messages* and *Embed Links*.
- **Time zones:** under *General*, set the server's time zone (like `America/New_York`). Members in other
  zones can pick their own with `/pingwin timezone set`. See [Time zones](#time-zones).
- "Mods" below means anyone with *Manage Roles*, *Manage Server* or *Administrator*.

## Time zones

Every time Pingwin posts (game days, event reminders, poll deadlines) is a Discord timestamp, so each person
sees it in **their own** time automatically. Time zones only matter when someone *types* a time, and for
the hour birthday shout-outs go out.

| Command | What |
|---|---|
| `/pingwin timezone set zone` | Pick yours (Eastern, Central, Mountain, Pacific, ... or any zone like `America/Boise`) |
| `/pingwin timezone show` | See which one Pingwin uses for you |
| `/pingwin timezone remove` | Go back to the server's |

**Time zone menu.** On the dashboard's **Role menu** tab, click **+ Time zone menu**. It makes a second
button menu (Eastern, Central, Mountain, Pacific; change them as you like) where picking a role also sets
that person's time zone, and picking another swaps it. `/pingwin timezone set` gives the matching role too, so the
two always agree. Click **Create missing roles**, then post it with **Post it**.

- `/pingwin gameday create when:fri 8pm` means 8 pm in the creator's zone. Anyone can also add a zone to the
  time: `sat 7pm CT`, `8:30pm MT`, `tomorrow 6pm eastern`. Pingwin replies with how it read the time.
- Birthday shout-outs go out at the chosen hour in the birthday person's own zone.
- Members who haven't picked one use the server's time zone from the Features tab.

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

## Event attendance and Frumples

Pingwin takes attendance at your Discord Events and pays the reward into **UnbelievaBoat**, so balances
stay where everyone already checks them.

- **Voice events** (location: a voice channel): anyone in the event's voice channel while it runs is
  noted. Staying 30 minutes (adjustable) counts; events shorter than that need half their length.
- **In-person events** (location: Somewhere Else): when the event starts, Pingwin posts an **I'm here**
  button. Optionally it needs a 4-letter code that the host announces; the host gets it with
  `/pingwin attendance code`.
- **When it ends** (the host ends it, its end time passes, or everyone has left the voice channel for 15
  minutes), Pingwin adds the reward in UnbelievaBoat (plus an optional host bonus) and posts a recap.

| Command | Who | What |
|---|---|---|
| `/pingwin attendance add member [event]` | Mods | Count someone (pays them if the event already ended) |
| `/pingwin attendance remove member [event]` | Mods | Take someone off (takes the reward back if it was paid) |
| `/pingwin attendance code` | Host or mods | The check-in code for an event happening now |
| `/pingwin attendance history [member]` | Everyone | Events someone came to recently |

The **Event attendance** panel on the Features tab shows recent events and who came, with buttons to add
or remove people and to retry payments that didn't go through.

**UnbelievaBoat token**

1. Go to [unbelievaboat.com/applications](https://unbelievaboat.com/applications) and sign in with Discord.
2. **Create Application** (any name, like Pingwin), then copy its **token** into the Features tab.
3. Use the application's **Authorize** link to add it to your server, with the **Economy** permission.

Pingwin only adds to (or takes back from) balances for attendance; it doesn't read or change anything else.

## Birthdays

| Command | Who | What |
|---|---|---|
| `/pingwin birthday set month day` | Everyone | Save your birthday (month and day, never the year) |
| `/pingwin birthday remove` | Everyone | Remove yours |
| `/pingwin birthday upcoming` | Everyone | The next birthdays |
| `/pingwin birthday set_for member month day` | Mods | Set someone else's |
| `/pingwin birthday remove_for member` | Mods | Remove someone else's |

You can also add, change or remove anyone's birthday in the **Birthday list** on the Features tab: type
the start of their name, pick them, choose the date, and click **Save birthday**.

On the dashboard, pick the channel, the hour to post (in each member's own time zone) and the message (`{mentions}`
becomes the birthday people). Optionally ping a role, or give a role for the day (Pingwin takes it back
the next day). Feb 29 birthdays are celebrated on Feb 28 in other years.

## Movie night

Each **series** (for example *MMM* and *FFF*) has its own watchlist, role and channel. Members nominate
movies, then a mod (or whoever you allow) turns the watchlist into a native Discord poll. When the poll
ends, Pingwin replies with the winner.

**Showtimes:** give a series a weekly showtime, like `Mon 8:30pm` (in the time zone under *General*). Then
`/pingwin movie poll` closes the poll exactly when the movie starts, and the poll message shows the closing time
in everyone's own time zone. (Discord polls can only run whole hours, so Pingwin ends it early at showtime.)
Passing `hours` still overrides it.

| Command | What |
|---|---|
| `/pingwin movie nominate series title [year]` | Add a movie to the watchlist (goes to the mods first, if approvals are on) |
| `/pingwin movie list series` | Show the watchlist |
| `/pingwin movie remove series movie` | Take one off |
| `/pingwin movie watched series movie` | Mark watched, or not |
| `/pingwin movie tonight series title [year]` | Set tonight's movie on a night without a poll |
| `/pingwin movie history [search]` | What you've watched and how you rated it |
| `/pingwin movie rate` | Rate the latest movie (if you missed the button) |
| `/pingwin movie page` | Link to the movie pages |
| `/pingwin movie poll series [hours] [question]` | Start a poll and ping the series' role |
| `/pingwin movie endpoll series` | End the running poll now |

What you control (all on the dashboard):

- **Who can nominate:** everyone, members with a certain role, or mods only. Plus a limit per person per
  series (0 = no limit).
- **Who can start polls:** mods only, a role, or everyone.
- **Poll options come from:** whoever starts the poll ticks the movies from a list, or the oldest
  nominations, or random ones. Plus how many movies go in a poll (2 to 10).
- **Approvals, Rate it, and how long ratings stay open.**
- **Poll length, single or multiple votes,** whether the winner is marked watched, and **what happens on a
  tie** (announce it so a mod decides, or pick one at random).

The **watchlist** panel on the Features tab lets you mark movies watched, unwatch them, or delete them.

### Approvals

With **Members' changes need a mod's OK** on (the default), anything a member does that changes the lists (a
nomination, removing a movie, marking one watched, a fix to the history) becomes a card in the mod
channel (*Send them to*, or the approval channel under Welcome and approval). A mod clicks **Approve** or **Deny**,
Pingwin applies it and tells the member. Mods' own changes apply straight away. Pending cards also show on
the movie pages, where mods can approve them too.

### Rate it

When a movie night ends (the poll winner, or `/pingwin movie tonight`), Pingwin logs it in the history.
About when the movie finishes (showtime + its runtime), it posts a **Rate it** button in the series'
channel. Each person gives a score out of 10 and a comment, and can answer the question the previous person
asked and leave one for the next (like "How many jump scares out of 10?"). The message keeps a live
average next to IMDb's, and closes after *Ratings stay open for* hours (48 by default).

### Movie details (OMDb)

Pingwin can fill in runtime, director, writers, stars, poster and IMDb rating from
[OMDb](https://www.omdbapi.com/apikey.aspx) (the free key allows 1,000 lookups a day). Paste the key under
**Features → Movie night**. Each movie is looked up **once** and saved, so nothing is fetched again, and you
don't need a spreadsheet formula like `GETMOVIEINFO`. Without a key everything still works; you just won't
get the extra details.

With a key, Pingwin **fills in missing details by itself**: every 20 minutes it looks up a few movies (the
history first, newest first, then the watchlists), staying under *OMDb requests per day* (800 by default;
one movie takes 1 to 3 requests). If OMDb says the daily limit is reached it waits until the next day, and
movies it can't find are tried again a week later (three tries). A result from a different year is treated
as a miss, so a remake or a namesake never replaces your movie. The movie pages' **Admin** tab shows the
progress, and **Look up missing details** does a bigger batch right away.

### Movie pages

`/pingwin movie page` links to **/movies** on the dashboard's web address (see
[remote access](REMOTE-DASHBOARD.md)). Anyone with the member role (**Friend**) signs in with Discord and
sees:

- **History:** every movie night with date, series, our average and IMDb's, searchable and sortable. Open
  one for the poster, details, everyone's ratings and the question chain. **Suggest a fix** sends a
  correction to the mods.
- **Reviews:** everyone's ratings and comments in one place. Filter by person, series or score, search
  them, or click someone's card for their average, how they compare with the group, and their favorites.
- **Watchlists:** each series' list as poster cards, with filters for named lists (like *Horror*), and a
  form to suggest a movie.
- **Stats:** charts of movie nights per year (by series), favorites and least favorites, where you
  disagreed with IMDb, how everyone scores, the most active critics, genres and directors. Hover or tab to
  any bar for the details.

On **History**, **Columns** picks what the table shows (year, rating count, us vs IMDb, director, writers,
stars, genre), and clicking a column heading sorts by it. Your choice is remembered in your browser.

Members only see the movie pages; the rest of the dashboard stays admin-only.

### Importing your old history

On the movie pages, the **Admin** tab imports from a spreadsheet log (columns like Date, Movie, Year,
Duration, Session, IMDb rating, our rating, Director, Writer, Stars) and a ratings document with sections
like:

```
The Fly
Date watched: 1/5/2026
Sam (8/10): Gross in the best way.
To Alex: How many times did you look away out of 10?
Alex (7/10): 3/10. Loved the ending.
```

Paste Google Sheets and Docs links (shared as *Anyone with the link can view*), or download them (File →
Download → CSV / Plain text) and upload the files. Names are matched to server members where possible.
Running it again only adds what's new. Then **Look up missing details** fills in OMDb info.

### People (who's who in the ratings)

Imported ratings keep the name they were written with, so one person can show up as a nickname, a typo
and their Discord name. On the **Admin** tab, **People** lists every name. Pick the **Discord member** each
name belongs to (likely matches are listed first), and those ratings join that member's own Discord ratings
under their current server name, on the Reviews tab, in the stats and in Discord recaps. For someone who
isn't in the server, type a name instead (like `Mrs. Smith` and `Mrs Smith` → `Sam`). The original names
stay as written; set it back to *not linked* and clear the name to undo.

### Importing a watchlist from Discord

If your club keeps a list in a pinned message (✅ for watched, 🇽 or ❌ for not yet), the **Admin** tab's
**Import a watchlist from Discord** adds it in one go: right-click the message → **Copy Message Link**, paste
it, pick the series and an optional list name (like *Horror*). Watched lines, movies already in the history
and ones already on the list are skipped. You can paste the list's text instead of a link.

### Typed commands

Several bots share most servers, so every Pingwin command starts with `/pingwin`. Pingwin can also answer
typed commands that start with `-pingwin`, like `-pingwin movie nominate MMM The Thing 1982`, `-pingwin
movie history`, `-pingwin birthday set 9 3` or `-pingwin timezone set Mountain` (`-pingwin help` lists
them). Turn on **General → Also answer typed -pingwin commands**; it needs the **Message Content** intent.

## Game day sign-ups

`/pingwin gameday create title when [note]` posts a card with **I'm in**, **Maybe** and **Can't** buttons and
pings the game role. *when* can be things like `fri 8pm`, `tomorrow 7:30pm`, `10/31 9pm` or `in 2 hours`
(a bare `8` means 8 pm). The time is in your own zone (`/pingwin timezone set`), or add one like `sat 7pm CT`. Pingwin reminds everyone who's in before it starts (set the minutes on the
dashboard) and pings them when it starts. `/pingwin gameday cancel` cancels it (the host or a mod).

## Stream alerts

Pingwin posts once per stream, with a link, when a streamer goes live:

- **In a Discord voice channel** (Go Live / screen share): instantly, no keys needed. The alert says which
  voice channel to join.
- **On Twitch or YouTube:** checked every few minutes, using free API keys.

1. Turn on **Stream alerts**, pick the channel and the role to ping (for example *Stream Squad*).
2. Add each streamer: a name, plus their **Discord username** (for Go Live alerts), Twitch login and/or
   YouTube handle (like `@name`). Fill in only what they use.
3. For Twitch or YouTube, paste the keys into the same section (**Twitch client ID**, **Twitch client secret**,
   **YouTube API key** at the bottom) and click **Save**. They're stored in `secrets.env`, never in `config.toml`.

**Finding a streamer's handles** (easiest: ask them)

- **Twitch login:** open their channel; it's the last part of the address. For `twitch.tv/coolstreamer`, enter
  `coolstreamer`.
- **YouTube handle:** open their channel; the handle starts with `@` and is under the channel name (and in the
  address, like `youtube.com/@coolstreamer`). Enter it with the `@`.
- On Discord, if they've linked Twitch or YouTube, it shows on their profile under **Connections**.

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
