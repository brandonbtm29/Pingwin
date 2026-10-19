from dataclasses import replace

from pingwin import invite_url
from pingwin.dashboard.setup_check import Facts, checklist, summarize

FRESH = Facts(
    connected=True,
    guild_name=None,
    invite=invite_url(123),
    guild_id_set=False,
    can_manage_roles=False,
    menu_count=4,
    missing_roles=["Gamer", "Stream Stan"],
    problem_roles=[],
    gate_role="Friend",
    gate_exists=False,
    menus_posted=0,
    failover_enabled=False,
    failover_ready=False,
    auto_updates=False,
    autostart=False,
)

DONE_ALL = replace(
    FRESH,
    guild_name="Clubhouse",
    guild_id_set=True,
    can_manage_roles=True,
    missing_roles=[],
    gate_exists=True,
    menus_posted=1,
    auto_updates=True,
    autostart=True,
)


def by_id(facts):
    return {s.id: s for s in checklist(facts)}


def test_fresh_install_shows_what_is_left():
    steps = by_id(FRESH)
    assert steps["connect"].status == "done"
    assert steps["invite"].status == "todo" and "client_id=123" in steps["invite"].link
    assert steps["gate"].status == "todo"
    in_server = by_id(replace(FRESH, guild_name="Clubhouse"))
    assert in_server["roles_ready"].status == "todo" and "Gamer" in in_server["roles_ready"].detail
    assert steps["guild_id"].status == "optional" and not steps["guild_id"].required
    summary = summarize(checklist(FRESH))
    assert summary["required_done"] < summary["required_total"]


def test_everything_done():
    summary = summarize(checklist(DONE_ALL))
    assert summary["required_done"] == summary["required_total"]
    assert all(s["status"] in ("done", "optional") for s in summary["steps"])


def test_role_problems_are_warnings():
    steps = by_id(replace(DONE_ALL, problem_roles=["Gamer"]))
    assert steps["roles_ready"].status == "warn" and "Gamer" in steps["roles_ready"].detail


def test_no_gate_role_is_optional():
    assert by_id(replace(DONE_ALL, gate_role=None))["gate"].status == "optional"


def test_failover_half_configured_warns():
    assert by_id(replace(DONE_ALL, failover_enabled=True))["failover"].status == "warn"
    assert by_id(replace(DONE_ALL, failover_enabled=True, failover_ready=True))["failover"].status == "done"


def test_autostart_hidden_off_windows():
    assert "autostart" not in by_id(replace(DONE_ALL, autostart=None))


def test_role_steps_wait_for_a_server():
    steps = by_id(replace(FRESH, missing_roles=[]))
    assert steps["roles_ready"].status == "todo" and "in your server" in steps["roles_ready"].detail
    assert "in your server" in steps["gate"].detail
