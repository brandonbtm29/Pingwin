from pathlib import Path

import pytest

from pingwin.config import ConfigError
from pingwin.persona import Persona, load_persona

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["pingwin", "deadpan"])
def test_bundled_personas_load(name):
    p = load_persona(name, ROOT / "personas")
    for key in ("role_added", "role_removed", "not_allowed", "about"):
        assert "{" not in p.say(key, role="Gamer", user="Sam", required="Friend")


def test_placeholders_and_fallbacks():
    p = Persona(name="Pingwin", messages={"role_added": ["Hi {user}, {role}! {unknown}"]})
    assert p.say("role_added", user="Sam", role="Gamer") == "Hi Sam, Gamer! {unknown}"
    assert p.say("role_removed", role="Gamer") == "Removed you from **Gamer**."  # default
    assert p.say("nonexistent") == "nonexistent"


def test_broken_template_does_not_crash():
    p = Persona(name="Pingwin", messages={"about": ["oops {"]})
    assert p.say("about") == "oops {"


def test_persona_name_is_validated(tmp_path):
    with pytest.raises(ConfigError, match="letters"):
        load_persona("../secrets", tmp_path)


def test_bad_message_type(tmp_path):
    (tmp_path / "x.toml").write_text("[messages]\nabout = 5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="messages.about"):
        load_persona("x", tmp_path)


def test_shared_folder_persona_wins(monkeypatch, tmp_path):
    (tmp_path / "personas").mkdir()
    (tmp_path / "personas" / "pingwin.toml").write_text('name = "Custom"\n', encoding="utf-8")
    monkeypatch.setenv("PINGWIN_DATA", str(tmp_path))
    assert load_persona("pingwin").name == "Custom"
    assert load_persona("deadpan").name == "Pingwin"  # falls back to the bundled one
