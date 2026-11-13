# Security policy

## Reporting a vulnerability

Please **don't** put the details of a security problem in a public issue. Report it privately instead:
on GitHub, open the repo's **Security** tab and click
[**Report a vulnerability**](https://github.com/brandonbtm29/Pingwin/security/advisories/new). Only the
maintainer sees it. I'll reply within a week.

Things that count as security issues here:

- Any way for a member to get a role that isn't in the menu, or to use the menu without the gate role
- Any way for a non-mod to run `/pingwin rolemenu` or `/pingwin reload|restart`, or to approve movie
  night changes without being a movie mod
- Any way to reach or change the dashboard from another device or website without signing in, or to sign in
  without Manage Server (or the dashboard admin role)
- Any way for a member signed in to the movie pages, or someone with the view-only link, to open the rest of
  the dashboard, or for the view-only link to change anything
- Any way to sign in as someone else (like a personal sign-in link working twice or after it expires),
  or to save someone else's voice rating
- Any way for a signed-in co-admin to manage backup PCs or remote access when full access is off
- The bot token or other secrets showing up in logs, messages or the repo

## Hardening already in place

- Pingwin only hands out roles listed in `config.toml`, matched by name, even if an old menu points elsewhere
- Roles set up by name (the dashboard admin role, movie mods, the gate role) count only as the highest role
  with that name, so a lower copy someone makes and gives themselves doesn't work
- The shared `secrets.env` only sets key names (like `OMDB_API_KEY`) on each PC, never settings other
  programs read (like `GIT_CONFIG_*` or `PYTHONPATH`), and the updater's git ignores `GIT_*` settings
- A personal movie-page link opens only the movie pages, even for an admin
- Exported reviews can't run as spreadsheet formulas, and text members write can't ping roles
- Ping roles are created with no permissions; `/pingwin rolemenu check` warns about roles that have any
- Mod commands use Discord's own permissions (`Manage Roles`, `Manage Server`); movie night's also accept
  the *Movie mods* role
- Privileged gateway intents only when a feature needs them: with Welcome and the mod log off, Pingwin can't
  read message content or the member list
- The dashboard listens on 127.0.0.1 only, checks the Host header, needs a key cookie (HttpOnly,
  SameSite=Strict), and requires a custom header plus a matching Origin for every change
- When it's put online with Tailscale Funnel, Funnel requests (marked by Tailscale, which strips
  look-alike headers) only accept Discord sign-in sessions, never the local key. Sign-in uses the
  `identify` scope, checks membership and Manage Server (or the admin role, or the member role for the
  movie pages only) at sign-in and every 5 minutes, and stores only hashes of session tokens
- The view-only movie link can only read the movie pages. Personal sign-in links work once, for 15 minutes,
  and opening one (like a link preview does) doesn't use it up
- Voice ratings are turned into text on the PC itself; recordings aren't sent anywhere or kept
- The token lives outside the repo; CI and the pre-commit hook scan for leaked secrets
- To know when the shared folder is in sync (before taking the job back from a backup), Pingwin reads
  SyncThing's API key from SyncThing's settings on the same PC and only asks SyncThing's local API for the
  folder's status, checking SyncThing's own certificate. It never changes SyncThing or logs the key
- **Signed updates:** a PC installs an update only if GitHub's newest commit is signed with a key listed in
  `.github/allowed_signers` (checked against the PC's own copy, so a new commit can't add its own key), and
  `main` is protected: no force-pushes, no deleting it, and every commit must be signed. Someone who gets
  into the GitHub account can push, but not sign with the maintainer's key, so the PCs won't run it.

## Running Pingwin on someone else's PC

Every PC that runs Pingwin holds the bot token and installs signed updates from `main`. Only add PCs (and
GitHub collaborators, whose keys would also need to be in `.github/allowed_signers`) you trust. If a PC is
retired, reset the token.
