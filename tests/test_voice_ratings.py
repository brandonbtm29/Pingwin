"""Rating by voice message: Pingwin writes down what someone said, finds the score, and saves it once they
tap Save (or Edit)."""

import asyncio
import time
from types import SimpleNamespace as NS

import discord
import pytest

from pingwin import movies, store, voicerate
from pingwin.cogs import movienight, voicerating
from pingwin.voicerate import spoken_score


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    monkeypatch.setattr(voicerate, "available", lambda: True)
    return tmp_path


def test_finding_the_score_in_what_was_said():
    said = {
        "I'd give it a seven and a half out of ten.": 7.5,
        "Honestly, 8/10, the ending was great.": 8,
        "Out of ten, I'd give it a solid eight.": 8,
        "It's one of the best movies I've seen. Ten out of ten.": 10,
        "I'd say 6.5. Way too long.": 6.5,
        "Seven point five, I watched it with two friends.": 7.5,
        "I give it a 7.": 7,
        "It was fine I guess, maybe a six": 6,
        "zero out of ten, never again": 0,
        "I'd give it like an eight": 8,
        # No clear score: they add it themselves.
        "It's one of the best movies I've seen.": None,
        "We watched the 1987 version and it rules": None,
        "That was a seven, eight maybe": None,
        "That was a fun one, and the first one was better": None,
        "One out of ten.": 1,
        "": None,
    }
    assert {t: spoken_score(t) for t in said} == said


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def voice_message(uid=7, guild=None, ref=None, seconds=6.0, audio=True):
    replies = []

    async def reply(content=None, **kw):
        replies.append(kw if content is None else content)

    async def read():
        return b"OggS..."

    att = NS(content_type="audio/ogg", duration=seconds, size=2000, read=read, is_voice_message=lambda: True)
    return NS(
        author=NS(id=uid, bot=False, display_name="Jo", name="jo"),
        flags=NS(voice=True),
        attachments=[att] if audio else [],
        guild=guild,
        reference=NS(message_id=ref) if ref else None,
        channel=NS(typing=Typing),
        reply=reply,
        replies=replies,
    )


def make(heard="Loved it, I'd give it an eight out of ten."):
    async def fetch_member(uid):  # not in the member list: Discord says they're not in the server
        raise discord.NotFound(NS(status=404, reason="Not Found"), "Unknown Member")

    home = NS(get_member=lambda uid: NS(id=uid) if uid in (7, 8) else None, fetch_member=fetch_member)
    rated = []

    async def after_rating(*a):
        rated.append(a)

    night = NS(after_rating=after_rating)
    bot = NS(
        is_active=True,
        home_guild=home,
        features=NS(movienight=NS(enabled=True, rate=True, voice_ratings=True)),
        persona=NS(say=lambda key, **v: f"[{key}] {v.get('score', '')}"),
    )
    cog = voicerating.VoiceRatings(bot)
    cog.tidy.cancel()

    async def transcribe(audio):
        return heard

    cog.transcriber = NS(transcribe=transcribe, unload_if_idle=lambda: False)
    bot.get_cog = lambda n: night if n == "MovieNight" else cog if n == "VoiceRatings" else None
    return cog, rated


def night(title="Arrival", year=2016, message=500, closed=False):
    lib = movies.load()
    m = movies.upsert_movie(lib, {"title": title, "year": year})
    e = movies.new_entry(m, "2026-11-02", "MMM")
    e["prompt"] = {"channel": 1, "message": message, "closes": time.time() + 3600, "closed": closed}
    lib["history"].append(e)
    movies.save(lib)
    return e["id"]


class Press:
    """A button press (or form) from someone: what Pingwin answered."""

    def __init__(self, uid=7, guild=None):
        self.user, self.guild, self.out = NS(id=uid), guild, {}
        self.response = NS(edit_message=self._edit, send_message=self._send, send_modal=self._modal)

    async def _edit(self, **kw):
        self.out["edit"] = kw

    async def _send(self, content=None, **kw):
        self.out["send"] = (content, kw)

    async def _modal(self, modal):
        self.out["modal"] = modal


def card_buttons(reply):
    return {c.op: c.item.disabled for c in reply["view"].children}


def test_a_voice_message_in_a_dm_becomes_a_rating(monkeypatch):
    async def go():
        cog, rated = make()
        hid = night()
        msg = voice_message()
        await cog.on_message(msg)
        (card,) = msg.replies
        assert "8/10" in card["embed"].description and "Loved it" in card["embed"].description
        assert card_buttons(card) == {"save": False, "edit": False, "cancel": False}  # one movie open: no Other
        (did,) = store.load(voicerating.STATE)["drafts"]
        # Only the person who spoke can save it.
        other = Press(uid=8)
        await cog.press(other, did, "save")
        assert "Jo's rating" in other.out["send"][0] and other.out["send"][1]["ephemeral"]
        assert movies.load()["history"][0]["ratings"] == {}
        me = Press()
        await cog.press(me, did, "save")
        r = movies.load()["history"][0]["ratings"]["7"]
        assert r["score"] == 8 and r["comment"].startswith("Loved it") and r["name"] == "Jo"
        assert "8/10" in me.out["edit"]["content"] and me.out["edit"]["view"] is None
        assert rated == [(hid, "7", {})] and store.load(voicerating.STATE)["drafts"] == {}
        again = Press()
        await cog.press(again, did, "save")
        assert "done" in again.out["edit"]["content"]

    asyncio.run(go())


def test_no_score_heard_means_edit_first_and_edit_fills_the_form():
    async def go():
        cog, _ = make("It was a fun one, the music was great.")
        night()
        lib = movies.load()
        movies.add_rating(lib["history"][0], "1", name="Sam", score=7, ask="Best song?", ask_for="7")
        movies.save(lib)
        msg = voice_message()
        await cog.on_message(msg)
        (card,) = msg.replies
        assert "didn't catch" in card["embed"].description and card_buttons(card)["save"] is True
        assert card["embed"].fields[0].name == "Sam asked you"  # her question is waiting
        (did,) = store.load(voicerating.STATE)["drafts"]
        p = Press()
        await cog.press(p, did, "edit")
        form = p.out["modal"]
        assert isinstance(form, movienight.RateModal) and form.draft_id == did
        assert form.score.default is None and form.comment.default.startswith("It was a fun one")
        assert form.question == "Best song?" and form.who is None  # a DM: no member picker
        assert [c.text for c in form.children][-1] == "Ask someone a question"

    asyncio.run(go())


def test_two_movies_open_and_cancel():
    async def go():
        cog, _ = make()
        first = night("Arrival", 2016, 500)
        second = night("Alien", 1979, 600)
        msg = voice_message()
        await cog.on_message(msg)
        assert "Alien" in msg.replies[0]["embed"].title  # the newest one
        (did,) = store.load(voicerating.STATE)["drafts"]
        p = Press()
        await cog.press(p, did, "other")
        assert "Arrival" in p.out["edit"]["embed"].title
        assert store.load(voicerating.STATE)["drafts"][did]["hid"] == first != second
        p = Press()
        await cog.press(p, did, "cancel")
        assert "Nothing was saved" in p.out["edit"]["content"] and movies.load()["history"][0]["ratings"] == {}

    asyncio.run(go())


def test_in_the_server_only_replies_to_pingwin_count():
    async def go():
        cog, _ = make()
        guild = NS(id=1)
        cog.bot.home_guild = guild
        night(message=500)
        msg = voice_message(guild=guild)  # not a reply: someone's voice message for the chat
        await cog.on_message(msg)
        assert msg.replies == []
        msg = voice_message(guild=guild, ref=123)  # a reply to something else
        await cog.on_message(msg)
        assert msg.replies == []
        msg = voice_message(guild=guild, ref=500)  # a reply to the Rate it post
        await cog.on_message(msg)
        assert "8/10" in msg.replies[0]["embed"].description
        msg = voice_message(guild=guild, ref=500, audio=False)  # the reply didn't ping Pingwin
        await cog.on_message(msg)
        assert "Keep the @ on" in msg.replies[0]

    asyncio.run(go())


def test_when_it_cant_listen(monkeypatch):
    async def go():
        cog, _ = make()
        msg = voice_message()
        await cog.on_message(msg)
        assert "nothing to rate" in msg.replies[0]
        night()
        msg = voice_message(uid=99)  # not in the server
        await cog.on_message(msg)
        assert msg.replies == []
        msg = voice_message(seconds=600)
        await cog.on_message(msg)
        assert "long one" in msg.replies[0]
        monkeypatch.setattr(voicerate, "available", lambda: False)
        msg = voice_message()
        await cog.on_message(msg)
        assert "can't listen" in msg.replies[0]
        cog.bot.features.movienight.voice_ratings = False
        msg = voice_message()
        await cog.on_message(msg)
        assert msg.replies == []

    asyncio.run(go())


def test_saving_from_the_form_replaces_the_card(monkeypatch):
    async def go():
        cog, rated = make()
        hid = night()
        msg = voice_message()
        await cog.on_message(msg)
        (did,) = store.load(voicerating.STATE)["drafts"]
        lib = movies.load()
        form = movienight.RateModal(hid, {"title": "Arrival"}, lib["history"][0], "7", draft={"score": 8}, draft_id=did)
        form.score._value, form.comment._value, form.ask._value = "8.5", "So good", ""
        p = Press()
        p.client = NS(get_cog=cog.bot.get_cog, persona=cog.bot.persona)
        p.user = NS(id=7, display_name="Jo", name="jo")
        await form.on_submit(p)
        assert p.out["edit"]["content"].startswith("[movie_rated] 8.5") and p.out["edit"]["view"] is None
        assert store.load(voicerating.STATE)["drafts"] == {}
        assert movies.load()["history"][0]["ratings"]["7"]["score"] == 8.5 and rated[-1][:2] == (hid, "7")

    asyncio.run(go())
