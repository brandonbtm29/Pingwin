"""Pingwin's own polls: one vote each or approval with a button per movie, how many to rank on each ranked poll,
and swapping a movie while a poll runs (whoever voted for it gets a DM)."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace as NS

import discord
import pytest
from test_movie_fixes import nom, open_poll
from test_ranked_polls import Part, setup_poll, the_poll

from pingwin import store
from pingwin.cogs import movienight


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


async def start(cog, ch, noms, **kw):
    inter = NS(user=NS(id=5), channel=ch, followup=NS(send=lambda *a, **k: asyncio.sleep(0)))
    closes = discord.utils.utcnow() + timedelta(hours=3)
    await cog.start_poll(inter, cog.series("MMM"), noms, 3, "Pick!", closes, **kw)
    return the_poll()


class Inter:
    def __init__(self, uid):
        self.user, self.said = NS(id=uid), []
        self.response = NS(send_message=self.say, defer=self.defer)
        self.followup = NS(send=self.say)

    async def say(self, text, **kw):
        self.said.append(text)

    async def defer(self, **kw):
        pass


class LinkedPart(Part):
    jump_url = "https://discord.test/poll"


def test_one_vote_each_with_a_button_per_movie(monkeypatch):
    async def go():
        cog, ch, noms = setup_poll(monkeypatch, multiple=False)
        p = await start(cog, ch, noms)
        post = ch.kw[-1]
        assert p["kind"] == "plurality" and "poll" not in post and "embed" in post  # not a Discord poll
        assert [c.custom_id for c in post["view"].children] == [f"pingwin:pv:{p['pid']}:{i}" for i in range(3)]
        assert [c.item.label for c in post["view"].children] == ["Alien (1979)", "Brazil (1985)", "Coherence (2013)"]
        assert "You voted for **Alien (1979)**" in cog.tap_vote(p["pid"], "1", 0)
        assert "You voted for **Brazil (1985)**" in cog.tap_vote(p["pid"], "1", 1)  # changed: one vote each
        cog.tap_vote(p["pid"], "2", 1)
        cog.tap_vote(p["pid"], "3", 2)
        assert cog.tap_vote(p["pid"], "3", 2) == "Took back your vote for **Coherence (2013)**."
        assert the_poll()["ballots"] == {"1": ["b"], "2": ["b"]}
        embed = cog.poll_embed(cog.series("MMM"), the_poll())
        assert "**2** · Brazil (1985)" in embed.description
        assert embed.footer.text.startswith("One vote each") and "2 voted" in embed.footer.text
        polls = await cog._read_polls(cog.series("MMM"), discord.utils.utcnow() - timedelta(days=1))
        assert polls[-1]["answers"][1] == ("Brazil (1985)", 2) and polls[-1]["voters"] == 2
        # Time's up: the most votes wins.
        state = store.load(movienight.STATE)
        state["mmm"]["polls"][-1]["ends"] = (discord.utils.utcnow() - timedelta(seconds=5)).isoformat()
        store.save(movienight.STATE, state)
        await cog.check()
        assert cog.tap_vote(p["pid"], "4", 0) == "This poll has closed."
        _, final = ch.edits[-1]
        assert final["view"] is None and "🏆 Brazil (1985)" in final["embed"].description
        assert final["embed"].footer.text == "One vote each · 2 voted · closed"
        assert ch.sent[-2] == "[movie_poll_winner] Brazil"
        assert the_poll()["result"] == {"winners": ["b"], "final": {"a": 0, "b": 2, "c": 0}}

    asyncio.run(go())


def test_approval_votes_for_as_many_as_you_like(monkeypatch):
    async def go():
        cog, ch, noms = setup_poll(monkeypatch, multiple=True)
        p = await start(cog, ch, noms)
        assert p["kind"] == "approval" and ch.kw[-1]["content"].startswith("Vote for every movie you'd be happy")
        assert cog.tap_vote(p["pid"], "1", 0).startswith("🗳️ Added **Alien (1979)**. Your picks: Alien (1979)\n")
        assert "Your picks: Alien (1979) · Coherence (2013)" in cog.tap_vote(p["pid"], "1", 2)
        assert cog.tap_vote(p["pid"], "1", 0).startswith("Took **Alien (1979)** off. Your picks: Coherence (2013)")
        said = {}

        async def send_message(text, **kw):
            said["text"] = text

        cog.refresh_poll_soon = lambda pid: said.setdefault("refresh", pid)
        tap = NS(user=NS(id=2), client=NS(get_cog=lambda n: cog), response=NS(send_message=send_message))
        await movienight.PollVoteButton(p["pid"], 2, "Coherence (2013)").callback(tap)
        await movienight.PollVoteButton(p["pid"], 1, "Brazil (1985)").callback(tap)
        assert "Your picks: Brazil (1985) · Coherence (2013)" in said["text"] and said["refresh"] == p["pid"]
        assert movienight.tallies(the_poll()) == {"a": 0, "b": 1, "c": 2}
        assert cog.poll_embed(cog.series("MMM"), the_poll()).footer.text.startswith("Approval: tap every movie")
        await cog.finish(cog.series("MMM"), the_poll())
        assert ch.sent[-2] == "[movie_poll_winner] Coherence"

    asyncio.run(go())


def test_how_many_to_rank_is_set_for_each_poll(monkeypatch):
    async def go():
        cog, ch, noms = setup_poll(monkeypatch)  # the setting: rank 3
        p = await start(cog, ch, noms, ranked=True, ranks=2)
        assert p["ranks"] == 2 and len(movienight.RankedModal(p, "1").picks) == 2
        assert "rank up to 2" in ch.kw[-1]["embed"].footer.text
        store.save(movienight.STATE, {"mmm": {"nominations": noms, "polls": []}})
        assert (await start(cog, ch, noms, ranked=True, ranks=5))["ranks"] == 3  # never more than it has
        store.save(movienight.STATE, {"mmm": {"nominations": noms, "polls": []}})
        assert (await start(cog, ch, noms, ranked=True))["ranks"] == 3

    asyncio.run(go())


def swap_setup(monkeypatch):
    cog, ch, noms = setup_poll(monkeypatch, multiple=False)
    state = store.load(movienight.STATE)
    state["mmm"]["nominations"] += [nom("d", "Dune", 1984), nom("e", "Eraserhead", 1977, watched="2026-10-01")]
    store.save(movienight.STATE, state)
    ch.get_partial_message = lambda mid: LinkedPart(ch, mid)
    dms = {}

    class User:
        def __init__(self, uid):
            self.uid = uid

        async def send(self, text):
            if self.uid == 3:
                raise discord.Forbidden(NS(status=403, reason="Forbidden"), "DMs are off")
            dms[self.uid] = text

    cog.bot.get_user = User
    cog.bot.is_mod = lambda member, role: member.id == 7  # the movie mod
    return cog, ch, noms, dms


def test_swapping_a_movie_in_a_ranked_poll(monkeypatch):
    async def go():
        cog, ch, noms, dms = swap_setup(monkeypatch)
        p = await start(cog, ch, noms, ranked=True)
        for uid, r in {"1": ["b", "a"], "2": ["b"], "3": ["c", "b"], "4": ["a"]}.items():
            cog.save_ballot(p["pid"], uid, r)
        typing = NS(namespace=NS(series="MMM"))
        assert [c.value for c in await cog.in_poll_autocomplete(typing, "")] == ["a", "b", "c"]
        assert [c.value for c in await cog.swap_in_autocomplete(typing, "")] == ["d"]  # unwatched, not in it
        someone = Inter(9)  # not a movie mod, and didn't start the poll
        await cog.swap.callback(cog, someone, "MMM", "b", "d")
        assert someone.said == ["Only the movie mods and whoever started the poll can change it."]
        mod = Inter(7)
        await cog.swap.callback(cog, mod, "MMM", "b", "e")
        assert "watchlist" in mod.said[-1]  # watched already
        await cog.swap.callback(cog, mod, "MMM", "b", "a")
        assert mod.said[-1] == "That movie is already in the poll."
        await cog.swap.callback(cog, mod, "MMM", "b", "d")
        p = the_poll()
        assert p["options"] == ["a", "d", "c"] and p["labels"]["d"] == "Dune (1984)"
        assert p["ballots"] == {"1": ["a"], "3": ["c"], "4": ["a"]}  # later picks move up; 2 only had Brazil
        assert mod.said[-1].startswith("Swapped **Brazil (1985)** for **Dune (1984)**. 3 people had voted for it")
        assert "(1 couldn't get it" in mod.said[-1]
        assert "Your other picks moved up. The poll: https://discord.test/poll" in dms[1]
        assert dms[2].endswith("You can vote again: https://discord.test/poll") and 3 not in dms and 4 not in dms
        _, edit = ch.edits[-1]
        assert "Dune (1984)" in edit["embed"].description
        assert "Dune (1984) took the place of Brazil (1985)" in edit["embed"].description
        assert [c.custom_id for c in edit["view"].children] == [f"pingwin:rcv:{p['pid']}"]

    asyncio.run(go())


def test_the_poll_starter_can_swap_and_a_one_vote_poll_loses_the_vote(monkeypatch):
    async def go():
        cog, ch, noms, dms = swap_setup(monkeypatch)
        p = await start(cog, ch, noms)
        cog.tap_vote(p["pid"], "1", 1)
        cog.tap_vote(p["pid"], "2", 0)
        starter = Inter(5)
        await cog.swap.callback(cog, starter, "MMM", "b", "d")
        assert the_poll()["ballots"] == {"2": ["a"]} and "1 person had voted for it" in starter.said[-1]
        assert dms[1].endswith("You can vote again: https://discord.test/poll")
        buttons = ch.edits[-1][1]["view"].children
        assert [c.item.label for c in buttons] == ["Alien (1979)", "Dune (1984)", "Coherence (2013)"]
        assert "You voted for **Dune (1984)**" in cog.tap_vote(p["pid"], "1", 1)
        await cog.swap.callback(cog, starter, "MMM", "c", "b")  # Brazil can come back in another spot
        assert starter.said[-1].endswith("Nobody had voted for it yet.")
        assert the_poll()["options"] == ["a", "d", "b"]

    asyncio.run(go())


def test_a_discord_poll_cant_be_swapped(monkeypatch):
    async def go():
        cog, ch, noms, dms = swap_setup(monkeypatch)
        state = store.load(movienight.STATE)
        state["mmm"]["polls"] = [open_poll(1, ["a", "b"], ago=-3600)]  # an older Discord poll, still running
        store.save(movienight.STATE, state)
        mod = Inter(7)
        await cog.swap.callback(cog, mod, "MMM", "a", "d")
        assert "Discord poll" in mod.said[-1] and the_poll()["options"] == ["a", "b"]

    asyncio.run(go())
