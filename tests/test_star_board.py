"""The Star birthdays message: one message, edited daily, posted again on a birthday."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace as NS

import discord
import pytest

from pingwin import store
from pingwin.cogs import birthdays
from pingwin.cogs.birthdays import life_line, parse_died, star_board, star_people


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    return tmp_path


ROWS = [
    NS(name="Keanu Reeves", date="1964-09-02", note="", died=""),
    NS(name="Wes Craven", date="Aug 2, 1939", note="", died="2015-08-30"),
    NS(name="Paul Walker", date="9/12/1973", note="", died="2013"),
    NS(name="Laurence Fishburne", date="1961-07-30", note="", died=""),
    NS(name="Arnold Schwarzenegger", date="1947-07-30", note="", died=""),
    NS(name="Broken", date="someday", note="", died=""),
]
LIB = {
    "movies": {
        "a": {"id": "a", "title": "John Wick", "stars": "Keanu Reeves, Michael Nyqvist"},
        "b": {"id": "b", "title": "The Matrix", "stars": "Keanu Reeves\nLaurence Fishburne"},
        "c": {"id": "c", "title": "Scream", "director": "Wes Craven"},
    },
    "history": [
        {"movie_id": "a", "date": "2026-03-06"},
        {"movie_id": "b", "date": "2026-07-13"},
        {"movie_id": "c", "date": None},
        {"movie_id": "a", "date": "2026-04-01"},  # the same movie again counts once
    ],
}


def test_died_and_life_lines():
    assert parse_died("2015-08-30") == (2015, 8, 30) and parse_died("2013") == (2013, None, None)
    assert parse_died("") is None and parse_died("Aug 30") is None  # a death date needs its year
    people = {p["name"]: p for p in star_people(ROWS)}
    assert "Broken" not in people
    assert life_line(people["Keanu Reeves"]) == "Born Sep 2, 1964"
    assert life_line(people["Wes Craven"]) == "Aug 2, 1939 – Aug 30, 2015 · died at 76"
    assert life_line(people["Paul Walker"]) == "1973–2013"


def test_board_on_a_birthday_and_on_other_days():
    people = star_people(ROWS)
    text, today = star_board(people, LIB, date(2026, 9, 2), 14)
    assert today
    assert "**Keanu Reeves**, 62 today" in text
    assert "Our movie nights with them (2): John Wick (Mar 2026), The Matrix (Jul 2026)" in text
    assert "**Sep 12** · Paul Walker, would be 53 · in 10 days" in text
    text, today = star_board(people, LIB, date(2026, 8, 2), 14)
    assert "**Wes Craven**, who would have been 87" in text and "died at 76" in text
    assert "Scream" in text and "Keanu" not in text  # Sep 2 is a month away: not listed
    text, today = star_board(people, LIB, date(2026, 7, 20), 14)
    assert text.count("**Jul 30**") == 2 and "Aug 2" in text and "Sep 2" not in text
    text, today = star_board(people, LIB, date(2026, 7, 20), 7)
    assert "Jul 30" not in text and "No star birthdays in the next week." in text
    text, today = star_board(people, LIB, date(2026, 11, 3), 14)
    assert not today and "No star birthdays in the next 2 weeks." in text


class Channel:
    def __init__(self):
        self.id = 5
        self.sent, self.edited, self.deleted = [], [], []

    async def send(self, text, **kw):
        self.sent.append(text)
        return NS(id=100 + len(self.sent))

    def get_partial_message(self, mid):
        channel = self

        class Msg:
            async def edit(self, content):
                channel.edited.append((mid, content))

            async def delete(self):
                channel.deleted.append(mid)

        return Msg()


def make_cog(channel):
    async def never():
        await asyncio.Event().wait()

    cfg = NS(enabled=True, channel=5, hour=9, others=ROWS, stars_board=True, stars_days=14, others_message="")
    bot = NS(features=NS(birthdays=cfg), text_channel=lambda c: channel, get_channel=lambda i: channel)
    cog = birthdays.Birthdays.__new__(birthdays.Birthdays)
    cog.bot = bot
    return cog


def test_edits_quietly_and_reposts_on_a_birthday(monkeypatch):
    monkeypatch.setattr(birthdays.movies, "load", lambda: LIB)

    async def go():
        ch = Channel()
        cog = make_cog(ch)
        state = {}
        assert not await cog._star_board(state, datetime(2026, 8, 30, 8))  # before the birthday hour
        assert await cog._star_board(state, datetime(2026, 8, 30, 9))
        assert len(ch.sent) == 1 and state["star_board"]["message"] == 101
        assert not await cog._star_board(state, datetime(2026, 8, 30, 12))  # nothing changed
        assert await cog._star_board(state, datetime(2026, 8, 31, 9))  # a new day, no birthday: edit
        assert len(ch.sent) == 1 and ch.edited[-1][0] == 101
        assert await cog._star_board(state, datetime(2026, 9, 2, 9))  # Keanu's birthday: delete, post again
        assert ch.deleted == [101] and len(ch.sent) == 2 and state["star_board"]["message"] == 102
        assert "62 today" in ch.sent[-1]
        assert not await cog._star_board(state, datetime(2026, 9, 2, 15))  # once a day

    asyncio.run(go())


def test_posts_again_when_the_old_one_is_gone(monkeypatch):
    monkeypatch.setattr(birthdays.movies, "load", lambda: LIB)

    async def go():
        ch = Channel()

        def gone(mid):
            class Msg:
                async def edit(self, content):
                    raise discord.NotFound(NS(status=404, reason="gone"), "Unknown Message")

            return Msg()

        ch.get_partial_message = gone
        cog = make_cog(ch)
        state = {"star_board": {"message": 7, "channel": 5, "day": "2026-08-30", "text": "old"}}
        assert await cog._star_board(state, datetime(2026, 8, 31, 9))
        assert len(ch.sent) == 1 and state["star_board"]["message"] == 101

    asyncio.run(go())


def test_old_rows_still_work():
    store.save("birthdays", {})
    assert star_people([NS(name="Friend", date="Mar 3", note="", died="")])[0]["y"] is None
