# Privacy notice

*Last updated: October 19, 2026*

Pingwin is a self-hosted Discord bot. This notice explains what it sees, what it keeps, and where.

## Who runs Pingwin

Pingwin isn't a central service. Every copy runs on the computer of whoever installed it (the
**operator**), usually a server's admins. The Pingwin project and its author don't receive any data from
anyone's copy. Pingwin has no analytics, no tracking and no ads.

If you have questions about a particular server's Pingwin, ask that server's admins. They're the
operators of that copy.

## What Pingwin sees

Pingwin connects to Discord without any of Discord's "privileged intents". That means:

- **It can't read message content.** It doesn't read, store or log anyone's messages.
- **It doesn't download the member list** or see anyone's online status or activity.
- **It does see:**
  - the server's name, channels and roles, which it needs to set up and hand out roles
  - the username and current roles of whoever clicks one of its buttons or uses one of its commands

## What Pingwin keeps

Everything stays on the operator's computers (and any backup PCs they set up):

| What | Why | How long |
|---|---|---|
| **Activity log**: when someone opts into or out of a role (their Discord username and the role name), which admin ran a settings command, and errors | Troubleshooting | The log rotates automatically: once it reaches about 6 MB, the oldest lines are deleted |
| **Menu locations**: the server, channel and message IDs of posted role menus | Updating menus in place | Until the menu is deleted |
| **Settings**: role names, channel IDs and the names of the PCs running Pingwin | Running the bot | Until the operator changes them |

The roles you pick are stored by **Discord**, as normal server roles. You can see them on your profile in that
server, and you can remove one at any time by tapping its button again.

## What Pingwin sends, and to whom

- **Discord:** everything Pingwin does (handing out roles, replying, posting menus) goes through Discord. See
  [Discord's Privacy Policy](https://discord.com/privacy).
- **GitHub:** if auto-update is on, Pingwin checks GitHub for new versions of its code. No user data is
  sent.
- **Nobody else.** Pingwin's dashboard only answers on the operator's own computer.

If an operator runs Pingwin on more than one PC, they may sync its settings between those PCs (for example
with SyncThing). That's under their control.

## Your choices

- **Stop getting pings:** tap the role's button again in the menu.
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
