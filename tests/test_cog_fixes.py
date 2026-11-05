"""Fixes in the cogs: clock-change moves, birthdays, big role lists, the mod log, stream alerts, attendance
dates and the welcome card."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import discord
import pytest

from pingwin import store, zones
from pingwin.cogs import attendance as att
from pingwin.cogs import birthdays, events, modlog, roles, streams, welcome
from pingwin.config import MenuConfig, MenuRole, parse_config

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def http_error(cls=discord.HTTPException, status=500, text="Nope"):
    return cls(NS(status=status, reason=text), text)


class Channel:
    def __init__(self, cid=9, name="events", fail=None):
        self.id, self.name, self.fail = cid, name, fail
        self.sent, self.deleted, self.edited = [], [], []
        self.mention, self.jump_url = f"<#{cid}>", f"https://discord.com/channels/1/{cid}"

    async def send(self, text=None, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(text if text is not None else kw)
        return NS(id=500 + len(self.sent))

    def get_partial_message(self, mid):
        ch = self

        class Msg:
            async def delete(self):
                ch.deleted.append(mid)

            async def edit(self, content=None, **kw):
                ch.edited.append((mid, content))

        return Msg()


# --------------------------------------------------------------------- events: Pingwin's own clock-change move
FFF_RULE = {"start": "2026-03-14T01:30:00+00:00", "end": None, "frequency": 2, "interval": 1, "by_weekday": [5]}
OLD = datetime(2026, 11, 7, 1, 30, tzinfo=UTC)  # Fri 8:30 PM EST: where Discord kept it
NEW = datetime(2026, 11, 7, 2, 30, tzinfo=UTC)  # Fri 9:30 PM EST: where Pingwin puts it back


def events_cog(ch, ev, lag=False):
    class Http:
        calls = []

        async def get_scheduled_events(self, gid, with_counts):
            return [
                {
                    "id": str(ev.id),
                    "name": ev.name,
                    "status": 1,
                    "scheduled_start_time": ev.start_time.isoformat(),
                    "recurrence_rule": FFF_RULE,
                }
            ]

        async def request(self, route, json=None, reason=None):
            self.calls.append(json)
            if not lag:  # Discord's update usually arrives before Pingwin looks again
                ev.start_time = datetime.fromisoformat(json["scheduled_start_time"]).astimezone(UTC)
            return {}

    cfg = NS(
        enabled=True,
        channel=9,
        announce_new=True,
        remind_minutes=60,
        reminders=[NS(before="1 day", keywords=[], message="", ping=True)],
        remind_at_start=True,
        ping_interested=False,
        rules=[NS(role="Movie fans", keywords=["FFF"])],
        replace_old=True,
        post_next=True,
        keep_local_time=True,
        movie_cards=False,
    )
    guild = NS(id=1, scheduled_events=[ev], me=NS(guild_permissions=NS(manage_events=True)))
    bot = NS(
        features=NS(events=cfg, tz=ET),
        is_active=True,
        home_guild=guild,
        text_channel=lambda c: ch,
        get_channel=lambda i: ch,
        role_named=lambda g, n: NS(mention="<@&1>", name="Movie fans"),
        persona=NS(say=lambda k, **v: f"{k}: {v.get('event')} {v.get('when', '')}"),
        get_cog=lambda n: None,
        user=NS(id=42),
        http=Http(),
    )
    cog = events.EventReminders.__new__(events.EventReminders)
    cog.bot = bot
    return cog


def fff(start=OLD):
    return NS(
        id=777,
        name="FFF",
        description="",
        start_time=start,
        status=discord.EventStatus.scheduled,
        url="https://discord.com/events/1/777",
        guild=None,
    )


@pytest.mark.parametrize("lag", [False, True])
def test_own_clock_change_move_keeps_the_sent_reminders(monkeypatch, lag):
    now = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)  # ~23 hours before: the 1-day reminder already went out
    monkeypatch.setattr(events.discord.utils, "utcnow", lambda: now)

    async def go():
        ch, ev = Channel(), fff()
        cog = events_cog(ch, ev, lag=lag)
        sent = {"start": int(OLD.timestamp()), "reminded:1440": "2026-11-06T01:30:00+00:00", "msg": [9, 400]}
        store.save(events.STATE, {"777": dict(sent)})
        await cog.check.coro(cog)
        if lag:  # Discord's copy catches up a moment later
            ev.start_time = NEW
        await cog.check.coro(cog)
        assert len(cog.bot.http.calls) == 1
        assert ch.sent == [] and ch.deleted == []  # no "next" post, no second ping
        rec = store.load(events.STATE)["777"]
        assert rec["start"] == int(NEW.timestamp())
        assert rec["reminded:1440"] == sent["reminded:1440"] and rec["msg"] == [9, 400]

    asyncio.run(go())


def test_own_move_puts_the_new_time_in_the_next_post(monkeypatch):
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(events.discord.utils, "utcnow", lambda: now)

    async def go():
        ch, ev = Channel(), fff()
        cog = events_cog(ch, ev)
        store.save(
            events.STATE, {"777": {"start": int(OLD.timestamp()), "next": "2026-11-01T03:00:00+00:00", "msg": [9, 400]}}
        )
        await cog.check.coro(cog)
        assert ch.sent == [] and ch.deleted == []
        assert ch.edited == [
            (400, f"event_next: FFF <t:{int(NEW.timestamp())}:F> (<t:{int(NEW.timestamp())}:R>)\n{ev.url}")
        ]
        # A real reschedule is still a new date.
        ev.start_time = NEW + timedelta(days=1)
        await cog._check_date(ev, 1440, now)
        assert len(ch.sent) == 1 and "event_next" in ch.sent[0]

    asyncio.run(go())


def test_clock_change_across_utc_midnight_moves_the_weekday():
    # Weekly Thu 7:30 PM EDT = Thu 23:30 UTC. After the clocks go back, 7:30 PM EST is Fri 00:30 UTC.
    rule = {"start": "2026-09-03T23:30:00+00:00", "end": None, "frequency": 2, "interval": 1, "by_weekday": [3]}

    async def go(raw_rule, start):
        cog = events_cog(Channel(), fff())
        cog._raw_events = [
            {"id": "777", "name": "TTT", "status": 1, "scheduled_start_time": start, "recurrence_rule": raw_rule}
        ]
        guild = NS(id=1, me=NS(guild_permissions=NS(manage_events=True)))
        await cog._keep_local_time(guild, datetime(2026, 11, 2, tzinfo=UTC))
        return cog.bot.http.calls[-1]

    body = asyncio.run(go(rule, "2026-11-05T23:30:00+00:00"))
    new_rule = body["recurrence_rule"]
    assert new_rule["by_weekday"] == [4]
    nxt = events.next_occurrence(
        new_rule, datetime.fromisoformat(body["scheduled_start_time"]) + timedelta(hours=5), ET
    )
    assert nxt.astimezone(ET).strftime("%a %H:%M") == "Thu 19:30"
    # Fri 9:30 PM stays on Saturday in UTC (01:30 -> 02:30): the weekday doesn't change.
    assert asyncio.run(go(FFF_RULE, "2026-11-07T01:30:00+00:00"))["recurrence_rule"]["by_weekday"] == [5]
    assert events.shift_weekdays([0, 1, 2, 3, 4], -1) == [6, 0, 1, 2, 3] and events.shift_weekdays(None, 1) is None


# --------------------------------------------------------------------- birthdays
class Member:
    def __init__(self, uid=111, forbid=False, have=()):
        self.id, self.forbid, self.roles = uid, forbid, list(have)

    async def add_roles(self, *r, reason=None):
        if self.forbid:
            raise http_error(discord.Forbidden, 403, "Missing Permissions")
        self.roles += list(r)

    async def remove_roles(self, *r, reason=None):
        if self.forbid:
            raise http_error(discord.Forbidden, 403, "Missing Permissions")
        self.roles = [x for x in self.roles if x not in r]


def birthday_cog(ch, members, fetch_error=None, birthday_role="Birthday"):
    async def fetch_member(uid):
        raise fetch_error or http_error(discord.NotFound, 404, "Unknown Member")

    role, ping = NS(name="Birthday", mention="<@&9>"), NS(name="Birthday Brigade", mention="<@&8>")
    guild = NS(get_member=lambda uid: members.get(uid), fetch_member=fetch_member)
    cfg = NS(
        enabled=True,
        channel=5,
        hour=9,
        message="🎂 Happy birthday {mentions}! 🎉",
        ping_role="Birthday Brigade",
        birthday_role=birthday_role,
        others=[],
        stars_board=True,
    )
    names = {"Birthday": role, "Birthday Brigade": ping}
    bot = NS(
        features=NS(birthdays=cfg, tz=ET),
        is_active=True,
        home_guild=guild,
        text_channel=lambda c: ch,
        role_named=lambda g, n: names.get(n),
    )
    cog = birthdays.Birthdays.__new__(birthdays.Birthdays)
    cog.bot = bot
    return cog, role


@pytest.fixture
def nov4(monkeypatch):
    class D(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 4, 10, 0, tzinfo=tz)

    monkeypatch.setattr(birthdays, "datetime", D)


def test_birthday_role_forbidden_posts_once_and_the_loop_lives(nov4):
    store.save(birthdays.STATE, {"people": {"111": {"m": 11, "d": 4}}})
    ch = Channel(5, "general")

    async def go():
        for _ in range(3):  # three runs (or restarts) that day
            cog, _ = birthday_cog(ch, {111: Member(forbid=True)})
            await cog.check.coro(cog)

    asyncio.run(go())
    assert ch.sent == ["<@&8> 🎂 Happy birthday <@111>! 🎉"]
    state = store.load(birthdays.STATE)
    assert state["posted_for"] == {"111": "2026-11-04"} and not state.get("role_given")


def test_birthday_post_that_fails_is_tried_again(nov4):
    store.save(birthdays.STATE, {"people": {"111": {"m": 11, "d": 4}}})
    ch = Channel(5, "general", fail=http_error(discord.Forbidden, 403, "Missing Access"))
    member = Member()

    async def go():
        cog, role = birthday_cog(ch, {111: member})
        await cog.check.coro(cog)  # doesn't raise
        assert "posted_for" not in store.load(birthdays.STATE) or not store.load(birthdays.STATE)["posted_for"]
        ch.fail = None
        await cog.check.coro(cog)
        return role

    role = asyncio.run(go())
    assert len(ch.sent) == 1 and role in member.roles
    assert store.load(birthdays.STATE)["role_given"] == {"111": "2026-11-04"}


def test_birthday_role_that_cant_be_taken_away_doesnt_stop_the_loop(nov4):
    store.save(birthdays.STATE, {"people": {}, "role_given": {"111": "2026-11-03"}})

    async def go():
        cog, role = birthday_cog(Channel(5), {})
        cog.bot.home_guild.get_member = lambda uid: Member(forbid=True, have=[role])
        await cog.check.coro(cog)

    asyncio.run(go())
    assert store.load(birthdays.STATE)["role_given"] == {}


def test_members_who_left_get_no_shout_out(nov4):
    store.save(birthdays.STATE, {"people": {"111": {"m": 11, "d": 4}, "222": {"m": 11, "d": 4}}})
    ch = Channel(5)

    async def go(error=None):
        cog, _ = birthday_cog(ch, {111: Member()}, fetch_error=error, birthday_role="")
        await cog.check.coro(cog)

    asyncio.run(go(http_error(status=503, text="Service Unavailable")))  # can't tell: try 222 later
    assert ch.sent == ["<@&8> 🎂 Happy birthday <@111>! 🎉"]
    assert "222" in store.load(birthdays.STATE)["people"]
    asyncio.run(go())  # Discord says 222 isn't in the server: no post, and their birthday is forgotten
    assert len(ch.sent) == 1
    assert store.load(birthdays.STATE)["people"] == {"111": {"m": 11, "d": 4}}


# --------------------------------------------------------------------- big "many" role lists
NAMES = [f"Role {i}" for i in range(30)]


class RoleMember:
    def __init__(self, guild, have):
        self.guild, self.roles, self.id, self.display_name = guild, list(have), 7, "Sam"

    async def add_roles(self, *r, reason=None):
        self.roles += list(r)

    async def remove_roles(self, *r, reason=None):
        self.roles = [x for x in self.roles if x not in r]


def many_menu(n=11):
    return MenuConfig("main", "Pick your pings", "", "many", tuple(MenuRole(name=x) for x in NAMES[:n]))


def test_many_dropdown_toggles_only_the_picked_roles():
    menu = many_menu(30)
    rs = [NS(id=100 + i, name=n) for i, n in enumerate(NAMES)]
    member = RoleMember(NS(roles=rs), [rs[0], rs[1], rs[3], rs[28]])  # Role 28 isn't in the dropdown (25 max)
    persona = NS(say=lambda k, **v: f"{k}:{v.get('role')}")
    assert roles.uses_dropdown(menu)
    msg = asyncio.run(roles.apply_picks(member, menu, {rs[10].id}, persona))
    assert [r.name for r in member.roles] == ["Role 0", "Role 1", "Role 3", "Role 28", "Role 10"]
    assert msg == "role_added:Role 10"
    msg = asyncio.run(roles.apply_picks(member, menu, {rs[1].id, rs[5].id}, persona))
    assert [r.name for r in member.roles] == ["Role 0", "Role 3", "Role 28", "Role 10", "Role 5"]
    assert msg == "role_added:Role 5 role_removed:Role 1"
    assert asyncio.run(roles.apply_picks(member, menu, set(), persona)).startswith("Nothing changed")


def test_many_dropdown_says_how_it_works():
    async def go():
        menu = many_menu()
        resolved = [(m, NS(id=100 + i)) for i, m in enumerate(menu.roles)]
        (dyn,) = roles.menu_view(resolved, menu).children
        assert "add or remove" in dyn.item.placeholder and dyn.item.min_values == 0
        assert "already have removes it" in roles.build_menu_embed(menu, "x").description
        assert roles.menu_footer(menu, NS(say=lambda k, **v: "tap again")) == roles.MANY_HINT
        # The dropdown is sent again after a pick, so the member's picks don't stay ticked.
        edits = []

        async def edit(view=None, **kw):
            edits.append(view)

        await dyn._clear_picks(NS(message=NS(edit=edit)))
        (again,) = edits[0].children
        assert again.item.custom_id == "pingwin:rsel:main" and len(again.item.options) == 11
        assert again.item.max_values == 11

    asyncio.run(go())


def test_many_dropdown_with_time_zones(monkeypatch):
    menu = MenuConfig("zones", "Zones", "", "many", tuple(MenuRole(n, zone=z) for n, _, z in zones.MENU_PRESET[:11]))
    rs = [NS(id=100 + i, name=m.name) for i, m in enumerate(menu.roles)]
    saved = {}
    monkeypatch.setattr(zones, "set_for", lambda uid, z: saved.__setitem__(uid, z))
    member = RoleMember(NS(roles=rs), [])
    asyncio.run(roles.apply_picks(member, menu, {rs[1].id}, NS(say=lambda k, **v: k)))
    assert saved[7] == menu.roles[1].zone and member.roles == [rs[1]]


# --------------------------------------------------------------------- mod log: what counts as a mod channel
class Perms:
    def __init__(self, view=False, **kw):
        self.view_channel = view
        for p in modlog.MOD_PERMS:
            setattr(self, p, kw.get(p, False))


def role(name, managed=False, **perms):
    return NS(name=name, managed=managed, permissions=Perms(**perms))


def test_mod_channels_are_ones_no_regular_role_can_see():
    everyone, friend = role("@everyone"), role("Friend")
    mods, bot_role = role("Mods", manage_messages=True), role("Pingwin", managed=True)
    guild = NS(default_role=everyone, roles=[everyone, friend, mods, bot_role])

    def chan(*can_see):
        return NS(permissions_for=lambda r: NS(view_channel=r in can_see))

    assert not modlog.is_private(chan(friend, mods), guild)  # members get in after approval: not private
    assert not modlog.is_private(chan(everyone), guild)
    assert modlog.is_private(chan(mods, bot_role), guild)  # only mods (and bots) see it
    assert modlog.is_private(chan(), guild)


def test_skipped_channels_match_whole_names():
    assert modlog.skipped("🎵-music", "music") and modlog.skipped("music", "#🎵-music")
    assert modlog.skipped("🤖︱bots", "Bots, memes") and modlog.skipped("memes", "Bots, memes")
    assert not modlog.skipped("robots", "bots") and not modlog.skipped("music-chat", "music")
    assert not modlog.skipped("general", "") and not modlog.skipped("general", " , ")


def test_deletes_are_logged_when_only_approved_members_see_the_channel():
    sent = []

    async def send(embed=None, **kw):
        sent.append(embed)

    everyone, friend = role("@everyone"), role("Friend")
    guild = NS(default_role=everyone, roles=[everyone, friend])
    bot = NS(
        features=NS(modlog=NS(enabled=True, channel=1, messages=True, edits=False, skip_channels="bots")),
        is_active=True,
        home_guild=guild,
        text_channel=lambda c: NS(id=1, name="mod-log", send=send),
        intents=NS(message_content=True),
    )
    cog = modlog.ModLog(bot)

    def msg(name, *can_see):
        ch = NS(id=2, name=name, mention=f"#{name}", permissions_for=lambda r: NS(view_channel=r in can_see))
        return NS(
            guild=guild,
            channel=ch,
            content="hi",
            attachments=[],
            author=NS(bot=False, mention="@a"),
            created_at=datetime(2026, 11, 4, tzinfo=UTC),
        )

    asyncio.run(cog.on_message_delete(msg("robots", friend)))
    asyncio.run(cog.on_message_delete(msg("bots", friend)))
    assert len(sent) == 1 and "#robots" in sent[0].description


# --------------------------------------------------------------------- stream alerts
def streams_cog(ch):
    cfg = NS(
        enabled=True,
        discord_live=True,
        check_minutes=3,
        channel=5,
        role="",
        message="🔴 **{name}** is live! {title}\n{links}",
        streamers=[NS(name="Sam", twitch="sam", youtube="", discord="sam")],
    )
    cog = streams.StreamAlerts.__new__(streams.StreamAlerts)
    cog.bot = NS(
        features=NS(streams=cfg),
        is_active=True,
        text_channel=lambda c: ch,
        role_named=lambda g, n: None,
    )
    cog.session, cog._twitch_token, cog._last_error = NS(closed=False), None, {}

    async def twitch_live(logins):
        return {"sam": {"platform": "Twitch", "key": "tw:1", "title": "Hi", "game": "", "url": "https://twitch.tv/sam"}}

    cog.twitch_live = twitch_live
    return cog


def test_a_failed_stream_alert_is_tried_again_and_the_loop_lives():
    ch = Channel(5, "stream", fail=http_error(text="Bad Gateway"))
    ch.guild = None

    async def go():
        cog = streams_cog(ch)
        await cog.check.coro(cog)  # doesn't raise
        assert store.load(streams.STATE)["Sam"] == {"live": False, "seen": []}
        ch.fail = None
        await cog.check.coro(cog)
        await cog.check.coro(cog)
        assert len(ch.sent) == 1 and store.load(streams.STATE)["Sam"] == {"live": True, "seen": ["tw:1"]}

    asyncio.run(go())


def test_go_live_after_a_stream_that_ended_while_pingwin_was_off():
    ch = Channel(5, "stream")
    ch.guild = None
    vc = NS(mention="<#77>", jump_url="https://discord.com/channels/1/77")
    member = NS(id=7, bot=False, name="sam", display_name="Sam", global_name=None, activities=())
    off, on = NS(self_stream=False, channel=vc), NS(self_stream=True, channel=vc)
    # They were live two hours ago; the "stopped" update came while Pingwin was restarting.
    store.save(streams.STATE, {"discord:7": {"live": True, "at": time.time() - 7200}})

    async def go():
        cog = streams_cog(ch)
        await cog.on_voice_state_update(member, off, on)
        assert len(ch.sent) == 1
        await cog.on_voice_state_update(member, on, off)
        await cog.on_voice_state_update(member, off, on)  # a dropped stream a minute later: no second alert
        assert len(ch.sent) == 1
        ch.fail = http_error()
        store.save(streams.STATE, {"discord:7": {"live": False, "at": 0}})
        await cog.on_voice_state_update(member, off, on)  # doesn't raise; not counted as sent
        assert store.load(streams.STATE)["discord:7"]["at"] == 0

    asyncio.run(go())


# --------------------------------------------------------------------- attendance: a repeating event's next date
MMM_RULE = {"start": "2026-03-17T00:30:00+00:00", "end": None, "frequency": 2, "interval": 1, "by_weekday": [1]}


def test_attendance_follows_the_next_date_of_a_repeating_event(monkeypatch):
    last_week = datetime(2026, 10, 27, 0, 30, tzinfo=UTC)  # Mon 8:30 PM EDT; nobody clicked Start
    this_week = datetime(2026, 11, 3, 1, 30, tzinfo=UTC)  # Mon 8:30 PM EST (the clocks went back)
    now = this_week + timedelta(minutes=10)
    monkeypatch.setattr(att.time, "time", lambda: now.timestamp())
    monkeypatch.setattr(att.discord.utils, "utcnow", lambda: now)
    event = NS(
        id=1,
        name="MMM",
        start_time=last_week,
        end_time=last_week + timedelta(hours=3),
        status=discord.EventStatus.scheduled,
        entity_type=discord.EntityType.voice,
        channel_id=10,
        creator_id=7,
    )
    guild = NS(id=99, scheduled_events=[event], get_channel=lambda cid: None, get_member=lambda uid: None)

    async def rules(g):
        return {1: MMM_RULE}

    reminders = NS(_rules=rules)
    cfg = parse_config({"attendance": {"enabled": True}}, require_token=False)

    async def go(cog_there):
        cog = att.Attendance.__new__(att.Attendance)
        cog.http = None
        cog.bot = NS(
            features=NS(attendance=cfg.features.attendance, tz=ET, secret=lambda n: ""),
            is_active=True,
            home_guild=guild,
            text_channel=lambda c: None,
            get_cog=lambda n: reminders if cog_there and n == "EventReminders" else None,
        )
        await att.Attendance.check.coro(cog)
        return store.load(att.STATE).get("sessions", {})

    assert asyncio.run(go(False)) == {}  # without the repeat rules, the old date is all there is
    sessions = asyncio.run(go(True))
    s = sessions[att.session_key(1, this_week)]
    assert not s["finalized"] and s["ends_at"] == (this_week + timedelta(hours=3)).timestamp()


# --------------------------------------------------------------------- welcome
def test_the_approval_card_goes_out_even_if_the_welcome_fails():
    home = NS(id=1)
    hello = Channel(3, "welcome", fail=http_error(discord.Forbidden, 403, "Missing Access"))
    cards = Channel(4, "approvals")
    cfg = NS(enabled=True, welcome_channel=3, welcome_message="Hi {mention}!", approval_channel=4)
    bot = NS(
        features=NS(welcome=cfg),
        is_active=True,
        home_guild=home,
        text_channel=lambda cid: {3: hello, 4: cards}[cid],
    )
    member = NS(
        id=55,
        bot=False,
        guild=home,
        mention="<@55>",
        display_name="Newbie",
        display_avatar=NS(url="https://cdn.discordapp.com/a.png"),
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    async def go():
        await welcome.Welcome(bot).on_member_join(member)
        assert len(cards.sent) == 1 and "embed" in cards.sent[0]
        cards.fail = http_error()
        await welcome.Welcome(bot).on_member_join(member)  # doesn't raise either

    asyncio.run(go())
    assert store.load(welcome.STATE)["55"]["status"] == "pending"
