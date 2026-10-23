# Security policy

## Reporting a vulnerability

Please **don't** open a public issue for security problems. Use GitHub's
"Report a vulnerability" button (Security tab → Advisories) to report privately.
I'll reply within a week.

Things that count as security issues here:

- Any way for a member to get a role that isn't in the menu, or to use the menu without the gate role
- Any way for a non-mod to run `/pingwin rolemenu` or `/pingwin reload|restart`
- Any way to reach or change the dashboard from another device or website without signing in, or to sign in
  without Manage Server (or the dashboard admin role)
- Any way for a signed-in co-admin to manage backup PCs or remote access when full access is off
- The bot token or other secrets showing up in logs, messages or the repo

## Hardening already in place

- Pingwin only hands out roles listed in `config.toml`, matched by name, even if an old menu points elsewhere
- Ping roles are created with no permissions; `/pingwin rolemenu check` warns about roles that have any
- Mod commands use Discord's own permission checks (`Manage Roles`, `Manage Server`)
- No privileged gateway intents: Pingwin can't read message content or the member list
- The dashboard listens on 127.0.0.1 only, checks the Host header, needs a key cookie (HttpOnly,
  SameSite=Strict), and requires a custom header plus a matching Origin for every change
- When it's put online with Tailscale Funnel, Funnel requests (marked by Tailscale, which strips
  look-alike headers) only accept Discord sign-in sessions, never the local key. Sign-in uses the
  `identify` scope, checks membership and Manage Server (or the admin role) at sign-in and every
  5 minutes, and stores only hashes of session tokens
- The token lives outside the repo; CI and the pre-commit hook scan for leaked secrets

## Running Pingwin on someone else's PC

Every PC that runs Pingwin holds the bot token, and auto-update runs whatever is pushed to
`main` on it. Only add PCs (and GitHub collaborators) you trust. If a PC is retired, reset the token.
