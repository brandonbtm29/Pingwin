"""Noticing movie nights in voice: the window, the tally, and guessing the movie from the polls."""

from datetime import datetime
from zoneinfo import ZoneInfo

from pingwin import voicewatch as vw

ET = ZoneInfo("America/New_York")


def test_watch_window_runs_from_15_minutes_before_to_5_hours_after():
    show = datetime(2026, 10, 19, 20, 30, tzinfo=ET)  # a Monday
    assert vw.current_show("Mon 8:30pm", datetime(2026, 10, 19, 20, 10, tzinfo=ET)) is None
    assert vw.current_show("Mon 8:30pm", datetime(2026, 10, 19, 20, 16, tzinfo=ET)) == show
    assert vw.current_show("Mon 8:30pm", datetime(2026, 10, 20, 1, 20, tzinfo=ET)) == show
    assert vw.current_show("Mon 8:30pm", datetime(2026, 10, 20, 1, 40, tzinfo=ET)) is None
    assert vw.current_show("", datetime(2026, 10, 19, 20, 30, tzinfo=ET)) is None


def test_tally_and_who_stayed():
    t = 1000.0
    s = vw.new_session("2026-10-19", t)
    for minute in range(1, 61):  # an hour: Sam all of it, Alex for the first 20 minutes
        here = {"sam", "alex"} if minute <= 20 else {"sam"}
        vw.sample(s, here, t + minute * 60)
    assert vw.stayed(s, 45) == ["sam"] and vw.stayed(s, 15) == ["alex", "sam"]
    assert s["peak"] == 2 and vw.minutes_together(s) == 59
    vw.sample(s, {"sam"}, t + 60 * 60 + 3600)  # Pingwin was off for an hour: only 2 minutes count
    assert s["seconds"]["sam"] == 60 * 60 + vw.MAX_GAP


def test_night_ends_when_everyone_leaves_after_showtime():
    show = datetime(2026, 10, 19, 20, 30, tzinfo=ET)
    start = show.timestamp() - 600
    s = vw.new_session("2026-10-19", start)
    vw.sample(s, {"sam"}, start + 60)  # someone hops on before showtime, then leaves
    assert not vw.is_over(s, show, start + 60 + 25 * 60)  # empty for 25 min, but it's not 9pm yet
    assert vw.is_over(s, show, show.timestamp() + 3 * 3600)
    assert vw.is_over(s, None, show.timestamp())  # the window closed


def poll(q, *answers, at=0):
    return {"q": q, "answers": list(answers), "at": at}


def test_picks_follow_the_winning_side():
    # Oct 5: Horror won the latest genre poll, and The Invisible Man won the latest horror poll.
    polls = [
        poll("Next MMM??", ("Horror", 2), ("Not Horror", 5), at=1),
        poll("If Horror wins...", ("Halloween", 0), ("Chucky", 2), ("Saw", 0), ("The Invisible Man", 5), at=2),
        poll("What's happening next MMM?", ("Horror", 4), ("Not Horror", 1), at=3),
        poll("If Not Horror wins", ("Soul", 1), ("Hitch", 1), ("Challengers", 0), ("Blade Runner", 3), at=4),
    ]
    picks = [p["title"] for p in vw.poll_picks(polls)]
    assert picks[:2] == ["The Invisible Man", "Blade Runner"]
    assert "Chucky" in picks and "Challengers" not in picks  # runners-up with votes, never zero-vote options
    # Oct 12: Not Horror won, so Blade Runner leads.
    polls.append(poll("Next MMM??", ("Horror", 1), ("Not Horror", 5), at=5))
    assert vw.poll_picks(polls)[0]["title"] == "Blade Runner"


def test_picks_skip_yes_no_polls_and_handle_single_polls():
    polls = [
        poll("Game day this Saturday?", ("Yes", 3), ("No", 1)),
        poll("Space Dinner Movie Event Options", ("Interstellar", 4), ("Moon", 2)),
    ]
    assert [p["title"] for p in vw.poll_picks(polls)] == ["Interstellar", "Moon"]
    assert vw.split_year("Alien (1979)") == ("Alien", 1979) and vw.split_year("Alien") == ("Alien", None)
