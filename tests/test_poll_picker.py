"""Starting a movie poll: pick from the watchlist, type movies in, or both. Typed ones join the watchlist."""

import asyncio
from types import SimpleNamespace as NS

import discord
import pytest
from test_movie_fixes import make_cog, mmm, nom

from pingwin import store
from pingwin.cogs import movienight
from pingwin.cogs.movienight import ChooseView, TypeMoviesModal, parse_typed_movie

OWNER = 5


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


class Response:
    def __init__(self, inter):
        self.inter = inter

    async def send_message(self, text=None, **kw):
        self.inter.said.append(text)

    async def edit_message(self, **kw):
        self.inter.edits.append(kw)

    async def send_modal(self, modal):
        self.inter.modal = modal

    async def defer(self, **kw):
        self.inter.deferred = True


class Inter:
    def __init__(self, user=OWNER, values=None):
        self.user = NS(id=user, display_name="Host")
        self.data = {"values": values or []}
        self.said, self.edits, self.modal, self.deferred = [], [], None, False
        self.response = Response(self)
        self.followup = NS(send=lambda *a, **k: asyncio.sleep(0))

    async def edit_original_response(self, **kw):
        self.edits.append(kw)


LIBRARY = {  # what looking a movie up finds
    "brazil": {"id": "brazil-1985", "title": "Brazil", "year": 1985},
    "alien": {"id": "alien-1979", "title": "Alien", "year": 1979},
}


def setup(monkeypatch, noms, **cfg):
    cog, ch = make_cog(
        [mmm()], **{"pick": "choose", "poll_options": 10, "run_polls": "everyone", "run_role": "", **cfg}
    )
    store.save(movienight.STATE, {"mmm": {"nominations": noms, "polls": []}})

    async def details(title, year=None, imdb_id=None):
        return LIBRARY.get(title.casefold())

    cog.details = details
    posted = {}

    async def start_poll(interaction, s, picks, hours, question, closes=None, ranked=False):
        posted["picks"] = picks
        return NS(jump_url="https://discord.com/channels/1/2/3")

    cog.start_poll = start_poll
    return cog, posted


def buttons(view):
    return {c.label: c for c in view.children if isinstance(c, discord.ui.Button)}


def watchlist():
    return store.load(movienight.STATE)["mmm"]["nominations"]


def test_reading_typed_movies():
    assert parse_typed_movie("Alien (1979)") == {"title": "Alien", "year": 1979, "imdb_id": None}
    assert parse_typed_movie("Blade Runner 2049")["title"] == "Blade Runner 2049"  # a bare number is the title
    assert parse_typed_movie("1917")["title"] == "1917"
    assert parse_typed_movie("https://www.imdb.com/title/tt0078748/") == {
        "title": "",
        "year": None,
        "imdb_id": "tt0078748",
    }
    assert parse_typed_movie("   ") is None


def test_pick_some_type_some_then_post(monkeypatch):
    async def go():
        noms = [nom("a", "Alien", 1979), nom("c", "Coherence", 2013)]
        cog, posted = setup(monkeypatch, noms)
        view = ChooseView(cog, cog.series("MMM"), noms, 3, "Pick!", OWNER)
        assert buttons(view)["Post poll"].disabled and buttons(view)["Type movies"]
        await view.picked(Inter(values=["c"]))
        # The form: Brazil isn't on the watchlist; "alien" is (so it's the watchlist's own entry); a link nobody finds.
        inter = Inter()
        await view.open_form(inter)
        assert isinstance(inter.modal, TypeMoviesModal) and inter.modal.box.style is discord.TextStyle.paragraph
        inter = Inter()
        await view.typed_in(inter, "Brazil (1985)\n\nalien\nhttps://www.imdb.com/title/tt0000001/\n")
        text = inter.edits[-1]["content"]
        assert inter.deferred and "**In the poll (3):** Coherence (2013) · Alien (1979) · Brazil (1985)" in text
        assert "Couldn't find tt0000001" in text and "added to the watchlist" in text
        assert not buttons(view)["Post poll"].disabled
        assert [t["title"] for t in view.typed] == ["Brazil"]
        assert TypeMoviesModal(view).box.default == "Brazil (1985)"  # opening it again shows what was typed
        await view.post(Inter())
        assert [p["title"] for p in posted["picks"]] == ["Coherence", "Alien", "Brazil"]
        brazil = next(n for n in watchlist() if n["title"] == "Brazil")
        assert brazil["by"] == OWNER and brazil["year"] == 1985 and brazil["movie_id"] == "brazil-1985"
        assert len(watchlist()) == 3  # Alien wasn't added twice

    asyncio.run(go())


def test_only_the_host_and_up_to_ten_movies(monkeypatch):
    """Ten is all a Discord poll holds. The How many setting (here 4) is only for oldest/random picks."""

    async def go():
        noms = [nom(f"m{i:02}", f"Movie {i}", 2000 + i) for i in range(12)]
        cog, posted = setup(monkeypatch, noms, poll_options=4)
        view = ChooseView(cog, cog.series("MMM"), noms, 3, "Pick!", OWNER)
        stranger = Inter(user=99, values=["m00"])
        await view.picked(stranger)
        assert stranger.said == ["This menu belongs to whoever started the poll."] and view.chosen == []
        inter = Inter(values=[n["id"] for n in noms[:11]])
        await view.picked(inter)
        assert buttons(view)["Post poll"].disabled and "Take out 1" in inter.edits[-1]["content"]
        await view.picked(Inter(values=[n["id"] for n in noms[:10]]))
        assert not buttons(view)["Post poll"].disabled
        await view.post(Inter())
        assert len(posted["picks"]) == 10

    asyncio.run(go())


def test_starting_a_poll_from_the_dashboard(monkeypatch):
    async def go():
        noms = [nom("a", "Alien", 1979), nom("c", "Coherence", 2013)]
        cog, _ = setup(monkeypatch, noms, poll_hours=24)
        sent = {}

        async def post_poll(s, picks, hours, question, closes=None, ranked=False, *, by=0, fallback=None):
            sent.update(picks=picks, hours=hours, question=question, ranked=ranked, by=by)
            return NS(channel=NS(name="polls")), ""

        cog.post_poll = post_poll
        cog.series_breaks = lambda s: []
        msg = await cog.poll_from_dashboard(cog.series("MMM"), ["c", "nope"], "Brazil (1985)\nalien", 6, "", True, 42)
        assert msg == "Poll posted in #polls with 3 movies."
        assert [p["title"] for p in sent["picks"]] == ["Coherence", "Alien", "Brazil"]  # "nope" isn't there
        assert (
            sent["hours"] == 6
            and sent["ranked"] is True
            and sent["by"] == 42
            and "what should we watch" in sent["question"]
        )
        assert next(n for n in watchlist() if n["title"] == "Brazil")["by"] == 42
        state = store.load(movienight.STATE)
        state["mmm"]["polls"].append({"status": "open", "options": []})
        store.save(movienight.STATE, state)
        with pytest.raises(ValueError, match="already a MMM poll"):
            await cog.poll_from_dashboard(cog.series("MMM"), ["a", "c"], "", None, "", None, 42)
        state["mmm"]["polls"] = []
        store.save(movienight.STATE, state)
        with pytest.raises(ValueError, match="Pick 2 to 10"):
            await cog.poll_from_dashboard(cog.series("MMM"), ["a"], "", None, "", None, 42)
        with pytest.raises(ValueError, match="Couldn't find tt0000001"):
            await cog.poll_from_dashboard(cog.series("MMM"), ["a"], "tt0000001", None, "", None, 42)
        assert len(watchlist()) == 3  # nothing added by the refused ones

    asyncio.run(go())


def test_an_empty_watchlist_opens_the_picker_to_type_movies(monkeypatch):
    async def go():
        cog, posted = setup(monkeypatch, [], pick="random", poll_hours=24, multiple=False)
        cog.series_breaks = lambda s: []
        inter = Inter()
        sent = {}

        async def send_message(text=None, **kw):
            sent.update(kw, text=text)

        inter.response.send_message = send_message
        await cog.poll.callback(cog, inter, "MMM")
        view = sent["view"]
        assert isinstance(view, ChooseView) and "type them with **Type movies**" in sent["text"]
        assert list(buttons(view)) == ["Type movies", "Post poll"]  # no watchlist menu

    asyncio.run(go())
