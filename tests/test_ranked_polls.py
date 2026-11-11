"""Ranked-choice polls: people rank their picks with Pingwin's Vote button, and an instant runoff picks the
winner when the poll closes."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import discord
import pytest
from test_movie_fixes import ET, Chan, make_cog, mmm, nom, use_clock

from pingwin import movies, store
from pingwin.cogs import movienight
from pingwin.rankedvote import first_choices, instant_runoff, rounds_text

OPTS = ["a", "b", "c", "d"]


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def ballots(*rankings):
    return {str(i): list(r) for i, r in enumerate(rankings)}


def test_a_majority_of_first_choices_wins_straight_away():
    r = instant_runoff(ballots("ab", "a", "ba", "ac"), OPTS)
    assert r["winners"] == ["a"] and len(r["rounds"]) == 1 and r["voters"] == 4
    assert r["final"] == {"a": 3, "b": 1, "c": 0, "d": 0}


def test_last_place_drops_out_and_its_votes_move_on():
    # a 3, b 3, c 2 (d 0): c and d drop out; c's voters liked b next, so b wins 5 to 3.
    r = instant_runoff(ballots("a", "a", "a", "b", "b", "b", "cb", "cb"), OPTS)
    assert r["winners"] == ["b"] and r["final"] == {"a": 3, "b": 5, "c": 0, "d": 0}
    assert [x["out"] for x in r["rounds"]] == [["d"], ["c"], []]
    text = rounds_text(r, str.upper)
    assert text.splitlines()[0] == "Round 1: A 3 · B 3 · C 2 · D 0, D is out"
    assert text.splitlines()[-1] == "Round 3: B 5 · A 3"


def test_ties():
    assert instant_runoff(ballots("a", "b"), ["a", "b"])["winners"] == ["a", "b"]  # level: a tie
    assert instant_runoff({}, OPTS)["winners"] == []  # nobody voted
    # A tie for last place goes to whoever had fewer votes the round before.
    r = instant_runoff(ballots("a", "a", "a", "b", "b", "cb", "dc", "dc", "d"), ["a", "b", "c", "d"])
    assert r["rounds"][0]["out"] == ["c"]  # c 1 vs b 2
    # Ballots that run out of choices sit out: a wins 2-1 once only 3 ballots are left.
    r = instant_runoff(ballots("a", "a", "b", "c"), ["a", "b", "c"])
    assert r["winners"] == ["a"] and r["final"]["a"] == 2


def test_ballots_are_cleaned_up():
    assert first_choices(ballots("xa", "aab", "", "b"), ["a", "b"]) == {"a": 2, "b": 1}


class Part:
    def __init__(self, ch, mid):
        self.ch, self.id = ch, mid

    async def edit(self, **kw):
        self.ch.edits.append((self.id, kw))

    async def reply(self, text, **kw):
        self.ch.sent.append(text)


class TextChan(Chan):
    def __init__(self, cid=1):
        super().__init__(cid)
        self.edits, self.kw = [], []
        self.guild = NS(id=1)

    def get_partial_message(self, mid):
        return Part(self, mid)

    async def send(self, *a, **kw):
        self.kw.append(kw)
        return await super().send(*a, **kw)

    async def history(self, **kw):  # no Discord polls in the channel
        for m in []:
            yield m


def setup_poll(monkeypatch, **cfg):
    use_clock(monkeypatch, datetime(2026, 11, 9, 12, 0, tzinfo=ET))
    cog, _ = make_cog([NS(**vars(mmm()), ranked=True)], ranked_choices=3, **cfg)
    ch = TextChan(1)
    monkeypatch.setattr(movienight.discord, "TextChannel", TextChan)
    cog.bot.get_channel = lambda cid: ch
    cog.bot.text_channel = lambda cid: ch if cid else None
    noms = [nom("a", "Alien", 1979), nom("b", "Brazil", 1985), nom("c", "Coherence", 2013)]
    store.save(movienight.STATE, {"mmm": {"nominations": noms, "polls": []}})
    return cog, ch, noms


def the_poll():
    return store.load(movienight.STATE)["mmm"]["polls"][-1]


def test_a_ranked_poll_from_start_to_finish(monkeypatch):
    async def go():
        cog, ch, noms = setup_poll(monkeypatch)
        followups = []
        inter = NS(user=NS(id=5), channel=ch, followup=NS(send=lambda *a, **k: followups.append(a) or asyncio.sleep(0)))
        closes = discord.utils.utcnow() + timedelta(hours=3)
        await cog.start_poll(inter, cog.series("MMM"), noms, 3, "MMM: what should we watch?", closes, ranked=True)
        post = ch.kw[-1]
        assert "embed" in post and "poll" not in post  # Pingwin's own message, not a Discord poll
        assert [c.custom_id for c in post["view"].children] == [f"pingwin:rcv:{the_poll()['pid']}"]
        p = the_poll()
        assert p["kind"] == "ranked" and p["labels"]["a"] == "Alien (1979)" and p["ranks"] == 3
        assert "rank up to 3" in post["embed"].footer.text and "0 voted" in post["embed"].footer.text
        # Votes: Brazil leads on first choices, but Coherence's voter likes Alien next.
        for uid, ranking in {"1": ["b"], "2": ["b", "a"], "3": ["a"], "4": ["a", "c"], "5": ["c", "a"]}.items():
            assert cog.save_ballot(p["pid"], uid, ranking)[0] == ""
        assert cog.save_ballot(p["pid"], "6", ["zzz"])[0] == "Pick at least your first choice."
        embed = cog.ranked_embed(cog.series("MMM"), the_poll())
        assert "**2** · Brazil (1985)" in embed.description and "5 voted" in embed.footer.text
        # Where it stands, for event reminders and the voice check: the runoff so far.
        polls = await cog._read_polls(cog.series("MMM"), discord.utils.utcnow() - timedelta(days=1))
        assert polls[-1]["answers"] == [("Alien (1979)", 3), ("Brazil (1985)", 2), ("Coherence (2013)", 0)]
        # Time's up.
        state = store.load(movienight.STATE)
        state["mmm"]["polls"][-1]["ends"] = (discord.utils.utcnow() - timedelta(seconds=5)).isoformat()
        store.save(movienight.STATE, state)
        await cog.check()
        assert cog.save_ballot(p["pid"], "7", ["a"])[0] == "This poll has closed."
        mid, final = ch.edits[-1]
        assert mid == p["message_id"] and final["view"] is None and "🏆 Alien (1979)" in final["embed"].description
        assert "How the count went" in final["embed"].description and "(closed)" in final["embed"].title
        assert ch.sent[-2] == "[movie_poll_winner] Alien"  # the announcement, then the movie mods' card
        assert isinstance(ch.sent[-1], dict) and "Alien" in str(ch.sent[-1]["view"].children[0].item.label)
        done = the_poll()
        assert done["status"] == "done" and done["result"]["winners"] == ["a"]

    asyncio.run(go())


def test_the_vote_form(monkeypatch):
    async def go():
        cog, ch, noms = setup_poll(monkeypatch)
        inter = NS(user=NS(id=5), channel=ch, followup=NS(send=lambda *a, **k: asyncio.sleep(0)))
        closes = discord.utils.utcnow() + timedelta(hours=3)
        await cog.start_poll(inter, cog.series("MMM"), noms, 3, "Pick!", closes, ranked=True)
        p = the_poll()
        cog.save_ballot(p["pid"], "9", ["c", "a"])
        form = movienight.RankedModal(the_poll(), "9")
        assert [c.text for c in form.children] == ["1st choice", "2nd choice", "3rd choice"]
        assert [[o.value for o in s.options if o.default] for s in form.picks] == [["c"], ["a"], []]
        assert form.picks[0].min_values == 1 and form.picks[1].min_values == 0
        # Picking the same one twice counts it once.
        form.picks[0]._values, form.picks[1]._values, form.picks[2]._values = ["b"], ["b"], ["a"]
        said = {}

        async def send_message(text, **kw):
            said["text"] = text

        cog.refresh_ranked_soon = lambda pid: said.setdefault("refresh", pid)
        await form.on_submit(NS(client=NS(get_cog=lambda n: cog), response=NS(send_message=send_message)))
        assert the_poll()["ballots"]["9"] == ["b", "a"]
        assert "1. Brazil (1985) · 2. Alien (1979)" in said["text"] and "counts once" in said["text"]
        assert said["refresh"] == p["pid"]

    asyncio.run(go())


def test_a_tie_goes_to_the_mods(monkeypatch):
    async def go():
        cog, ch, noms = setup_poll(monkeypatch)
        inter = NS(user=NS(id=5), channel=ch, followup=NS(send=lambda *a, **k: asyncio.sleep(0)))
        await cog.start_poll(inter, cog.series("MMM"), noms[:2], 3, "Pick!", discord.utils.utcnow(), ranked=True)
        for uid, r in {"1": ["a"], "2": ["b"]}.items():
            state = store.load(movienight.STATE)
            state["mmm"]["polls"][-1].setdefault("ballots", {})[uid] = r
            store.save(movienight.STATE, state)
        await cog.finish(cog.series("MMM"), the_poll())
        assert ch.sent[-1].startswith("[movie_poll_tie]") and movies.load()["pending"] == {}

    asyncio.run(go())
