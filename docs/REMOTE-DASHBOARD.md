# Remote dashboard and co-admins

By default Pingwin's dashboard only opens on the PC that runs it. You can also put it online so
co-admins can open it from anywhere (a laptop, a Mac, a phone) and **sign in with Discord**. They
don't install anything.

It works like this:

- **Tailscale Funnel** gives the dashboard a private-looking but public address, like
  `https://my-pc.tail1234.ts.net:10000`. Only the PC running Pingwin needs Tailscale.
- Visitors see a **Sign in with Discord** page. Pingwin only lets them in if they're in your server and
  have **Manage Server** (or a role you pick). It checks again every few minutes, so removing someone's
  role also removes their access.
- **If that PC is off,** its dashboard link is down, but Pingwin keeps working in Discord from the backup
  PCs. Each PC can have its own link; `/pingwin dashboard` lists them all.

## Set it up (about 5 minutes)

Everything is on the dashboard's **Status** tab, under **Remote access**. Open the dashboard on the PC
itself for this (`scripts\open_dashboard.cmd`).

1. **Tailscale on this PC.** Install it from [tailscale.com/download](https://tailscale.com/download) and
   sign in (the free plan is enough). If it's already there, skip this.
2. **Put it online.** Click **Put it online**. The first time, Tailscale may open a page asking you to allow
   Funnel; approve it and click the button again. Pingwin uses Funnel on port **10000**, so anything
   else this PC shares with Tailscale (on 443 or 8443) is left alone.
3. **Turn on Sign in with Discord.** In the [Developer Portal](https://discord.com/developers/applications),
   open Pingwin → **OAuth2**:
   - Under **Redirects**, click **Add Redirect**, paste the address the dashboard shows (it ends in
     `/auth/callback`), and click **Save Changes**.
   - Copy the **Client Secret** (click **Reset Secret** if it's hidden; this doesn't change the bot
     token) and paste it into the dashboard. It's saved in `secrets.env`, never in `config.toml`.
4. **Who can sign in.** Anyone with Manage Server can. To let others in too, pick an extra role.
5. **Share it.** Send co-admins the link, or tell them to type `/pingwin dashboard` in Discord.

For a backup PC to have its own link, do steps 1 and 2 on that PC and add its address as another
redirect in step 3 (Discord allows several).

## What co-admins can do

Everything on the dashboard, except two things that are kept for the PC's owner:

- adding or removing **backup PCs** (that would hand someone the bot token), and
- changing **remote access** itself.

To let signed-in admins do those too, tick **Also let them add or remove backup PCs** under *Who can
sign in*. Only do that for people you'd trust with the bot token.

Every change made from the dashboard is logged with the name of whoever made it.

## Movie pages for members

The same link also serves the **movie pages** at `/movies` (history, ratings, watchlists, stats; see
[movie night](FEATURES.md#movie-pages)). Anyone with the member role (`required_role`, like **Friend**)
can sign in with Discord there, and only sees the movie pages: the rest of the dashboard says no. Members
who sign in from the main link are sent to the movie pages. `/pingwin movie page` posts the link.

Suggestions made on the movie pages go to the mods' approval channel, the same as in Discord.

## Working at the same time

- **Who's here:** a bar under the tabs shows who else has a dashboard open, which tab they're on, and
  whether they have unsaved changes. This includes dashboards on other PCs.
- **No silent overwrites:** if someone saves a section after you opened it, your save is stopped and a
  banner says who changed what. Click **Discard my changes and reload** to see theirs.
- **Two versions:** if two PCs save the same file within a few seconds, before SyncThing has copied it,
  SyncThing keeps both. The dashboard shows a **Two versions of the settings** panel with what's
  different, and you pick which to keep.

## Turning it off

**Status → Remote access → Take it offline.** The link stops working right away. To sign everyone out,
use **Sign everyone out** in the same place. You can also run `tailscale funnel --https=10000 off`.

## Security notes

- The dashboard still only listens on `127.0.0.1`. Tailscale forwards Funnel visitors to it and marks
  their requests, so Pingwin knows they came from outside. The local sign-in key never works that way.
- Requests must be for this PC's own Tailscale address; anything else is refused.
- Sign-in uses Discord's `identify` scope only: Pingwin sees your Discord ID, name and picture, and
  can't see your servers, messages or email. It doesn't keep Discord's access token.
- Sessions last 14 days and are stored per PC in `.dashboard-sessions.json` (only a hash of each one).
- Changes need a custom header and a matching `Origin`, so other websites can't make changes for you.
