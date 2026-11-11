"""Event reminders: many reminders, the nearest one only, and where a movie night's vote stands."""

from types import SimpleNamespace as NS

from pingwin import voicewatch as vw
from pingwin.cogs.events import due_reminder, reminder_list


def test_reminder_times():
    assert vw.parse_before("1 day") == 1440 and vw.parse_before("2h") == 120 and vw.parse_before("1h30m") == 90
    assert vw.parse_before("15 min") == 15 and vw.parse_before("90") == 90 and vw.parse_before("1 week") == 10080
    assert vw.parse_before("soon") is None and vw.parse_before("") is None and vw.parse_before("3 fortnights") is None


def test_many_reminders_but_only_the_nearest_when_several_are_due():
    cfg = NS(
        remind_minutes=300,
        reminders=[
            NS(before="1 day", keywords=[], message="", ping=False),
            NS(before="30 min", keywords=["MMM", "Mystery"], message="Popcorn time! {poll}", ping=True),
            NS(before="nonsense", keywords=[], message="", ping=True),
        ],
    )
    rs = reminder_list(cfg)
    assert [r["minutes"] for r in rs] == [300, 1440, 30]
    pick, mark = due_reminder(rs, "Mystery Movie Monday", 1000, set())
    assert pick["minutes"] == 1440 and mark == [1440]
    pick, mark = due_reminder(rs, "Mystery Movie Monday", 200, {1440})
    assert pick["minutes"] == 300 and mark == [300, 1440]
    # Pingwin was off all day: only the 30 minute one goes out, the rest are marked sent.
    pick, mark = due_reminder(rs, "Mystery Movie Monday", 20, set())
    assert pick["minutes"] == 30 and mark == [30, 300, 1440]
    # Game night doesn't match MMM's keywords, so its nearest reminder is the 5 hour one.
    pick, _ = due_reminder(rs, "Game night", 20, set())
    assert pick["minutes"] == 300
    assert due_reminder(rs, "Game night", 20, {300, 1440}) == (None, [])
    assert due_reminder(rs, "Game night", -5, set()) == (None, [])  # already started


def poll(q, *answers, at):
    return {"q": q, "answers": list(answers), "at": at}


def test_where_the_vote_stands():
    week = 100.0
    polls = [
        poll("If Horror wins...", ("Halloween", 1), ("Saw", 0), ("Monster Squad", 2), at=50),  # last week's
        poll("Next MMM movie", ("Horror", 3), ("Not Horror", 2), at=120),
        poll("If Not Horror wins ...", ("Soul", 3), ("Hitch", 1), at=121),
    ]
    st = vw.standings(polls, week)
    assert st["lead"] == ["horror"] and st["pick"] == "Monster Squad" and st["votes"] == 5
    assert vw.standings_line(st) == "🗳️ **Horror** is ahead (3–2), so it's looking like **Monster Squad**."
    tie = vw.standings([*polls[:1], poll("Next MMM", ("Horror", 2), ("Not Horror", 2), at=130), polls[2]], week)
    line = vw.standings_line(tie)
    assert line.startswith("🗳️ It's a tie (2–2)!") and "**Monster Squad**" in line and "**Soul**" in line
    empty = vw.standings([poll("Next MMM", ("Horror", 0), ("Not Horror", 0), at=130)], week)
    assert vw.standings_line(empty) == "🗳️ Nobody has voted yet!" and empty["votes"] == 0
    assert vw.standings(polls[:1], week) is None  # nothing posted this week yet


def test_vote_nudges():
    rows = [(7, "{votes} votes so far."), (4, "Only {votes} people have voted!")]
    assert vw.nudge(rows, 3) == "Only 3 people have voted!"
    assert vw.nudge(rows, 5) == "5 votes so far."
    assert vw.nudge(rows, 9) == "" and vw.nudge(rows, None) == ""
