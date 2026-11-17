"""/pingwin movie pollinfo: each poll movie's IMDb link, rating, runtime, genre and short plot, just for you."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace as NS

import discord
import pytest
from test_movie_fixes import make_cog, mmm, nom

from pingwin import movies, store
from pingwin.cogs import movienight
from pingwin.cogs.movienight import imdb_url, poll_details_embed

ALIEN = {
    "id": "alien-1979",
    "title": "Alien",
    "year": 1979,
    "imdb_id": "tt0078748",
    "imdb_rating": 8.5,
    "runtime": 117,
    "genre": "Horror, Sci-Fi",
    "plot": "The crew of a commercial spacecraft encounters a deadly lifeform after investigating an unknown "
    "transmission.",
    "looked_up": 1,
}


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


def test_the_details_message():
    e = poll_details_embed([(nom("a", "Alien", 1979), ALIEN), (nom("b", "Brazil"), None)])
    alien, brazil = e.description.split("\n\n")
    assert alien.startswith(
        "**[Alien (1979)](https://www.imdb.com/title/tt0078748/)** · ⭐ 8.5 · 117 min · Horror, Sci-Fi"
    )
    assert "deadly lifeform" in alien
    assert brazil == "**[Brazil](https://www.imdb.com/find/?q=Brazil&s=tt)**"  # not looked up: an IMDb search
    assert imdb_url(None, "The Thing", 1982) == "https://www.imdb.com/find/?q=The+Thing+1982&s=tt"
    # Ten movies with long plots still fit in one message.
    long = {**ALIEN, "plot": "word " * 200}
    big = poll_details_embed([(nom(str(i), f"Movie {i}", 2000 + i), long) for i in range(10)])
    assert len(big.description) <= 4096 and big.description.count("…") == 10
    assert poll_details_embed([]) is None


class Chan:
    def __init__(self):
        self.id, self.name, self.guild, self.sent = 1, "polls", NS(), []

    async def send(self, content=None, **kw):
        self.sent.append(NS(content=content, **kw))
        return NS(id=900 + len(self.sent))


class Inter:
    def __init__(self):
        self.user = NS(id=5)
        self.deferred, self.sent = None, []

        async def defer(**kw):
            self.deferred = kw

        async def send(text=None, **kw):
            self.sent.append(NS(text=text, **kw))

        self.response = NS(defer=defer, send_message=send)
        self.followup = NS(send=send)


def test_polls_point_to_pollinfo_which_sends_the_details(monkeypatch):
    async def go():
        cog, _ = make_cog([NS(**vars(mmm()), ranked=False)], poll_details=True, multiple=False)
        ch = Chan()
        cog.bot.text_channel = lambda cid: ch if cid else None

        async def command_mention(path):
            return f"</pingwin {path}:77>"

        cog.bot.command_mention = command_mention
        lib = movies.load()
        lib["movies"]["alien-1979"] = ALIEN
        movies.save(lib)
        looked = []

        async def details(title, year=None, imdb_id=None):  # Brazil isn't in the library yet: look it up
            looked.append(title)
            return {"id": "brazil-1985", "title": "Brazil", "year": 1985, "plot": "A clerk dreams.", "looked_up": 1}

        cog.details = details
        picks = [{**nom("a", "Alien", 1979), "movie_id": "alien-1979"}, nom("b", "Brazil", 1985)]
        store.save(movienight.STATE, {"mmm": {"nominations": picks, "polls": []}})
        inter = Inter()
        await cog.pollinfo.callback(cog, inter, None)
        assert inter.sent[-1].text == "No movie poll is running right now."
        closes = discord.utils.utcnow() + timedelta(hours=3)
        msg, problem = await cog.post_poll(cog.series("MMM"), picks, 3, "Pick!", closes, False, by=5)
        assert problem == "" and len(ch.sent) == 1  # just the poll, nothing posted under it
        assert "IMDb links and plots for these movies: </pingwin movie pollinfo:77>" in ch.sent[0].content
        inter = Inter()
        await cog.pollinfo.callback(cog, inter, "MMM")
        assert inter.deferred == {"ephemeral": True, "thinking": True}
        (embed,) = inter.sent[-1].embeds
        assert inter.sent[-1].ephemeral and "MMM" in embed.title
        assert "tt0078748" in embed.description and "A clerk dreams." in embed.description
        assert looked == ["Brazil"]  # Alien was already known
        cog.cfg.poll_details = False  # turned off: the poll doesn't mention it
        store.save(movienight.STATE, {"mmm": {"nominations": picks, "polls": []}})
        await cog.post_poll(cog.series("MMM"), picks, 3, "Pick!", closes, False, by=5)
        assert "pollinfo" not in ch.sent[-1].content

    asyncio.run(go())
