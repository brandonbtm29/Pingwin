from datetime import UTC, datetime, timedelta

from pingwin.config import FailoverConfig, Node
from pingwin.failover import (
    FIND_PEERS,
    SYNC_SETTLE,
    WARMUP_SECONDS,
    Lease,
    files_caught_up,
    parse_lease,
    render_lease,
    should_lead,
)
from pingwin.syncthing import FolderSync

NOW = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)
MAIN = Node("BTM-NOVA")
BACKUP = Node("FRIEND-PC")
CFG = FailoverConfig(True, 1, 30, 120, pcs=("BTM-NOVA", "FRIEND-PC"))


def lease(node: Node) -> Lease:
    return Lease(node.name, NOW)


def lead(held: Lease | None, me: Node, quiet_for: float = 0, caught_up: bool = True) -> bool:
    """quiet_for: seconds this PC has seen the lease unchanged (its own monotonic clock)."""
    return should_lead(held, me, quiet_for, CFG, caught_up=caught_up)


def test_render_parse_roundtrip():
    text = render_lease(MAIN, "main PC", NOW - timedelta(hours=1), NOW)
    assert "**BTM-NOVA** (main PC)" in text
    assert parse_lease(text, NOW) == Lease("BTM-NOVA", NOW)


def test_parse_ignores_other_messages():
    assert parse_lease("hello", NOW) is None
    assert parse_lease("-# lease:{not json}", NOW) is None


def test_first_pc_claims_empty_lease():
    assert lead(None, BACKUP)
    assert lead(None, MAIN, caught_up=False)  # nobody to wait for: take it at once


def test_holder_keeps_renewing():
    assert lead(lease(BACKUP), BACKUP, 10)
    assert lead(lease(MAIN), MAIN, caught_up=False)  # restarted: our own lease is still ours


def test_backup_waits_while_main_checks_in():
    assert not lead(lease(MAIN), BACKUP, 30)


def test_backup_takes_over_when_main_goes_quiet():
    assert lead(lease(MAIN), BACKUP, 121)
    assert lead(lease(MAIN), BACKUP, 121, caught_up=False)  # no one is answering: don't wait


def test_main_takes_back_from_backup_once_caught_up():
    assert lead(lease(BACKUP), MAIN, 5)
    assert not lead(lease(BACKUP), MAIN, 5, caught_up=False)  # let SyncThing catch up first
    assert lead(lease(BACKUP), MAIN, 121, caught_up=False)  # the backup went quiet too


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


def synced(idle=True, uptime=3600.0, peers=(600.0,), waiting=""):
    return FolderSync(idle, waiting, uptime, tuple(peers))


def test_caught_up_as_soon_as_syncthing_has_nothing_left():
    assert files_caught_up(synced(), 1, False) == (True, "")  # connected to the backup, nothing to download
    ready, why = files_caught_up(synced(idle=False, waiting="SyncThing has 3 files to bring over"), 1, False)
    assert not ready and why == "SyncThing has 3 files to bring over"
    assert files_caught_up(synced(idle=False), WARMUP_SECONDS, False)[0]  # never longer than the old wait


def test_a_fresh_connection_gets_time_to_swap_file_lists():
    ready, why = files_caught_up(synced(peers=(SYNC_SETTLE - 1,)), 1, False)
    assert not ready and "just connected" in why
    assert files_caught_up(synced(peers=(SYNC_SETTLE,)), 1, False)[0]


def test_no_other_pc_connected():
    # SyncThing has had time to find the other PCs and none is there (the backup is off): nothing to wait for.
    assert files_caught_up(synced(peers=(), uptime=FIND_PEERS), 1, False)[0]
    ready, why = files_caught_up(synced(peers=(), uptime=FIND_PEERS - 1), 1, False)  # SyncThing just started
    assert not ready and "looking for the other PCs" in why


def test_after_a_gap_only_new_connections_count():
    # Back from sleep: a connection from before then may be dead, so wait for one made since (or the search time).
    ready, _ = files_caught_up(synced(peers=(900.0,)), 20, True)
    assert not ready
    assert files_caught_up(synced(peers=(900.0, 15.0)), 20, True)[0]
    assert files_caught_up(synced(peers=(900.0,)), FIND_PEERS, True)[0]


def test_without_syncthing_wait_the_old_way():
    ready, why = files_caught_up(None, WARMUP_SECONDS - 1, False)
    assert not ready and "3 minutes" in why
    assert files_caught_up(None, WARMUP_SECONDS, False)[0]
