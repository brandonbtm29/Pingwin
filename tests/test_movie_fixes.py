"""Movie night fixes: franchise polls and queues, poll dates, /movie tonight, saving without losing other
changes, remakes, reading polls, DST and rater names on the Rate it card."""

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import discord
import pytest

from pingwin import franchise, movies, store
from pingwin.cogs import movienight
from pingwin.timeparse import next_weekly

ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def use_clock(monkeypatch, start: datetime) -> dict:
    """A clock for the cog's datetime.now (Discord timestamps and time.time stay real)."""
    clock = {"now": start}

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"]

    monkeypatch.setattr(movienight, "datetime", FakeDT)
    return clock


class Poll:
    def __init__(self, votes, done=True):
        self.answers = [NS(id=i + 1, vote_count=v, text=f"a{i}") for i, v in enumerate(votes)]
        self.done = done

    def is_finalised(self):
        return self.done


class Msg:
    def __init__(self, ch, mid, poll=None):
        self.ch, self.id, self.poll = ch, mid, poll

    async def reply(self, text, **kw):
        self.ch.sent.append(text)

    async def end_poll(self):
        self.poll.done = True


class Chan:
    def __init__(self, cid=1):
        self.id, self.sent, self.msgs, self.guild, self.name = cid, [], {}, NS(), f"c{cid}"
        self.hook = None  # runs while we "wait on Discord", like another change landing at the same time
        self.delay = 0.0

    async def _wait(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.hook:
            self.hook()

    async def fetch_message(self, mid):
        await self._wait()
        return self.msgs[mid]

    async def send(self, *a, **kw):
        await self._wait()
        self.sent.append(a[0] if a else kw)
        return Msg(self, 9000 + len(self.sent))


def make_cog(series, **cfg):
    async def never():
        await asyncio.Event().wait()

    ch = Chan(1)
    conf = NS(
        enabled=True,
        auto_details=False,
        omdb_daily=800,
        series=series,
        voice_check=False,
        rate=False,
        rate_hours=48,
        tie="mods",
        mark_watched=True,
        queue_announce_hours=6,
        announce_channel=None,
        approvals=False,
        approval_channel=1,
        mod_role="",
        polls_channel=None,
    )
    vars(conf).update(cfg)
    bot = NS(
        features=NS(movienight=conf, secret=lambda k: None, tz=ET),
        is_active=True,
        wait_until_ready=never,
        text_channel=lambda cid: ch if cid else None,
        get_channel=lambda cid: ch,
        role_named=lambda g, r: None,
        home_guild=None,
        persona=NS(say=lambda key, **v: f"[{key}] {v.get('title', '')}"),
        node=NS(name="pc"),
    )
    cog = movienight.MovieNight(bot)
    cog.check.cancel()
    cog.fill_loop.cancel()
    return cog, ch


def fff():
    return NS(name="FFF", full_name="", role="", channel=1, showtime="Fri 9:30pm", franchises=True)


def mmm(showtime="Mon 8:30pm"):
    return NS(name="MMM", full_name="", role="", channel=1, showtime=showtime)


def nom(i, title, year=None, watched=None):
    return {"id": i, "title": title, "year": year, "by": 1, "added": i, "watched": watched}


def open_poll(mid, options, ago=40, **kw):
    ends = (discord.utils.utcnow() - timedelta(seconds=ago)).isoformat()
    return {"message_id": mid, "channel_id": 1, "options": options, "ends": ends, "status": "open", "by": 1, **kw}


def history():
    lib = movies.load()
    return [(lib["movies"][e["movie_id"]]["title"], e["date"]) for e in lib["history"]]


ALIEN = "✅ Alien (1979)\n✅ Aliens (1986)\nAlien 3 (1992)"
MOD = NS(display_name="Mod", name="mod", id=5)


def night_request(series: str, day: str) -> dict:
    """The pending "did this movie night happen?" card for a night."""
    return next(
        r
        for r in movies.load()["pending"].values()
        if r["kind"] == "detected" and r["series"] == series and r["data"]["date"] == day and r["status"] == "pending"
    )


async def confirm(cog, series: str, day: str, approve: bool = True, **kw) -> str:
    """A movie mod clicks "Yes, we watched it" (or "We skipped it") on that night's card."""
    return await cog.decide(night_request(series, day)["id"], approve, MOD, **kw)


def posts(ch) -> list:
    """What was posted as text (the movie mods' cards are embeds)."""
    return [t for t in ch.sent if isinstance(t, str)]


# 1. A franchise poll's winner waits for the current franchise to finish.
def test_franchise_poll_winner_waits_for_the_last_part(monkeypatch):
    clock = use_clock(monkeypatch, datetime(2026, 11, 6, 16, 0, tzinfo=ET))

    async def go():
        s = fff()
        cog, ch = make_cog([s])
        ch.msgs[77] = Msg(ch, 77, Poll([5, 2]))
        q = franchise.new_queue("Alien", franchise.parse_items(ALIEN))
        noms = [nom("x", "Halloween"), nom("y", "Scream")]
        poll = open_poll(77, ["x", "y"], end_early=True, end_sent=True)  # ended early with endpoll
        store.save(movienight.STATE, {"fff": {"nominations": noms, "polls": [poll], "queue": q}})
        await cog.check()  # 4pm: part 3 announced, and Halloween wins while Alien isn't done
        st = store.load(movienight.STATE)["fff"]
        assert st["queue"]["name"] == "Alien" and st["next_franchise"] == "Halloween"
        assert "Halloween" in cog.queue_embed(s).description
        clock["now"] = datetime(2026, 11, 6, 21, 31, tzinfo=ET)
        await cog.check()  # it started: the movie mods are asked to confirm part 3
        assert history() == [] and night_request("FFF", "2026-11-06")["data"]["expected"]["title"] == "Alien 3"
        await confirm(cog, "FFF", "2026-11-06")  # after the movie: part 3 is logged, then Halloween takes over
        assert history() == [("Alien 3", "2026-11-06")]
        st = store.load(movienight.STATE)["fff"]
        assert st["queue"]["name"] == "Halloween" and st["queue"]["paused"] and "next_franchise" not in st
        assert any("already won the vote" in str(t) for t in ch.sent)

    asyncio.run(go())


def test_franchise_poll_closing_at_showtime_logs_the_last_part_first(monkeypatch):
    use_clock(monkeypatch, datetime(2026, 11, 6, 21, 30, 20, tzinfo=ET))

    async def go():
        cog, ch = make_cog([fff()])
        ch.msgs[77] = Msg(ch, 77, Poll([5, 2]))
        q = franchise.new_queue("Alien", franchise.parse_items(ALIEN))
        q["announced_for"] = "2026-11-06"
        noms = [nom("x", "Halloween"), nom("y", "Scream")]
        poll = open_poll(77, ["x", "y"], end_early=True, end_sent=True)
        store.save(movienight.STATE, {"fff": {"nominations": noms, "polls": [poll], "queue": q}})
        await cog.check()
        await confirm(cog, "FFF", "2026-11-06")
        assert history() == [("Alien 3", "2026-11-06")]
        assert store.load(movienight.STATE)["fff"]["queue"]["name"] == "Halloween"

    asyncio.run(go())


# 2. A poll winner is logged for the showing the poll was for.
def test_poll_showing_is_the_showtime_it_was_for():
    async def go():
        cog, _ = make_cog([mmm()])
        s = cog.series("MMM")
        sunday = "2026-11-01T17:00:00+00:00"  # noon on Sunday, ended early
        assert cog._poll_showing(s, sunday) == datetime(2026, 11, 2, 20, 30, tzinfo=ET)
        at_showtime = datetime(2026, 11, 2, 20, 30, tzinfo=ET).isoformat()
        assert cog._poll_showing(s, at_showtime) == datetime(2026, 11, 2, 20, 30, tzinfo=ET)
        general = cog.series("General")
        assert cog._poll_showing(general, sunday).date().isoformat() == "2026-11-01"  # no showtime: when it closed

    asyncio.run(go())


def test_ending_a_poll_early_logs_it_for_showtime_and_rates_after_it():
    async def go():
        now = datetime.now(ET)
        showtime = (now + timedelta(days=2)).strftime("%a") + " 8:30pm"
        cog, ch = make_cog([mmm(showtime)], rate=True)
        ch.msgs[55] = Msg(ch, 55, Poll([1, 4]))
        poll = open_poll(55, ["a", "b"], ago=30, end_early=True, end_sent=True)
        noms = [nom("a", "Arrival", 2016), nom("b", "Alien", 1979)]
        store.save(movienight.STATE, {"mmm": {"nominations": noms, "polls": [poll]}})
        await cog.check()
        show = next_weekly(showtime, discord.utils.parse_time(poll["ends"]).astimezone(ET) - timedelta(hours=6))
        assert movies.load()["history"] == []  # nothing until a movie mod confirms it
        assert not store.load(movienight.STATE)["mmm"]["nominations"][1]["watched"]
        await confirm(cog, "MMM", show.date().isoformat())  # confirmed early: Rate it still waits
        (e,) = movies.load()["history"]
        assert e["date"] == show.date().isoformat() and e["date"] > now.date().isoformat()
        assert e["rate_at"] > show.timestamp() and not e.get("prompt")  # no Rate it before the movie
        assert store.load(movienight.STATE)["mmm"]["nominations"][1]["watched"]

    asyncio.run(go())


# 3. /movie tonight marks the watchlist and keeps the date it was asked for.
def test_tonight_keeps_its_date_and_comes_off_the_watchlist(monkeypatch):
    clock = use_clock(monkeypatch, datetime(2026, 11, 2, 21, 0, tzinfo=ET))  # Monday, during the movie

    async def go():
        cog, _ = make_cog([mmm()])
        store.save(movienight.STATE, {"mmm": {"nominations": [nom("a", "Arrival", 2016)], "polls": []}})
        s = cog.series("MMM")
        when = cog.tonight_showing(s)
        assert when == datetime(2026, 11, 2, 20, 30, tzinfo=ET)
        data = {"title": "Arrival", "year": 2016, "when": when.isoformat()}
        req = {"kind": "tonight", "series": "MMM", "data": data}
        assert movienight.describe_request(req) == "Set tonight's MMM movie (2026-11-02) to **Arrival**"
        clock["now"] = datetime(2026, 11, 3, 9, 0, tzinfo=ET)  # a mod approves it the next morning
        assert "**Arrival**" in await cog.apply("tonight", "MMM", data, 1)
        assert history() == []
        await confirm(cog, "MMM", "2026-11-02")
        assert history() == [("Arrival", "2026-11-02")]
        assert store.load(movienight.STATE)["mmm"]["nominations"][0]["watched"].startswith("2026-11-02")
        msg = await cog.apply("watched", "MMM", {"id": "a", "title": "Arrival", "watched": True}, 1)
        assert "already marked as watched" in msg and len(movies.load()["history"]) == 1

    asyncio.run(go())


# 4. Saving after waiting on Discord keeps changes made meanwhile.
def test_two_approval_requests_at_once_are_both_kept():
    async def go():
        cog, ch = make_cog([mmm()], approvals=True)
        cog.is_movie_mod = lambda member: False
        ch.delay = 0.05
        a, b = NS(id=1, display_name="Ann", name="ann"), NS(id=2, display_name="Bo", name="bo")
        await asyncio.gather(
            cog.submit("nominate", "MMM", {"title": "Alien"}, a), cog.submit("nominate", "MMM", {"title": "Heat"}, b)
        )
        assert len(ch.sent) == 2 and len(movies.load()["pending"]) == 2

    asyncio.run(go())


def test_a_nomination_made_while_a_poll_closes_is_kept():
    async def go():
        cog, ch = make_cog([mmm()])
        ch.msgs[55] = Msg(ch, 55, Poll([1, 3]))
        noms = [nom("a", "Arrival", 2016), nom("b", "Alien", 1979)]
        store.save(movienight.STATE, {"mmm": {"nominations": noms, "polls": [open_poll(55, ["a", "b"])]}})

        def someone_nominates():
            state = store.load(movienight.STATE)
            state["mmm"]["nominations"].append(nom("c", "Heat", 1995))
            store.save(movienight.STATE, state)
            ch.hook = None

        ch.hook = someone_nominates
        await cog.check()
        data = store.load(movienight.STATE)["mmm"]
        assert [n["title"] for n in data["nominations"]] == ["Arrival", "Alien", "Heat"]
        assert data["polls"][0]["status"] == "done" and not data["nominations"][1]["watched"]
        (req,) = movies.load()["pending"].values()
        assert req["data"]["expected"]["title"] == "Alien"

    asyncio.run(go())


def test_a_review_saved_while_the_rate_it_button_posts_is_kept():
    async def go():
        cog, ch = make_cog([mmm()], rate=True)
        lib = movies.load()
        m = movies.upsert_movie(lib, {"title": "Arrival", "year": 2016})
        e = movies.new_entry(m, "2026-11-02", "MMM")
        e.update(rate_at=time.time() - 1, rate_channel=1)
        lib["history"].append(e)
        movies.save(lib)

        def review_from_the_pages():
            lib = movies.load()
            movies.add_rating(lib["history"][0], "7", name="Sam", user_id="7", score=8)
            movies.save(lib)
            ch.hook = None

        ch.hook = review_from_the_pages
        await cog.check()
        (e,) = movies.load()["history"]
        assert e["ratings"]["7"]["score"] == 8 and e["prompt"]["message"]

    asyncio.run(go())


def test_watching_voice_keeps_watchlist_changes():
    async def go():
        cog, _ = make_cog([mmm("")], voice_check=True)
        cog.bot.home_guild = NS()
        sess = {"day": "2026-10-01", "seconds": {}, "last": 0}
        store.save(movienight.STATE, {"mmm": {"nominations": [], "polls": []}, "voice_watch": {"mmm": sess}})

        async def verdict(s, sess):
            state = store.load(movienight.STATE)
            state["mmm"]["nominations"].append(nom("c", "Heat", 1995))
            store.save(movienight.STATE, state)

        cog._voice_verdict = verdict
        await cog.watch_voice()
        state = store.load(movienight.STATE)
        assert [n["title"] for n in state["mmm"]["nominations"]] == ["Heat"] and "mmm" not in state["voice_watch"]

    asyncio.run(go())


# 5. Pausing after an announcement skips that night.
def test_pausing_after_the_announcement_skips_that_night(monkeypatch):
    clock = use_clock(monkeypatch, datetime(2026, 11, 6, 16, 0, tzinfo=ET))

    def set_paused(paused):  # what the movie pages' Pause and Resume do
        state = store.load(movienight.STATE)
        state["fff"]["queue"]["paused"] = paused
        store.save(movienight.STATE, state)

    async def go():
        cog, ch = make_cog([fff()])
        q = franchise.new_queue("Alien", franchise.parse_items("✅ Alien (1979)\nAliens (1986)\nAlien 3 (1992)"))
        store.save(movienight.STATE, {"fff": {"nominations": [], "polls": [], "queue": q}})
        await cog.run_queues()
        assert ch.sent == ["[franchise_next] Aliens (1986)"]
        set_paused(True)  # "we're skipping tonight"
        clock["now"] = datetime(2026, 11, 6, 21, 31, tzinfo=ET)
        await cog.run_queues()
        clock["now"] = datetime(2026, 11, 7, 12, 0, tzinfo=ET)
        set_paused(False)
        await cog.run_queues()
        assert movies.load()["history"] == []  # the skipped night wasn't logged
        clock["now"] = datetime(2026, 11, 13, 16, 0, tzinfo=ET)
        await cog.run_queues()
        assert ch.sent[-1] == "[franchise_next] Aliens (1986)"  # the same part, a week later

    asyncio.run(go())

    # Resumed before the movie starts: it's on after all.
    q = franchise.new_queue("Alien", franchise.parse_items("Alien (1979)"))
    fri = datetime(2026, 11, 6, 21, 30, tzinfo=ET)
    q.update(announced_for="2026-11-06", paused=True)
    assert franchise.due(q, fri + timedelta(days=7), fri + timedelta(minutes=1), 6) == "cancel"
    q["paused"] = False
    assert franchise.due(q, fri + timedelta(days=7), fri + timedelta(minutes=1), 6) == "log"


# 6. A remake isn't the original.
THING_2011 = {"title": "The Thing", "year": 2011, "imdb_id": "tt0905372", "poster": "https://x/2011.jpg"}


def test_details_for_a_watchlist_remake_dont_touch_the_original(monkeypatch):
    async def lookup(http, key, title, year=None, spent=None, imdb_id=None):
        spent[0] += 1
        return {**THING_2011, "looked_up": 1.0} if year == 2011 else None

    monkeypatch.setattr(movies, "omdb_lookup", lookup)

    async def go():
        lib = movies.load()
        thing = movies.upsert_movie(lib, {"title": "The Thing", "year": 1982})
        thing.update(omdb_tries=3, omdb_at=0)  # gave up on it earlier
        lib["history"].append(movies.new_entry(thing, "2023-10-30", "MMM"))
        movies.save(lib)
        store.save(movienight.STATE, {"mmm": {"nominations": [nom("n1", "The Thing", 2011)], "polls": []}})
        cog, _ = make_cog([mmm()])
        cog.bot.features.secret = lambda k: "key"
        cog._http = lambda: None
        assert (await cog.fill_details(100))["found"] == 1
        lib = movies.load()
        assert not lib["movies"]["thing-1982"].get("imdb_id") and not lib["movies"]["thing-1982"].get("poster")
        assert lib["movies"]["thing-2011"]["imdb_id"] == "tt0905372"
        assert store.load(movienight.STATE)["mmm"]["nominations"][0]["movie_id"] == "thing-2011"

    asyncio.run(go())


def test_merging_details_keeps_an_existing_imdb_id():
    m = {"id": "thing-1982", "title": "The Thing", "year": 1982, "imdb_id": "tt0084787"}
    movies.merge_details(m, {**THING_2011, "genre": "Horror"})
    assert m["imdb_id"] == "tt0084787" and m["genre"] == "Horror"
    bare = {"id": "x", "title": "X"}
    assert movies.merge_details(bare, {"imdb_id": "tt1"})["imdb_id"] == "tt1"  # filled in when missing


def test_logging_the_original_leaves_the_remake_on_the_watchlist():
    async def go():
        cog, _ = make_cog([mmm()])
        noms = [nom("a", "The Thing", 2011), nom("b", "The Thing", 1982), nom("c", "Thing, The")]
        store.save(movienight.STATE, {"mmm": {"nominations": noms, "polls": []}})
        await cog.add_night(cog.series("MMM"), "The Thing", 1982, None, "2026-10-26")
        marks = [bool(n["watched"]) for n in store.load(movienight.STATE)["mmm"]["nominations"]]
        assert marks == [False, True, True]  # no year: the same title still counts

    asyncio.run(go())


def test_importing_a_list_and_nominating_know_remakes():
    async def go():
        cog, _ = make_cog([mmm()])
        lib = movies.load()
        lib["history"].append(
            movies.new_entry(movies.upsert_movie(lib, {"title": "The Thing", "year": 1982}), "x", "MMM")
        )
        movies.save(lib)
        store.save(movienight.STATE, {"mmm": {"nominations": [], "polls": []}})
        rows = [{"title": "The Thing", "year": 2011}, {"title": "The Thing", "year": 1982}]
        assert cog.import_list("MMM", rows, 1) == {"added": 1, "watched": 1, "already": 0}
        (n,) = store.load(movienight.STATE)["mmm"]["nominations"]
        assert n["year"] == 2011 and n["movie_id"] is None  # not pointed at the 1982 movie
        assert cog.import_list("MMM", rows[:1], 1)["already"] == 1
        # The original can be nominated while the remake waits (both have years); the same one can't.
        assert "Added" in await cog.apply("nominate", "MMM", {"title": "The Thing", "year": 1951}, 1)
        assert "already on the watchlist" in await cog.apply("nominate", "MMM", {"title": "The Thing", "year": 2011}, 1)

    asyncio.run(go())


# 7. A franchise part logged another way moves the queue on.
def test_a_part_logged_by_hand_moves_the_queue_on(monkeypatch):
    clock = use_clock(monkeypatch, datetime(2026, 11, 4, 22, 0, tzinfo=ET))

    async def go():
        cog, ch = make_cog([fff()])
        s = cog.series("FFF")
        items = franchise.parse_items("Alien (1979)\nAliens (1986)\nAlien 3 (1992)")
        store.save(
            movienight.STATE, {"fff": {"nominations": [], "polls": [], "queue": franchise.new_queue("A", items)}}
        )
        await cog.add_night(s, "Alien", 1979, None, "2026-11-04")  # a bonus Wednesday, from the movie pages
        q = store.load(movienight.STATE)["fff"]["queue"]
        assert q["items"][0]["done"] == "2026-11-04" and q["logged_for"] == "2026-11-04"
        await cog.add_night(s, "Alien 3", 1992, None, "2026-11-04")  # not the next part: nothing moves
        assert franchise.next_part(store.load(movienight.STATE)["fff"]["queue"])["title"] == "Aliens"
        clock["now"] = datetime(2026, 11, 6, 16, 0, tzinfo=ET)
        await cog.run_queues()
        assert posts(ch) == ["[franchise_next] Aliens (1986)"]
        clock["now"] = datetime(2026, 11, 6, 20, 0, tzinfo=ET)
        data = {"title": "Aliens", "year": 1986, "when": cog.tonight_showing(s).isoformat()}
        await cog.apply("tonight", "FFF", data, 1)  # set by hand before the movie
        clock["now"] = datetime(2026, 11, 6, 21, 31, tzinfo=ET)
        await cog.run_queues()  # the queue's own question goes on the same card
        assert len([r for r in movies.load()["pending"].values() if r["kind"] == "detected"]) == 1
        await confirm(cog, "FFF", "2026-11-06")
        assert sorted(history()) == [("Alien", "2026-11-04"), ("Alien 3", "2026-11-04"), ("Aliens", "2026-11-06")]
        q = store.load(movienight.STATE)["fff"]["queue"]
        assert franchise.next_part(q)["title"] == "Alien 3" and len(posts(ch)) == 1

    asyncio.run(go())


# 8. Reading polls looks at the newest messages.
def test_reading_polls_takes_the_newest_messages():
    asked = {}
    t0 = datetime(2026, 11, 1, 12, 0, tzinfo=ET)

    def msg(minutes, q):
        poll = NS(question=NS(text=q), answers=[NS(text="A", vote_count=1)])
        return NS(poll=poll, created_at=t0 + timedelta(minutes=minutes))

    class Polls:
        name = "polls"

        def history(self, **kw):
            asked.update(kw)

            async def gen():
                for m in (msg(30, "newest"), NS(poll=None), msg(10, "older")):
                    yield m

            return gen()

    async def go():
        cog, _ = make_cog([mmm()])
        ch = Polls()
        cog.bot.text_channel = lambda cid: ch if cid else None
        polls = await cog._read_polls(cog.series("MMM"), t0 - timedelta(days=15))
        assert asked["oldest_first"] is False and asked["limit"] == 200
        assert [p["q"] for p in polls] == ["older", "newest"]  # still handed back oldest first

    asyncio.run(go())


# 9. queue_announce_hours = 0: no announcement, still logged.
def test_no_announcements_still_logs_each_part(monkeypatch):
    clock = use_clock(monkeypatch, datetime(2026, 11, 6, 16, 0, tzinfo=ET))

    async def go():
        cog, ch = make_cog([fff()], queue_announce_hours=0)
        q = franchise.new_queue("Alien", franchise.parse_items("Alien (1979)\nAliens (1986)\nAlien 3 (1992)"))
        store.save(movienight.STATE, {"fff": {"nominations": [], "polls": [], "queue": q}})
        for when in (datetime(2026, 11, 6, 16, 0), datetime(2026, 11, 6, 21, 31), datetime(2026, 11, 13, 21, 31)):
            clock["now"] = when.replace(tzinfo=ET)
            await cog.run_queues()
            await cog.run_queues()
            if when.hour == 21:
                await confirm(cog, "FFF", when.date().isoformat())
        assert history() == [("Alien", "2026-11-06"), ("Aliens", "2026-11-13")]
        assert posts(ch) == []  # nothing announced

    asyncio.run(go())


# 10. Poll lengths are real time across a DST change.
def test_poll_length_across_the_end_of_daylight_saving():
    sat = datetime(2026, 10, 31, 22, 0, tzinfo=ET)  # EDT; clocks go back early Sunday
    closes, hours = movienight.poll_close("Mon 8:30pm", sat, None, 24)
    assert closes == datetime(2026, 11, 2, 20, 30, tzinfo=ET)
    assert closes.timestamp() - sat.timestamp() == 47.5 * 3600 and hours == 48  # Discord's timer ends after it
    closes, hours = movienight.poll_close("Mon 8:30pm", sat, 24, 24)
    assert hours == 24 and closes.timestamp() - sat.timestamp() == 24 * 3600


# 11. The Rate it card shows People names, not user IDs.
def test_rate_it_card_uses_people_names():
    lib = movies.load()
    m = movies.upsert_movie(lib, {"title": "Arrival", "year": 2016})
    e = movies.new_entry(m, "2026-11-02", "MMM")
    movies.add_rating(e, "123", name="Old name", user_id="123", score=8)
    movies.add_rating(e, "name:sam", name="Sam", score=7)
    lib["history"].append(e)
    lib["people"] = {"name:sam": "@456"}
    movies.save(lib)

    async def go():
        cog, _ = make_cog([mmm()])
        members = {123: "Riley", 456: "Samantha"}
        cog.bot.home_guild = NS(get_member=lambda uid: NS(display_name=members[uid]) if uid in members else None)
        desc = cog._prompt_embed(e, m, time.time() + 60).description
        assert "**Riley**: 8/10" in desc and "**Samantha**: 7/10" in desc and "@456" not in desc

    asyncio.run(go())
