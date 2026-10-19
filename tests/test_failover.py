from datetime import UTC, datetime, timedelta

from pingwin.config import FailoverConfig, Node
from pingwin.failover import Lease, parse_lease, render_lease, should_lead

NOW = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)
MAIN = Node("BTM-NOVA")
BACKUP = Node("FRIEND-PC")
CFG = FailoverConfig(True, 1, 30, 120, pcs=("BTM-NOVA", "FRIEND-PC"))


def lease(node: Node, seconds_ago: float) -> Lease:
    return Lease(node.name, NOW - timedelta(seconds=seconds_ago))


def test_render_parse_roundtrip():
    text = render_lease(MAIN, "main PC", NOW - timedelta(hours=1), NOW)
    assert "**BTM-NOVA** (main PC)" in text
    assert parse_lease(text, NOW) == Lease("BTM-NOVA", NOW)


def test_parse_ignores_other_messages():
    assert parse_lease("hello", NOW) is None
    assert parse_lease("-# lease:{not json}", NOW) is None


def test_first_pc_claims_empty_lease():
    assert should_lead(None, BACKUP, NOW, CFG)


def test_holder_keeps_renewing():
    assert should_lead(lease(BACKUP, 10), BACKUP, NOW, CFG)


def test_backup_waits_while_main_checks_in():
    assert not should_lead(lease(MAIN, 30), BACKUP, NOW, CFG)


def test_backup_takes_over_when_main_goes_quiet():
    assert should_lead(lease(MAIN, 121), BACKUP, NOW, CFG)


def test_main_takes_back_from_backup_immediately():
    assert should_lead(lease(BACKUP, 5), MAIN, NOW, CFG)


def test_unlisted_pc_waits_behind_listed_ones():
    extra = Node("SPARE-LAPTOP")
    assert CFG.rank("SPARE-LAPTOP") == 3
    assert not should_lead(lease(BACKUP, 5), extra, NOW, CFG)
    assert should_lead(lease(extra, 5), BACKUP, NOW, CFG)
    assert should_lead(lease(BACKUP, 200), extra, NOW, CFG)  # everyone listed is offline


def test_names_match_case_insensitively():
    assert CFG.rank("btm-nova") == 1
    assert should_lead(lease(Node("btm-nova"), 5), MAIN, NOW, CFG)


def test_describe():
    assert CFG.describe("BTM-NOVA") == "main PC"
    assert CFG.describe("FRIEND-PC") == "backup #1"
    assert CFG.describe("OTHER") == "not in the backup list"
