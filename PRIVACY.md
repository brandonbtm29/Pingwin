# Privacy notice

*Last updated: October 19, 2026 (version 0.7.0)*

Pingwin is a self-hosted Discord bot. This notice explains what it sees, what it keeps, and where.

## Who runs Pingwin

Pingwin isn't a central service. Every copy runs on the computer of whoever installed it (the
**operator**), usually a server's admins. The Pingwin project and its author don't receive any data from
anyone's copy. Pingwin has no analytics, no tracking and no ads.

If you have questions about a particular server's Pingwin, ask that server's admins. They're the
operators of that copy.

## What Pingwin sees

With its default settings, Pingwin connects to Discord without any of Discord's "privileged intents". That means:

- **It can't read message content.** It doesn't read, store or log anyone's messages.
- **It doesn't download the member list** or see anyone's online status or activity.
- **It does see:**
  - the server's name, channels and roles, which it needs to set up and hand out roles
  - the username and current roles of whoever clicks one of its buttons or uses one of its commands
  - Discord Events in the server (name, description, time, who clicked *Interested*), if event reminders are on

Two optional features change this, and only when the operator turns them on:

- **Welcome and approval** and the **mod log** use the *Server Members* intent, so Pingwin is told when
  someone joins or leaves and when their roles change.
- The **mod log** can also use the *Message Content* intent, so it can show what an edited or deleted
  message said. It posts that to a channel the operator chooses (meant to be mod-only) and doesn't keep its
  own copy beyond Discord's usual short-lived memory of recent messages.

**Stream alerts** check public Twitch and YouTube pages of the streamers the operator lists. They don't
involve any Discord member's data.

## What Pingwin keeps

Everything stays on the operator's computers (and any backup PCs they set up):

| What | Why | How long |
|---|---|---|
| **Activity log**: when someone opts into or out of a role (their Discord username and the role name), which admin ran a settings command, and errors | Troubleshooting | The log rotates automatically: once it reaches about 6 MB, the oldest lines are deleted |
| **Menu locations**: the server, channel and message IDs of posted role menus | Updating menus in place | Until the menu is deleted |
| **Settings**: role names, channel IDs and the names of the PCs running Pingwin | Running the bot | Until the operator changes them |
| **Birthdays** (if on): your Discord user ID with the month and day you entered. Never the year | Posting the shout-out | Until you run `/pingwin birthday remove` or a mod removes it |
| **Movie watchlists** (if on): each nominated title, who nominated it (user ID) and when, and running polls | Running movie night | Until the movie is removed |
| **Movie history and ratings** (if on): which movie was watched when, and each rating: your user ID (or the name from an imported ratings document), score, comment and any question or answer you wrote | Showing the history, averages and stats on the movie pages | Until a mod deletes the rating or the movie night |
| **Movie rater names** (if a mod sets them): which name each rater's ratings count as | Grouping someone's ratings on the movie pages | Until a mod clears it |
| **Movie change requests** (if approvals are on): who asked for a nomination, removal or history fix, and whether a mod approved it | The approval cards | Until the operator deletes `state/movielib.json` |
| **Game day sign-ups** (if on): the title, time, host and who answered In / Maybe / Can't (user IDs) | Reminders | About two days after the game |
| **Welcome approvals** (if on): the new member's user ID and whether a mod approved or denied them | Keeping the buttons working | Until the operator deletes `state/approvals.json` |
| **Event attendance** (if on): for each event, the user IDs of people in its voice channel and for how long, who checked in or was added by a mod, and what they were paid | Paying rewards and showing attendance history | About 4 months |
| **Time zones**: your user ID and the time zone you picked with `/pingwin timezone set` | Reading the times you type; birthday timing | Until you run `/pingwin timezone remove` |
| **Dashboard sign-ins** (if the operator puts the dashboard online): the Discord ID, name and picture link of admins and members who sign in with Discord (members only for the movie pages) | Keeping them signed in | 14 days, or until they sign out |
| **Who's editing**: the names of admins with a dashboard open, and who last saved each settings section | Warning people who edit at the same time | Removed about a minute after they close it; last-saved names until the next save |
| **What's been announced** (events, streams): event and stream IDs | Not posting twice | Old entries are dropped automatically |

The roles you pick are stored by **Discord**, as normal server roles. You can see them on your profile in that
server, and you can remove one at any time by tapping its button again.

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
  movie) for details like runtime and IMDb rating. No Discord user data is sent. See
  [OMDb](https://www.omdbapi.com/legal.htm).
- **Google:** only when an admin imports old movie history from a Google Sheets or Docs link. Pingwin
  downloads those files; it doesn't send anything.
- **Tailscale:** only if the operator puts the dashboard online. Visitors' requests pass through
  Tailscale's Funnel relay. See [Tailscale's privacy policy](https://tailscale.com/privacy-policy).
- **Nobody else.** Unless the operator puts it online, Pingwin's dashboard only answers on their own computer.

If an operator runs Pingwin on more than one PC, they may sync its settings between those PCs (for example
with SyncThing). That's under their control.

## Your choices

- **Stop getting pings:** tap the role's button again in the menu.
- **Remove your birthday:** `/pingwin birthday remove`. **Remove your time zone:** `/pingwin timezone remove`. **Remove a nomination:** `/pingwin movie remove`. **Change or remove a rating:** rate again to change it, or ask a mod to delete it.
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
