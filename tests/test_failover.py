from datetime import UTC, datetime, timedelta

from pingwin.config import FailoverConfig, Node
from pingwin.failover import Lease, parse_lease, render_lease, should_lead

NOW = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)
MAIN = Node("BTM-NOVA")
BACKUP = Node("FRIEND-PC")
CFG = FailoverConfig(True, 1, 30, 120, pcs=("BTM-NOVA", "FRIEND-PC"))


def lease(node: Node) -> Lease:
    return Lease(node.name, NOW)


def lead(held: Lease | None, me: Node, quiet_for: float = 0, warmed_up: bool = True) -> bool:
    """quiet_for: seconds this PC has seen the lease unchanged (its own monotonic clock)."""
    return should_lead(held, me, quiet_for, CFG, warmed_up=warmed_up)


def test_render_parse_roundtrip():
    text = render_lease(MAIN, "main PC", NOW - timedelta(hours=1), NOW)
    assert "**BTM-NOVA** (main PC)" in text
    assert parse_lease(text, NOW) == Lease("BTM-NOVA", NOW)


def test_parse_ignores_other_messages():
    assert parse_lease("hello", NOW) is None
    assert parse_lease("-# lease:{not json}", NOW) is None


def test_first_pc_claims_empty_lease():
    assert lead(None, BACKUP)
    assert lead(None, MAIN, warmed_up=False)  # nobody to wait for: take it at once


def test_holder_keeps_renewing():
    assert lead(lease(BACKUP), BACKUP, 10)
    assert lead(lease(MAIN), MAIN, warmed_up=False)  # restarted: our own lease is still ours


def test_backup_waits_while_main_checks_in():
    assert not lead(lease(MAIN), BACKUP, 30)


def test_backup_takes_over_when_main_goes_quiet():
    assert lead(lease(MAIN), BACKUP, 121)
    assert lead(lease(MAIN), BACKUP, 121, warmed_up=False)  # no one is answering: don't wait


def test_main_takes_back_from_backup_once_warmed_up():
    assert lead(lease(BACKUP), MAIN, 5)
    assert not lead(lease(BACKUP), MAIN, 5, warmed_up=False)  # let SyncThing catch up first
    assert lead(lease(BACKUP), MAIN, 121, warmed_up=False)  # the backup went quiet too


def test_lease_age_ignores_the_pcs_clocks():
    # The wall-clock age of the lease (here "an hour old" by a fast clock) no longer matters, only how long
    # this PC has seen it unchanged.
    old = Lease(MAIN.name, NOW - timedelta(hours=1))
    assert not lead(old, BACKUP, 0)
    assert lead(old, BACKUP, 121)


def test_unlisted_pc_waits_behind_listed_ones():
    extra = Node("SPARE-LAPTOP")
    assert CFG.rank("SPARE-LAPTOP") == 3
    assert not lead(lease(BACKUP), extra, 5)
    assert lead(lease(extra), BACKUP, 5)
    assert lead(lease(BACKUP), extra, 200)  # everyone listed is offline


def test_names_match_case_insensitively():
    assert CFG.rank("btm-nova") == 1
    assert lead(lease(Node("btm-nova")), MAIN, 5)


def test_describe():
    assert CFG.describe("BTM-NOVA") == "main PC"
    assert CFG.describe("FRIEND-PC") == "backup #1"
    assert CFG.describe("OTHER") == "not in the backup list"
