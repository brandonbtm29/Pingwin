"""The rating question game: a rater picks who answers their question, Pingwin pings that person under the
Rate it post, and the ping shows the answer once it comes."""

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace as NS

import discord
from test_movie_fixes import ET, Chan, make_cog, mmm, use_clock
from test_movie_pages import KEY, headers
from test_remote import data, fake_bot, remote  # noqa: F401  (data is an autouse fixture)

from pingwin import movies
from pingwin.cogs import movienight
from pingwin.dashboard.auth import Sessions
from pingwin.dashboard.server import create_app


class Part:
    """A message we only know the id of (discord's PartialMessage)."""

    def __init__(self, ch, mid):
        self.ch, self.id = ch, mid

    def to_reference(self, **kw):
        return ("ref", self.id)

    async def edit(self, **kw):
        self.ch.edits.append((self.id, kw))

    async def delete(self):
        self.ch.deleted.append(self.id)


class TextChan(Chan):
    def __init__(self, cid=1):
        super().__init__(cid)
        self.edits, self.deleted, self.kw = [], [], []

    def get_partial_message(self, mid):
        return Part(self, mid)

    async def send(self, *a, **kw):
        self.kw.append(kw)
        return await super().send(*a, **kw)


def setup_night(monkeypatch):
    """A cog with one MMM night whose Rate it post is up."""
    use_clock(monkeypatch, datetime(2026, 11, 3, 1, 0, tzinfo=ET))
    cog, _ = make_cog([mmm()], rate=True)
    ch = TextChan(1)
    monkeypatch.setattr(movienight.discord, "TextChannel", TextChan)  # our fake passes the isinstance checks
    cog.bot.get_channel = lambda cid: ch
    cog.bot.text_channel = lambda cid: ch if cid else None
    cog.bot.persona = NS(say=lambda key, **v: f"[{key}] " + " | ".join(f"{k}={x}" for k, x in sorted(v.items())))
    lib = movies.load()
    m = movies.upsert_movie(lib, {"title": "Arrival", "year": 2016})
    e = movies.new_entry(m, "2026-11-02", "MMM")
    e["prompt"] = {"channel": 1, "message": 500, "closes": time.time() + 3600, "closed": False}
    lib["history"].append(e)
    movies.save(lib)
    return cog, ch, e["id"]


def rate(hid, key, name, **kw):
    lib = movies.load()
    e = next(x for x in lib["history"] if x["id"] == hid)
    old = dict(e.get("ratings", {}).get(key) or {}) or None
    movies.add_rating(e, key, name=name, user_id=key, score=kw.pop("score", 7), **kw)
    movies.save(lib)
    return old


def rating(hid, key):
    return next(x for x in movies.load()["history"] if x["id"] == hid)["ratings"][key]


def test_the_chosen_person_is_pinged_and_sees_the_answer(monkeypatch):
    async def go():
        cog, ch, hid = setup_night(monkeypatch)
        old = rate(hid, "1", "Sam", ask="How many aliens out of 10?", ask_for="7", ask_for_name="Jo")
        await cog.after_rating(hid, "1", old)
        (text,) = [t for t in ch.sent if isinstance(t, str)]
        assert text.startswith("[movie_question_for]") and "target=<@7>" in text and "asker=Sam" in text
        kw = ch.kw[-1]
        assert kw["reference"] == ("ref", 500)  # under the Rate it post
        assert [u.id for u in kw["allowed_mentions"].users] == [7] and not kw["allowed_mentions"].everyone
        assert [c.custom_id for c in kw["view"].children] == [f"pingwin:rate:{hid}"]
        ping = rating(hid, "1")["ask_msg"]
        assert ping == [1, 9001]
        # Saving the same question again doesn't ping twice.
        old = rate(hid, "1", "Sam", ask="How many aliens out of 10?", ask_for="7", ask_for_name="Jo")
        await cog.after_rating(hid, "1", old)
        assert len([t for t in ch.sent if isinstance(t, str)]) == 1 and rating(hid, "1")["ask_msg"] == ping
        # Jo answers: the ping shows her answer and loses its button.
        q, asker = movies.question_for(next(x for x in movies.load()["history"] if x["id"] == hid), "7")
        assert (q, asker) == ("How many aliens out of 10?", "1")
        await cog.after_rating(hid, "7", rate(hid, "7", "Jo", answered=q, answered_from=asker, answer="Seven"))
        (mid, edit) = ch.edits[-2] if ch.edits[-1][0] == 500 else ch.edits[-1]
        assert mid == 9001 and edit["view"] is None and "→ Seven" in edit["content"] and "**Jo**" in edit["content"]
        # The Rate it post's footer no longer waits on her.
        e = next(x for x in movies.load()["history"] if x["id"] == hid)
        footer = cog._prompt_embed(e, {"title": "Arrival"}, time.time() + 60).footer.text
        assert not footer

    asyncio.run(go())


def test_changing_the_question_replaces_the_ping(monkeypatch):
    async def go():
        cog, ch, hid = setup_night(monkeypatch)
        await cog.after_rating(hid, "1", rate(hid, "1", "Sam", ask="Best scene?", ask_for="7", ask_for_name="Jo"))
        e = next(x for x in movies.load()["history"] if x["id"] == hid)
        assert cog._prompt_embed(e, {"title": "Arrival"}, time.time() + 60).footer.text == ("Questions waiting for Jo")
        first = rating(hid, "1")["ask_msg"]
        await cog.after_rating(hid, "1", rate(hid, "1", "Sam", ask="Best scene?", ask_for="8", ask_for_name="Kim"))
        assert ch.deleted == [first[1]] and rating(hid, "1")["ask_msg"] != first
        # Asked of whoever's next: no ping, and the old one goes.
        await cog.after_rating(hid, "1", rate(hid, "1", "Sam", ask="Best scene?"))
        assert len(ch.deleted) == 2 and rating(hid, "1").get("ask_msg") is None
        e = next(x for x in movies.load()["history"] if x["id"] == hid)
        footer = cog._prompt_embed(e, {"title": "Arrival"}, time.time() + 60).footer.text
        assert footer == "Next person's question: Best scene?"

    asyncio.run(go())


def test_a_closed_night_points_to_the_movie_pages(monkeypatch):
    async def go():
        cog, ch, hid = setup_night(monkeypatch)
        lib = movies.load()
        lib["history"][0]["prompt"]["closed"] = True
        movies.save(lib)
        await cog.after_rating(hid, "1", rate(hid, "1", "Sam", ask="Why?", ask_for="7", ask_for_name="Jo"))
        assert "movie pages" in ch.sent[-1] and "view" not in ch.kw[-1] and "reference" not in ch.kw[-1]

    asyncio.run(go())


def test_the_rate_it_form(monkeypatch):
    async def go():
        cog, ch, hid = setup_night(monkeypatch)
        rate(hid, "1", "Sam", ask="How many aliens?", ask_for="7", ask_for_name="Jo")
        lib = movies.load()
        e = lib["history"][0]
        form = movienight.RateModal(hid, {"title": "Arrival"}, e, "7")
        labels = [c.text for c in form.children]
        assert labels == [
            "Your score out of 10",
            "What did you think?",
            "Sam asked you",
            "Ask someone a question",
            "Who should answer it?",
        ]
        assert form.children[2].description == "How many aliens?"
        # Someone else doesn't get Jo's question.
        form = movienight.RateModal(hid, {"title": "Arrival"}, e, "8")
        assert [c.text for c in form.children][2] == "Ask someone a question"
        # Changing your rating keeps who you asked.
        form = movienight.RateModal(hid, {"title": "Arrival"}, e, "1")
        assert [v.id for v in form.who.default_values] == [7]
        # Who a question can go to.
        jo, bot = NS(id=7, display_name="Jo", bot=False), NS(id=9, display_name="Pingwin", bot=True)
        assert movienight.pick_asked(e, "8", "Hi?", jo)[0] is None  # Sam's question is waiting for her
        assert movienight.pick_asked(e, "8", "", NS(id=10, display_name="Kim", bot=False)) == (None, "")
        who, note = movienight.pick_asked(e, "8", "Hi?", bot)
        assert who is None and "bots" in note and "whoever rates next" in note
        assert movienight.pick_asked(e, "8", "Hi?", NS(id=10, display_name="Kim", bot=False))[0].id == 10

    asyncio.run(go())


def member(uid, name, is_bot=False):
    return NS(id=uid, display_name=name, name=name.lower(), global_name=None, bot=is_bot)


def test_the_movie_pages_ask_someone_by_name(tmp_path, monkeypatch):
    use_clock(monkeypatch, datetime(2026, 11, 3, 1, 0, tzinfo=ET))
    lib = movies.load()
    m = movies.upsert_movie(lib, {"title": "Arrival", "year": 2016})
    e = movies.new_entry(m, "2026-11-02", "MMM")
    lib["history"].append(e)
    movies.save(lib)
    people = {42: member(42, "Riley"), 7: member(7, "Jo"), 9: member(9, "Pingwin", True)}
    guild = NS(get_member=people.get, members=list(people.values()), name="Clubhouse")

    async def go():
        bot = fake_bot()
        bot.guilds = [guild]
        pinged, cog = [], NS(after_rating=lambda *a: pinged.append(a) or asyncio.sleep(0))
        posting = {"now": False}  # the fake cog only stands in while a review is saved
        bot.get_cog = lambda n: cog if n == "MovieNight" and posting["now"] else None
        from aiohttp.test_utils import TestClient, TestServer

        app = create_app(bot, KEY, remote=remote(True), sessions=Sessions(tmp_path / "sessions.json"))
        async with TestClient(TestServer(app, host="127.0.0.1")) as client:
            me = headers(app, "member")
            got = await (await client.get("/api/movies", headers=me)).json()
            assert [x["name"] for x in got["members"]] == ["Jo", "Riley"]  # members can pick who to ask
            body = {"hid": e["id"], "score": "8", "ask": "How many aliens?", "ask_for": "9"}
            r = await client.post("/api/movies/review", json=body, headers=me)
            assert r.status == 400  # not a bot
            posting["now"] = True
            r = await client.post("/api/movies/review", json={**body, "ask_for": "7"}, headers=me)
            posting["now"] = False
            assert r.status == 200 and "pinged Jo" in (await r.json())["message"]
            assert pinged[-1][:2] == (e["id"], "42")
            got = (await (await client.get("/api/movies", headers=me)).json())["history"][0]["ratings"]
            assert got[0]["ask_for"] == "7" and got[0]["ask_to"] == ["Jo"]
            r = await client.post("/api/movies/review", json={**body, "ask_for": "7", "score": "9"}, headers=me)
            assert "pinged" not in (await r.json())["message"]  # the same question: no second ping

    asyncio.run(go())
    assert discord  # used for the fakes' types


def test_mods_pick_who_rates_first_when_confirming(monkeypatch):
    async def go():
        cog, ch, _ = setup_night(monkeypatch)
        s = cog.series("MMM")
        await cog.ask_night(s, "2026-10-26", {"title": "Contact", "year": 1997}, "The poll picked", 0)
        (card,) = [t for t in ch.sent if isinstance(t, dict)]
        assert any(isinstance(c, movienight.StarterPick) for c in card["view"].children)
        (req,) = [r for r in movies.load()["pending"].values() if r["kind"] == "detected"]
        problem, (embed, _view) = cog.set_night_starter(req["id"], NS(id=7, display_name="Jo", bot=False))
        assert problem == "" and "**Jo** rates first" in embed.description
        assert (
            cog.set_night_starter(req["id"], NS(id=9, display_name="Pingwin", bot=True))[0] == "Bots don't rate movies."
        )
        await cog.decide(req["id"], True, NS(display_name="Mod", name="mod"))
        lib = movies.load()
        e = next(x for x in lib["history"] if lib["movies"][x["movie_id"]]["title"] == "Contact")
        assert e["starter"] == {"uid": "7", "name": "Jo", "by": "Mod"}
        # The Rate it button goes up: Jo is pinged under it, and there's no "Pick who starts".
        await cog.post_prompt(e, lib)
        assert [c.custom_id for c in ch.kw[-2]["view"].children] == [f"pingwin:rate:{e['id']}"]
        assert ch.sent[-1].startswith("[movie_rate_starter]") and "target=<@7>" in ch.sent[-1]
        assert [u.id for u in ch.kw[-1]["allowed_mentions"].users] == [7]
        cog._save_prompt(e)
        assert next(x for x in movies.load()["history"] if x["id"] == e["id"])["starter"]["ping"]
        # Jo rates: her ping says she kicked it off.
        await cog.after_rating(e["id"], "7", rate(e["id"], "7", "Jo"))
        assert any("kicked off" in kw.get("content", "") for _mid, kw in ch.edits)
        assert next(x for x in movies.load()["history"] if x["id"] == e["id"])["starter"]["done"]

    asyncio.run(go())


def test_anyone_picks_who_starts_from_the_rate_it_post(monkeypatch):
    async def go():
        cog, ch, hid = setup_night(monkeypatch)
        e = movies.load()["history"][0]
        assert [type(c).__name__ for c in cog._prompt_view(e).children] == ["RateButton", "StarterButton"]
        assert cog.starter_problem(hid) == ""
        msg = await cog.pick_starter(hid, NS(id=7, display_name="Jo", bot=False), NS(display_name="Sam"))
        assert msg == "Pinged Jo to go first." and "target=<@7>" in ch.sent[-1]
        e = movies.load()["history"][0]
        assert e["starter"]["by"] == "Sam" and e["starter"]["ping"]
        mid, kw = ch.edits[-1]
        assert mid == 500 and [type(c).__name__ for c in kw["view"].children] == ["RateButton"]
        assert kw["embed"].footer.text == "Jo goes first"
        assert "already picked" in cog.starter_problem(hid)
        assert "already picked" in await cog.pick_starter(hid, NS(id=8, display_name="Kim", bot=False), None)

    asyncio.run(go())


def test_once_someone_rates_nobody_picks_a_starter(monkeypatch):
    async def go():
        cog, ch, hid = setup_night(monkeypatch)
        await cog.after_rating(hid, "1", rate(hid, "1", "Sam"))
        assert "already started" in cog.starter_problem(hid)
        mid, kw = ch.edits[-1]
        assert [type(c).__name__ for c in kw["view"].children] == ["RateButton"]

    asyncio.run(go())
