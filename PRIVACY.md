# Privacy notice

*Last updated: November 11, 2026 (version 0.35.0)*

Pingwin is a self-hosted Discord bot. This notice explains what it sees, what it keeps, and where.

## Who runs Pingwin

Pingwin isn't a central service. Every copy runs on the computer of whoever installed it (the
**operator**), usually a server's admins. The Pingwin project and its author don't receive any data from
anyone's copy. Pingwin has no analytics, no tracking and no ads.

If you have questions about a particular server's Pingwin, ask that server's admins. They're the
operators of that copy.

## What Pingwin sees

With its default settings, Pingwin connects to Discord without any of Discord's "privileged intents". That means:

- **It can't read message content** in the server, except messages that mention it. It doesn't store or
  log anyone's messages (voice ratings, below, keep only the text of a voice message sent to Pingwin).
- **It doesn't download the member list** or see anyone's online status or activity.
- **It does see:**
  - the server's name, channels and roles, which it needs to set up and hand out roles
  - the username and current roles of whoever clicks one of its buttons or uses one of its commands
  - Discord Events in the server (name, description, time, who clicked *Interested*), if event reminders are on
  - who is in a voice channel, and who is streaming there, which Discord shows every bot. Pingwin only uses
    it for the features below that are on: event attendance, noticing movie nights in voice, and Go Live
    stream alerts. It never hears audio.
  - messages sent to it in a DM. If movie night's **Rate by voice message** is on, it also listens to voice
    messages sent as a reply to its Rate it posts.

Two optional features change this, and only when the operator turns them on:

- **Welcome and approval** and the **mod log** use the *Server Members* intent, so Pingwin is told when
  someone joins or leaves and when their roles change.
- The **mod log** can also use the *Message Content* intent, so it can show what a deleted (or, if the
  operator turns it on, edited) message said. It posts that to a channel the operator chooses (meant to be
  mod-only) and doesn't keep its own copy beyond Discord's usual short-lived memory of recent messages.

**Voice ratings:** when someone sends Pingwin a voice message to rate a movie, the PC that's answering
downloads it, turns it into text with speech-to-text that runs on that PC (Whisper), and throws the
recording away. Nothing is sent to a speech service. The text waits on a card until the person who spoke taps
**Save** (it then becomes their rating comment) or **Cancel**; an unsaved card is deleted after 2 days.

**Stream alerts** check public Twitch and YouTube pages of the streamers the operator lists, and Discord's
Go Live for the Discord usernames the operator lists.

## What Pingwin keeps

Everything stays on the operator's computers (and any backup PCs they set up):

| What | Why | How long |
|---|---|---|
| **Activity log**: when someone opts into or out of a role (their Discord username and the role name), which admin ran a settings command or approved a change, short notes like "saved a voice rating from (name)", and errors | Troubleshooting | The log rotates automatically: once it reaches about 6 MB, the oldest lines are deleted |
| **Menu locations**: the server, channel and message IDs of posted role menus | Updating menus in place | Until the menu is deleted |
| **Settings**: role names, channel IDs and the names of the PCs running Pingwin | Running the bot | Until the operator changes them |
| **Birthdays** (if on): your Discord user ID with the month and day you entered. Never the year | Posting the shout-out | Until you run `/pingwin birthday remove` or a mod removes it |
| **Birthdays of people without Discord** (if a mod adds them): a name, birthday (with the year, if given), an optional note and date of death, in `config.toml` | The Star birthdays message | Until a mod removes them |
| **Movie watchlists** (if on): each nominated title, who nominated it (user ID) and when, and running polls | Running movie night | Until the movie is removed |
| **Ranked-choice poll ballots** (if used): each voter's user ID and their ranking | Counting the poll | Kept with the last few finished polls of each series, then dropped |
| **Movie history and ratings** (if on): which movie was watched when, and each rating: your user ID (or a name, from an imported ratings document or entered by a mod for a guest), score, comment, any question or answer you wrote, who your question was for, and who was picked to rate first | Showing the history, averages and stats on the movie pages | Until a mod deletes the rating or the movie night |
| **Voice rating cards** (if on): the text Pingwin heard in your voice message, the score it found, your user ID and display name. Never the recording | Letting you check it before you tap Save | Until you tap Save (it becomes your rating) or Cancel, or 2 days |
| **Movie rater names** (if a mod sets them): which name each rater's ratings count as | Grouping someone's ratings on the movie pages | Until a mod clears it |
| **Backup copy of the movie library**: the same data as above, as it was at the start of the day | Undoing a mistake | Replaced daily (`state/backups`) |
| **Voice check for movie nights** (if on): which members were in the movie night's voice channel around showtime, and for how long (never audio) | Noticing that a movie night happened and asking the movie mods which movie it was | The tally is deleted after the night; the mods' card keeps the names of who stayed, and the history keeps only how many people and how long |
| **Movie change requests and "did it happen?" cards** (if on): who asked for a nomination, removal or history fix, which movie mod answered a card, and whether it was approved | The approval cards | Until the operator deletes `state/movielib.json` |
| **Movie night breaks** (if used): the dates skipped, the reason and the name of the movie mod who added it | Skipping those showings | Dropped once it's been over for 60 days (the next time a break is added) |
| **Game day sign-ups** (if on): the title, time, host and who answered In / Maybe / Can't (user IDs) | Reminders | About two days after the game |
| **Welcome approvals** (if on): the new member's user ID and whether a mod approved or denied them | Keeping the buttons working | Until the operator deletes `state/approvals.json` |
| **Event attendance** (if on): for each event, the user IDs of people in its voice channel and for how long, who checked in or was added by a mod, and what they were paid | Paying rewards and showing attendance history | About 4 months |
| **Time zones**: your user ID and the time zone you picked with `/pingwin timezone set` | Reading the times you type; birthday timing | Until you run `/pingwin timezone remove` |
| **Dashboard sign-ins** (if the operator puts the dashboard online): the Discord ID, name and picture link of admins and members who sign in with Discord or a personal sign-in link (members only for the movie pages) | Keeping them signed in | 14 days, or until they sign out. Unused personal sign-in links expire after 15 minutes |
| **Who's editing**: the names of admins with a dashboard open, and who last saved each settings section | Warning people who edit at the same time | Removed about a minute after they close it; last-saved names until the next save |
| **What's been announced** (events, streams): event and stream IDs | Not posting twice | Old entries are dropped automatically |

The roles you pick are stored by **Discord**, as normal server roles. You can see them on your profile in that
server, and you can remove one at any time by tapping its button again.

## Who can see the movie pages

If the operator puts the dashboard online, the movie pages (the history, everyone's ratings, comments,
questions and answers, the watchlists with who suggested each movie, and the stats) can be read by:

- members who sign in (with the member role, like **Friend**), and
- **anyone who has the view-only link**, without signing in. Admins choose where to share it (for example
  pinned in a channel) and can reset it at any time, which stops the old link working. The data behind the
  page includes each rater's display name, but not their Discord user ID (each person gets a stand-in, so
  their reviews still group together).

The rest of the dashboard (settings, logs, the birthday list, attendance) is only for admins.

## What Pingwin sends, and to whom

- **Discord:** everything Pingwin does (handing out roles, replying, posting menus) goes through Discord. See
  [Discord's Privacy Policy](https://discord.com/privacy).
- **GitHub:** if auto-update is on, Pingwin checks GitHub for new versions of its code. No user data is
  sent.
- **Twitch and YouTube:** if stream alerts are on, Pingwin asks them whether the listed streamers are live.
  No Discord user data is sent.
- **UnbelievaBoat:** if attendance rewards are on, Pingwin tells UnbelievaBoat (another Discord bot's
  service) the user ID of each attendee and how much to add to their balance. See
  [UnbelievaBoat's privacy policy](https://unbelievaboat.com/privacy).
- **OMDb:** if the operator adds an OMDb key, Pingwin looks up movie titles and years there (once per
  movie) for details like runtime and IMDb rating, and sends the words typed in a movie title box to suggest
  matching titles. No Discord user data is sent. See
  [OMDb](https://www.omdbapi.com/legal.htm).
- **Google:** only when an admin imports old movie history from a Google Sheets or Docs link. Pingwin
  downloads those files; it doesn't send anything.
- **Hugging Face:** only if the operator installs voice ratings. The speech-to-text model is downloaded
  from there once per PC, the first time it's used. No recordings or user data are sent; speech-to-text
  runs on the PC.
- **Tailscale:** only if the operator puts the dashboard online. Visitors' requests pass through
  Tailscale's Funnel relay. See [Tailscale's privacy policy](https://tailscale.com/privacy-policy).
- **Image hosts:** the dashboard and movie pages show movie posters and Discord profile pictures straight
  from where they're hosted (like IMDb's image server and Discord), so visitors' browsers load them from there.
- **Nobody else.** Unless the operator puts it online, Pingwin's dashboard only answers on their own computer.

If an operator runs Pingwin on more than one PC, they may sync its settings between those PCs (for example
with SyncThing). That's under their control.

## Your choices

- **Stop getting pings:** tap the role's button again in the menu.
- **Remove your birthday:** `/pingwin birthday remove`. **Remove your time zone:** `/pingwin timezone remove`. **Remove a nomination:** `/pingwin movie remove`. **Change or remove a rating:** rate again to change it, or ask a mod to delete it.
- **Voice ratings:** you don't have to use them. Tap **Cancel** on the card and nothing is saved.
- **Remove what's been logged about you:** ask the server's admins. They can delete Pingwin's log files at any time.
- **Remove Pingwin entirely:** an admin can kick the bot from the server. That stops it from seeing anything there.

## Children

Pingwin is used on Discord, which requires users to be at least 13 (or older where local law requires).
Pingwin doesn't knowingly collect anything beyond what's described above.

## Changes

Changes to this notice are tracked in the project's
[history on GitHub](https://github.com/brandonbtm29/Pingwin/commits/main/PRIVACY.md).

## Contact

For a particular server's copy, contact that server's admins. For the Pingwin project itself,
[open an issue](https://github.com/brandonbtm29/Pingwin/issues).
