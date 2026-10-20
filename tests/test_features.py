import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from pingwin.config import ConfigError, load_config
from pingwin.configedit import save_section
from pingwin.features import SECTIONS, parse_features, slug

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")


def test_defaults_when_sections_missing():
    f = parse_features({})
    assert f.events.enabled is False and f.events.remind_minutes == 60
    assert f.movienight.pick == "choose" and f.movienight.series == ()
    assert f.bot.timezone == "America/New_York"
    assert not f.needs_members and not f.needs_message_content


def test_intents_follow_features():
    f = parse_features({"modlog": {"enabled": True, "messages": True}})
    assert f.needs_members and f.needs_message_content
    f = parse_features({"welcome": {"enabled": True}})
    assert f.needs_members and not f.needs_message_content


@pytest.mark.parametrize(
    "data, match",
    [
        ({"events": {"remind_minutes": 5000}}, "between"),
        ({"bot": {"timezone": "Mars/Base"}}, "time zone"),
        ({"movienight": {"pick": "vibes"}}, "one of"),
        ({"movienight": {"series": [{"name": "MMM"}, {"name": "mmm"}]}}, "same name"),
    ],
)
def test_bad_values_are_explained(data, match):
    with pytest.raises(ConfigError, match=match):
        parse_features(data)


def test_every_field_has_a_valid_default():
    for section in SECTIONS:
        for field in section["fields"]:
            assert "label" in field and field["type"] in {
                "toggle",
                "channel",
                "role",
                "number",
                "text",
                "textarea",
                "select",
                "secret",
                "rows",
            }


def test_save_section_round_trip(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text((ROOT / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    monkeypatch.delenv("PINGWIN_CONFIG", raising=False)
    save_section(
        "events",
        {
            "enabled": True,
            "channel": "123456789012345678",
            "remind_minutes": "30",
            "rules": [{"role": "Gamer", "keywords": "game, gaming"}, {"role": "", "keywords": ""}],
        },
    )
    save_section("streams", {"TWITCH_CLIENT_ID": "abc", "YOUTUBE_API_KEY": True})  # True = unchanged
    f = load_config(require_token=False).features
    assert f.events.enabled and f.events.channel == 123456789012345678 and f.events.remind_minutes == 30
    assert [(r.role, r.keywords) for r in f.events.rules] == [("Gamer", ("game", "gaming"))]
    assert "# Pingwin settings" in cfg.read_text(encoding="utf-8")  # comments kept
    secrets = (tmp_path / "secrets.env").read_text(encoding="utf-8")
    assert "TWITCH_CLIENT_ID=abc" in secrets and "YOUTUBE_API_KEY" not in secrets
    assert tomllib.loads(cfg.read_text(encoding="utf-8"))["events"]["rules"][0]["keywords"] == ["game", "gaming"]


def test_slug():
    assert slug("Movie Mondays!") == "movie-mondays"


# ---------------------------------------------------------------- feature logic
def test_event_keyword_matching():
    from pingwin.cogs.events import match_roles

    rules = (
        SimpleNamespace(role="Movie Night Maniac", keywords=("mmm", "movie")),
        SimpleNamespace(role="Gamer", keywords=("game",)),
        SimpleNamespace(role="Film Franchise Fanatic", keywords=("fff",)),
    )
    assert match_roles("MMM: Blade Runner", rules) == ["Movie Night Maniac"]
    assert match_roles("Movies and games night", rules) == ["Movie Night Maniac", "Gamer"]
    assert match_roles("Endgame watch party", rules) == []  # 'game' must start a word
    assert match_roles("FFF Resident Evil", rules) == ["Film Franchise Fanatic"]


def test_birthdays():
    from pingwin.cogs.birthdays import is_birthday, upcoming, valid_date

    assert valid_date(2, 29) and not valid_date(4, 31)
    assert is_birthday(10, 19, date(2026, 10, 19))
    assert is_birthday(2, 29, date(2027, 2, 28)) and not is_birthday(2, 29, date(2028, 2, 28))
    people = {"1": {"m": 10, "d": 25}, "2": {"m": 1, "d": 2}, "3": {"m": 10, "d": 19}}
    assert [u for _, u, _, _ in upcoming(people, date(2026, 10, 19))] == ["3", "1", "2"]


def test_movie_poll_helpers():
    from pingwin.cogs.movienight import pick_options, winners

    noms = [
        {"id": "a", "added": "2026-01-02", "watched": None},
        {"id": "b", "added": "2026-01-01", "watched": None},
        {"id": "c", "added": "2026-01-03", "watched": "2026-02-01"},
    ]
    assert [n["id"] for n in pick_options(noms, "oldest", 10)] == ["b", "a"]
    assert len(pick_options(noms, "random", 1)) == 1
    assert winners([("a", 3), ("b", 5)]) == ["b"]
    assert winners([("a", 2), ("b", 2)]) == ["a", "b"]
    assert winners([("a", 0), ("b", 0)]) == []


def test_gameday_toggle():
    from pingwin.cogs.gameday import toggle

    g = {"in": [], "maybe": [], "out": []}
    assert toggle(g, 1, "in") == "in" and g["in"] == [1]
    assert toggle(g, 1, "maybe") == "maybe" and g["in"] == [] and g["maybe"] == [1]
    assert toggle(g, 1, "maybe") == "" and g["maybe"] == []


def test_stream_alert_text():
    from pingwin.cogs.streams import format_alert

    live = [
        {"platform": "Twitch", "title": "FF7 part 3", "game": "Final Fantasy VII", "url": "https://twitch.tv/x"},
        {"platform": "YouTube", "title": "", "game": "", "url": "https://youtu.be/y"},
    ]
    text = format_alert("🔴 **{name}** is live! {title}\n{links}", "Sam Streams", live)
    assert "Sam Streams" in text and "FF7 part 3" in text and "Twitch: https://twitch.tv/x" in text
    assert "YouTube: https://youtu.be/y" in text


# ---------------------------------------------------------------- time parsing
NOW = datetime(2026, 10, 19, 13, 0, tzinfo=NY)  # a Monday


@pytest.mark.parametrize(
    "text, expected",
    [
        ("in 2 hours", NOW + timedelta(hours=2)),
        ("in 30 min", NOW + timedelta(minutes=30)),
        ("8pm", datetime(2026, 10, 19, 20, 0, tzinfo=NY)),
        ("tonight", datetime(2026, 10, 19, 20, 0, tzinfo=NY)),
        ("tomorrow 7:30pm", datetime(2026, 10, 20, 19, 30, tzinfo=NY)),
        ("fri 8pm", datetime(2026, 10, 23, 20, 0, tzinfo=NY)),
        ("friday 20:00", datetime(2026, 10, 23, 20, 0, tzinfo=NY)),
        ("mon 9am", datetime(2026, 10, 26, 9, 0, tzinfo=NY)),  # this Monday's 9am has passed
        ("10/24 8pm", datetime(2026, 10, 24, 20, 0, tzinfo=NY)),
        ("2026-11-01 18:00", datetime(2026, 11, 1, 18, 0, tzinfo=NY)),
        ("noon", datetime(2026, 10, 20, 12, 0, tzinfo=NY)),  # today's noon has passed
    ],
)
def test_parse_when(text, expected):
    from pingwin.timeparse import parse_when

    assert parse_when(text, NOW) == expected


@pytest.mark.parametrize("text", ["whenever", "monster 8pm", "fri", "13/45 8pm", "25pm"])
def test_parse_when_rejects(text):
    from pingwin.timeparse import parse_when

    assert parse_when(text, NOW) is None


def test_zone_names_and_abbreviations():
    from pingwin import zones

    assert zones.zone("Mountain").key == "America/Denver"
    assert str(zones.zone("ct")) == "America/Chicago"
    assert zones.zone("America/New_York").key == "America/New_York"
    assert zones.zone("Nowhere/Land") is None
    assert zones.split_zone("fri 8pm MT") == ("fri 8pm", ZoneInfo("America/Denver"))
    assert zones.split_zone("fri 8pm") == ("fri 8pm", None)
    assert zones.split_zone("8") == ("8", None)
    assert zones.label(ZoneInfo("America/Chicago")) == "Central"
    assert zones.search("")[0] == ("Eastern", "America/New_York")
    assert ("America/Boise", "America/Boise") in zones.search("boise")


def test_member_zones_are_saved(tmp_path, monkeypatch):
    from pingwin import zones

    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    eastern = ZoneInfo("America/New_York")
    assert zones.for_member(42, eastern) is eastern
    zones.set_for(42, "America/Denver")
    assert zones.for_member(42, eastern).key == "America/Denver"
    zones.set_for(42, None)
    assert zones.get(42) is None


def test_typed_times_use_the_members_zone():
    from pingwin import zones
    from pingwin.timeparse import parse_when

    now = datetime(2026, 10, 19, 12, 0, tzinfo=ZoneInfo("UTC"))
    text, tz = zones.split_zone("tomorrow 8pm MT")
    start = parse_when(text, now.astimezone(tz))
    eastern = parse_when("tomorrow 8pm", now.astimezone(ZoneInfo("America/New_York")))
    assert start - eastern == timedelta(hours=2)


def test_birthdays_go_out_in_each_members_morning():
    from pingwin.cogs.birthdays import due_now

    eastern = ZoneInfo("America/New_York")
    people = {"1": {"m": 10, "d": 20}, "2": {"m": 10, "d": 20}}
    saved = {"2": "America/Denver"}
    at = datetime(2026, 10, 20, 9, 30, tzinfo=eastern)  # 9:30 ET = 7:30 MT
    assert due_now(people, {}, saved, eastern, 9, at) == [("1", "2026-10-20")]
    later = datetime(2026, 10, 20, 11, 5, tzinfo=eastern)  # 9:05 MT
    assert due_now(people, {"1": "2026-10-20"}, saved, eastern, 9, later) == [("2", "2026-10-20")]
    assert due_now(people, {"1": "2026-10-20", "2": "2026-10-20"}, saved, eastern, 9, later) == []
