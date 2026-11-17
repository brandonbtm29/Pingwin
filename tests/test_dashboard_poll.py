"""Starting a movie poll from the dashboard's Movie watchlists panel."""

from types import SimpleNamespace as NS

from test_dashboard_fixes import OWNER, make_bot, run, signed_in
from test_remote import data  # noqa: F401  (an autouse fixture)


class PollCog:
    def __init__(self):
        self.calls = []

    def series(self, name):
        return NS(name="MMM") if name == "mmm" else None

    async def poll_from_dashboard(self, s, ids, typed, hours, question, ranked, by):
        self.calls.append((s.name, ids, typed, hours, question, ranked, by))
        if typed == "boom":
            raise ValueError("Pick 2 to 10 movies (that's 1).")
        return "Poll posted in #polls with 3 movies."


def bot_with(cog, active=True):
    bot = make_bot(cog=cog)
    bot.is_active = active
    bot.coordinator = NS(leader="MAIN-PC")
    return bot


POLL = {"action": "poll", "series": "mmm", "ids": ["a", 7], "typed": "Brazil (1985)", "hours": "", "ranked": True}


def test_admins_start_a_poll_from_the_dashboard():
    cog = PollCog()

    async def scenario(client, app):
        r = await client.post("/api/watchlist", json=POLL, headers=OWNER)
        assert r.status == 200 and (await r.json())["message"] == "Poll posted in #polls with 3 movies."
        assert cog.calls[-1] == ("MMM", ["a"], "Brazil (1985)", None, "", True, 0)  # the owner: no Discord ID
        r = await client.post("/api/watchlist", json={**POLL, "hours": "6", "ranked": "yes"}, headers=signed_in(app))
        assert r.status == 200 and cog.calls[-1][3:] == (6, "", None, 42)
        r = await client.post("/api/watchlist", json={**POLL, "typed": "boom"}, headers=OWNER)
        assert r.status == 400 and "Pick 2 to 10" in (await r.json())["error"]
        r = await client.post("/api/watchlist", json={**POLL, "series": "nope"}, headers=OWNER)
        assert r.status == 400
        r = await client.post("/api/watchlist", json={**POLL, "hours": "999"}, headers=OWNER)
        assert r.status == 200 and cog.calls[-1][3] == 768  # kept in range
        r = await client.post("/api/watchlist", json=POLL, headers=signed_in(app, "member"))
        assert r.status == 403  # members can't use the dashboard
        body = await (await client.get("/api/watchlist", headers=OWNER)).json()
        assert body["poll_max"] == 10 and body["can_post"] is True

    run(scenario, bot_with(cog))


def test_a_standby_pc_doesnt_post_polls():
    cog = PollCog()

    async def scenario(client, app):
        r = await client.post("/api/watchlist", json=POLL, headers=OWNER)
        assert r.status == 409 and "MAIN-PC" in (await r.json())["error"] and cog.calls == []
        body = await (await client.get("/api/watchlist", headers=OWNER)).json()
        assert body["can_post"] is False and body["leader"] == "MAIN-PC"

    run(scenario, bot_with(cog, active=False))
